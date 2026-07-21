"""담당: 이서영

진짜 충돌에 대해 병합 후보 N개를 생성한다.

값 충돌(base 대비 ours/theirs가 줄 수는 그대로고 같은 줄만 다른 값으로 고친
경우)은 LLM을 부르지 않고 ours/theirs 원문 그대로를 후보로 낸다.

구조적 충돌은 `git merge-file --diff3`로 먼저 텍스트 3-way 병합을 시도한다.
겹치지 않는 부분은 git이 이미 자동으로 합쳐주므로, LLM은 진짜로 충돌한
훙크(hunk)만 보고 그 부분만 다시 쓴다 — 파일 전체를 LLM이 통째로 재생성하면
큰 파일에서 출력 토큰이 폭발해 느려지는 문제를 이렇게 피한다. 훙크가 여러
개(파일 안에서 서로 멀리 떨어진 충돌)여도 각각 독립적으로 처리하고, 훙크별
후보 조합을 그대로 이어붙여 파일 전체를 재조립한다 — MergeCandidate.content
계약(파일 전체)은 그대로 지킨다.

diff3가 아예 충돌 없이 완전히 합쳐버리는 경우(mergiraf의 구조적 판단과 git의
줄 단위 판단이 다를 수 있음)도 LLM 없이 그 결과를 그대로 반환한다.

후보 간 다양성은 병렬 호출마다 다른 temperature를 줘서 유도한다 — 이전엔
"이전 후보를 보여주고 다르게 만들라"는 순차 방식이었는데, 훙크 단위 병렬
생성과는 안 맞아서(각 호출이 서로를 기다리면 병렬화 의미가 없음) 버렸다.

Gemini API로 실제 호출한다 (테스트/평가용 — 최종 프로바이더는 로드맵상 TBD).
GEMINI_API_KEY는 .env에서 읽는다. 프로바이더 교체 대비를 위해 실제 API 호출은
_call_llm 한 곳에만 몰아뒀다 — 나중에 다른 프로바이더로 바꿀 땐 이 함수만 갈아
끼우면 된다.

_call_llm은 (model, temperature, prompt) 해시를 키로 `.weld_cache/llm_responses.json`에
응답을 디스크 캐싱한다(verify/callgraph.py의 content-hash 캐시와 같은 패턴).
같은 훙크가 재평가·재시도·재등장으로 다시 들어오면 API를 다시 안 부른다 —
무료 티어 요청 수 쿼터(하루 20건)를 지키기 위한 조치. thinking_budget=0과는
독립적인 절약 축(그건 토큰 비용, 이건 요청 수)이라 서로 보완적이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from weld.langs import detect_language
from weld.types import MergeCandidate

load_dotenv()

_DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

_LLM_CACHE_VERSION = 1
_LLM_CACHE_LOCK = threading.Lock()

# 후보 개수만큼 앞에서부터 잘라 쓴다. 온도를 낮은 값부터 배치해 첫 후보(c-0)는
# 비교적 보수적이고, 뒤로 갈수록 다른 해석을 더 적극적으로 탐색하게 한다.
_CANDIDATE_TEMPERATURES = [0.2, 0.7, 1.1, 1.4, 1.7]

_HUNK_PROMPT_TEMPLATE = """다음은 git 3-way 병합에서 실제로 충돌한 코드 조각(훙크) \
하나다. base는 공통 조상, ours와 theirs는 각각 여기서 갈라져 나온 두 버전이다. \
파일의 나머지 부분은 이미 자동으로 병합됐고, 이 조각만 사람(너)이 판단해야 한다.
{language_line}

--- base (이 조각의 공통 조상) ---
{base}
--- ours (이 조각의 우리 쪽 버전) ---
{ours}
--- theirs (이 조각의 상대 쪽 버전) ---
{theirs}
지시사항: base 대비 ours와 theirs 각각이 이 조각에서 실제로 무엇을 바꾸려 \
했는지 판단하고, 문법적으로 유효한 하나의 결과로 통합하라.

