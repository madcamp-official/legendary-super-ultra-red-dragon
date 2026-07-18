"""담당: 나

진짜 충돌에 대해 병합 후보 N개를 생성한다.

값 충돌(base 대비 ours/theirs가 줄 수는 그대로고 같은 줄만 다른 값으로 고친
경우)은 LLM을 부르지 않고 ours/theirs 원문 그대로를 후보로 낸다 — 어차피
"우선순위"라는 지시는 결국 "그 값을 써라"와 같고, LLM에게 물렁하게 시키면
두 전략이 같은 답으로 수렴해버리는 문제가 실측으로 확인됐다. 구조적 충돌
(줄 추가/삭제 등 실제 통합이 필요한 경우)만 LLM으로 synthesis한다.

Gemini API로 실제 호출한다 (테스트/평가용 — 최종 프로바이더는 로드맵상 TBD).
GEMINI_API_KEY는 .env에서 읽는다. 프로바이더 교체 대비를 위해 실제 API 호출은
_call_llm 한 곳에만 몰아뒀다 — 나중에 다른 프로바이더로 바꿀 땐 이 함수만 갈아
끼우면 된다.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai

from weld.types import MergeCandidate

load_dotenv()

_DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

_BASE_PROMPT_TEMPLATE = """다음은 git 3-way 병합 충돌이다. base는 공통 조상, ours와 \
theirs는 각각 여기서 갈라져 나온 두 버전이다.

--- base ---
{base}

--- ours ---
{ours}

--- theirs ---
{theirs}

base 대비 ours와 theirs 각각이 실제로 무엇을 바꾸려 했는지 판단하고, 문법적으로
유효한 하나의 결과로 통합하라.

규칙:
- 병합된 최종 코드만 출력한다. 설명, 마크다운 코드블록 표시(```), 충돌 마커
  (<<<<<<<, =======, >>>>>>>)를 포함하지 않는다.
"""

_ALTERNATIVE_SUFFIX = """
이 충돌에 대해 이미 다음과 같은 병합안(들)이 나와 있다:

{previous_block}

이들과 논리적으로 또는 구조적으로 의미 있게 다른 대안이 있다면 그것을 반환하라
(예: 계산 순서가 다르거나, 조건 판단 기준이 다르거나, 예외 처리 방식이 다른
경우). 코드 위치만 바꾸는 등 실질적 차이가 없는 변형은 만들지 마라. 이 충돌에
합리적인 대안이 없다면, 위 병합안 중 하나와 동일한 코드를 그대로 반환해도 된다.
"""


def _build_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY가 설정되지 않았다. 저장소 루트의 .env 파일에 "
            "GEMINI_API_KEY=... 를 추가하라."
        )
    return genai.Client(api_key=api_key)


def _call_llm(client: genai.Client, prompt: str) -> str:
    response = client.models.generate_content(model=_DEFAULT_MODEL, contents=prompt)
    return (response.text or "").strip()


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


def generate_candidates(base: str, ours: str, theirs: str, n: int = 3) -> list[MergeCandidate]:
    """3-way 충돌에 대해 서로 다른 전략으로 후보 n개를 생성한다."""
    if is_value_conflict(base, ours, theirs):
        candidates = [
            MergeCandidate(id="c-0", content=ours, strategy="ours-verbatim"),
            MergeCandidate(id="c-1", content=theirs, strategy="theirs-verbatim"),
        ]
        return candidates[:n]

    client = _build_client()
    candidates = []
    previous_contents: list[str] = []
    for i in range(n):
        base_prompt = _BASE_PROMPT_TEMPLATE.format(base=base, ours=ours, theirs=theirs)
        if previous_contents:
            previous_block = "\n\n".join(
                f"--- 기존 병합안 {idx + 1} ---\n{content}"
                for idx, content in enumerate(previous_contents)
            )
            prompt = base_prompt + _ALTERNATIVE_SUFFIX.format(previous_block=previous_block)
        else:
            prompt = base_prompt

        content = _call_llm(client, prompt)
        previous_contents.append(content)
        strategy = "llm-primary" if i == 0 else f"llm-alternative-{i}"
        candidates.append(MergeCandidate(id=f"c-{i}", content=content, strategy=strategy))
    return candidates