엄격한 규칙:
- 이 조각을 대체할 코드만 출력한다. 파일의 다른 부분은 다시 쓰지 않는다.
- 설명, 마크다운 코드블록 표시(```), 충돌 마커(<<<<<<<, |||||||, =======,
  >>>>>>>)를 포함하지 않는다.
- 원본 조각과 들여쓰기/줄바꿈 스타일을 맞춘다 — 이 출력은 그대로 파일에
  이어붙여지므로, 앞뒤 줄과 어긋나면 파일이 깨진다.
"""

_CONFLICT_BLOCK_RE = re.compile(
    r"^<<<<<<< [^\n]*\n"
    r"(?P<ours>.*?)"
    r"^\|\|\|\|\|\|\| [^\n]*\n"
    r"(?P<base>.*?)"
    r"^=======\n"
    r"(?P<theirs>.*?)"
    r"^>>>>>>> [^\n]*\n",
    re.MULTILINE | re.DOTALL,
)

_ConflictHunk = tuple[str, str, str]  # (ours, base, theirs)
_Segment = str | _ConflictHunk


def _build_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY가 설정되지 않았다. 저장소 루트의 .env 파일에 "
            "GEMINI_API_KEY=... 를 추가하라."
        )
    return genai.Client(api_key=api_key)


def _llm_cache_path() -> Path:
    return Path(".weld_cache") / "llm_responses.json"


def _load_llm_cache() -> dict[str, str]:
    try:
        data = json.loads(_llm_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _LLM_CACHE_VERSION:
        return {}
    responses = data.get("responses")
    return dict(responses) if isinstance(responses, dict) else {}


def _save_llm_cache(responses: dict[str, str]) -> None:
    path = _llm_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": _LLM_CACHE_VERSION, "responses": responses}),
            encoding="utf-8",
        )
    except OSError:
        pass  # 캐시 저장 실패해도 기능엔 영향 없음 — 다음 호출이 그냥 다시 API를 부른다.


def _cache_key(model: str, temperature: float, prompt: str) -> str:
    return hashlib.sha256(f"{model}|{temperature}|{prompt}".encode("utf-8")).hexdigest()


def _call_llm(client: genai.Client, prompt: str, temperature: float = 0.7) -> str:
    """캐시 히트면 API를 아예 안 부른다. 훙크 단위 병렬 호출(ThreadPoolExecutor)에서
    여러 스레드가 동시에 들어오므로, 디스크 read-modify-write 구간만 락으로 감싸고
    실제 네트워크 호출은 락 밖에서 해 병렬성을 유지한다."""
    key = _cache_key(_DEFAULT_MODEL, temperature, prompt)

    with _LLM_CACHE_LOCK:
        cached = _load_llm_cache().get(key)
    if cached is not None:
        return cached

    response = client.models.generate_content(
        model=_DEFAULT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=temperature,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    text = (response.text or "").strip()

    with _LLM_CACHE_LOCK:
        cache = _load_llm_cache()
        cache[key] = text
        _save_llm_cache(cache)
    return text


def is_value_conflict(base: str, ours: str, theirs: str) -> bool:
    """base 대비 ours/theirs가 정확히 같은 줄(들)만 다른 값으로 고쳤는지 판별한다.

    줄 수가 바뀌었거나(추가/삭제), ours/theirs가 서로 다른 줄을 건드렸다면
    실제 통합이 필요한 구조적 충돌로 보고 False를 반환한다.
    """
    base_lines = base.splitlines()
    ours_lines = ours.splitlines()
    theirs_lines = theirs.splitlines()

    if len(base_lines) != len(ours_lines) or len(base_lines) != len(theirs_lines):
        return False

    changed_by_ours = {i for i, (b, o) in enumerate(zip(base_lines, ours_lines)) if b != o}
    changed_by_theirs = {i for i, (b, t) in enumerate(zip(base_lines, theirs_lines)) if b != t}

    if not changed_by_ours or not changed_by_theirs:
        return False

    return changed_by_ours == changed_by_theirs


def _diff3_merge(base: str, ours: str, theirs: str) -> str:
    """git merge-file --diff3로 텍스트 3-way 병합을 시도한 결과 텍스트를 반환한다.

    겹치지 않는 변경은 이미 합쳐진 채로, 진짜 충돌한 부분만 <<<<<<< / ||||||| /
    ======= / >>>>>>> 마커로 표시돼 돌아온다 (git merge-file은 충돌이 있어도
    비-0 exit code를 낼 뿐 -p로 준 stdout 결과 자체는 항상 유효하다).
    """
    with tempfile.TemporaryDirectory(prefix="weld-diff3-") as tmp:
        tmp_path = Path(tmp)
        ours_f = tmp_path / "ours"
        base_f = tmp_path / "base"
        theirs_f = tmp_path / "theirs"
        ours_f.write_text(ours)
        base_f.write_text(base)
        theirs_f.write_text(theirs)
        result = subprocess.run(
            ["git", "merge-file", "--diff3", "-p", str(ours_f), str(base_f), str(theirs_f)],
            capture_output=True,
            text=True,
        )
        return result.stdout


def _split_diff3_segments(diff3_text: str) -> list[_Segment]:
    """diff3 결과를 (합쳐진 일반 텍스트) / (충돌 훙크) 세그먼트로 순서대로 쪼갠다."""
    segments: list[_Segment] = []
    pos = 0
    for match in _CONFLICT_BLOCK_RE.finditer(diff3_text):
        if match.start() > pos:
            segments.append(diff3_text[pos : match.start()])
        segments.append((match.group("ours"), match.group("base"), match.group("theirs")))
        pos = match.end()
    if pos < len(diff3_text):
        segments.append(diff3_text[pos:])
    return segments


def _normalize_hunk_output(resolved: str, hunk: _ConflictHunk) -> str:
    """LLM이 훙크 시작 들여쓰기/끝 개행을 놓치는 경우를 보정한다.

    훙크는 파일 중간(예: 함수 시그니처 바로 다음)에 그대로 이어붙여지는데,
    첫 줄 앞 문맥(예: "def greet(name):")이 이 훙크만 보는 프롬프트엔 없어서
    LLM이 첫 줄 들여쓰기를 종종 빼먹는 걸 실측으로 확인했다(IndentationError로
    이어짐). base/ours/theirs 중 하나의 첫 줄 들여쓰기를 정답으로 삼아 강제
    맞추고, 원본이 개행으로 끝나면 결과도 개행으로 끝나게 한다.
    """
    ours_hunk, base_hunk, theirs_hunk = hunk
    reference = base_hunk or ours_hunk or theirs_hunk
    if not reference or not resolved:
        return resolved

    ref_first_line = reference.splitlines()[0]
    expected_indent = ref_first_line[: len(ref_first_line) - len(ref_first_line.lstrip(" \t"))]

    lines = resolved.splitlines(keepends=True)
    if lines and expected_indent:
        first_line = lines[0]
        newline = first_line[len(first_line.rstrip("\n")) :]
        lines[0] = expected_indent + first_line.rstrip("\n").lstrip(" \t") + newline
    resolved = "".join(lines)

    if reference.endswith("\n") and not resolved.endswith("\n"):
        resolved += "\n"

    return resolved


def _language_line(file_path: str | None) -> str:
    """훙크 프롬프트에 끼워 넣을 언어 힌트 한 줄. 모르는 확장자/경로 없으면 빈 문자열."""
    if not file_path:
        return ""
    spec = detect_language(file_path)
    if spec is None:
        return ""
    return f"이 코드는 {spec.name} 언어다. 문법과 관용구를 이 언어 기준으로 판단하라.\n"


def _resolve_hunk(
    client: genai.Client, hunk: _ConflictHunk, temperature: float, language_line: str = ""
) -> str:
    ours_hunk, base_hunk, theirs_hunk = hunk
    prompt = _HUNK_PROMPT_TEMPLATE.format(
        base=base_hunk, ours=ours_hunk, theirs=theirs_hunk, language_line=language_line
    )
    resolved = _call_llm(client, prompt, temperature=temperature)
    return _normalize_hunk_output(resolved, hunk)


def generate_candidates(
    base: str, ours: str, theirs: str, n: int = 3, file_path: str | None = None
) -> list[MergeCandidate]:
    """3-way 충돌에 대해 후보 n개를 생성한다.

    file_path가 주어지면 확장자로 언어를 판별해(langs.detect_language) LLM
    프롬프트에 알려준다 — 안 주면 LLM이 코드 내용만 보고 언어를 추측해야 한다.
    """
    if is_value_conflict(base, ours, theirs):
        candidates = [
            MergeCandidate(id="c-0", content=ours, strategy="ours-verbatim"),
            MergeCandidate(id="c-1", content=theirs, strategy="theirs-verbatim"),
        ]
        return candidates[:n]

    segments = _split_diff3_segments(_diff3_merge(base, ours, theirs))
    conflict_positions = [i for i, seg in enumerate(segments) if isinstance(seg, tuple)]

    if not conflict_positions:
        # git의 텍스트 3-way 병합만으로 이미 완전히 합쳐졌다 — LLM 호출 불필요.
        merged = "".join(segments)  # type: ignore[arg-type]
        return [MergeCandidate(id="c-0", content=merged, strategy="diff3-verbatim")][: max(n, 1)]

    client = _build_client()
    temperatures = _CANDIDATE_TEMPERATURES[:n]
    language_line = _language_line(file_path)

    with ThreadPoolExecutor(max_workers=max(1, len(conflict_positions) * len(temperatures))) as pool:
        futures = {
            (pos, cand_i): pool.submit(
                _resolve_hunk, client, segments[pos], temperatures[cand_i], language_line  # type: ignore[arg-type]
            )
            for pos in conflict_positions
            for cand_i in range(len(temperatures))
        }
        resolutions = {key: future.result() for key, future in futures.items()}

    candidates = []
    for cand_i, temperature in enumerate(temperatures):
        parts = [
            resolutions[(pos, cand_i)] if pos in conflict_positions else seg
            for pos, seg in enumerate(segments)
        ]
        content = "".join(parts)
        candidates.append(
            MergeCandidate(id=f"c-{cand_i}", content=content, strategy=f"llm-hunk-t{temperature}")
        )
    return candidates
