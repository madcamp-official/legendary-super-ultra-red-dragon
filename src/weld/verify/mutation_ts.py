"""담당: 김민재

tree-sitter 기반 다국어 뮤테이션 엔진 (비Python 언어용).

Python 전용 ast 엔진(verify/mutation.py)과 같은 철학 — "이 줄이 실행됐나"가
아니라 "이 줄에 결함을 주입해도 테스트가 잡아내나" — 을 언어 무관하게
구현한다. 핵심 아이디어는 **텍스트 스플라이스**: tree-sitter가 주는 정확한
바이트 오프셋으로 연산자/리터럴 토큰만 제자리 치환하므로, AST를 unparse할
필요가 없어 문법별 프린터를 언어마다 새로 짤 필요가 없다.

오퍼레이터 (토큰 타입 = 토큰 텍스트인 tree-sitter 특성 활용):
  - 비교 반전: < → >=, == → !=, === → !== 등
  - 논리 반전: && ↔ ||
  - 산술 반전: + ↔ -, * ↔ /
  - 불리언 반전: true ↔ false
  - 문자열 → 빈 문자열, 숫자 0 → -1

Python ast 엔진과의 의도적 차이:
  - 판정 기준: 언어별 테스트 명령(langs.LanguageSpec.test_command) 전체 실행의
    exit code. 줄 단위 커버리지 확인이 없으므로, 테스트가 안 지나가는 줄의
    뮤턴트는 '생존'으로 집계된다 → 점수가 낮아져 에스컬레이션되는 보수적
    방향의 편향이다 (mutants_uncovered는 항상 0으로 남는다).
  - 문장 삭제(SBR)는 뺐다 — 세미콜론/블록 규칙이 언어마다 달라 텍스트
    스플라이스로 안전하게 지우기 어렵고, 나머지 오퍼레이터만으로도 변경
    영역 검증 신호는 충분히 나온다.

fail-safe: tree-sitter 미설치, 미지원 언어, 테스트 명령 없음, baseline 실패 —
전부 "신호 없음"(MutationScore 0/0)을 반환하고 판정은 policy.trust의
mutants_total==0 분기가 맡는다.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from weld.langs import LanguageSpec, detect_language
from weld.types import MergeCandidate, MutationScore
from weld.verify.mutation import (
    _MIN_SAMPLES_FOR_EARLY_STOP,
    _TEST_TIMEOUT_S,
    _changed_line_numbers,
    _wilson_interval,
)

# 토큰 텍스트 → 치환 텍스트. tree-sitter는 연산자 토큰의 node.type이 토큰
# 텍스트 그대로라(예: "<", "&&") 언어가 달라도 같은 표에서 대부분 동작한다.
_TOKEN_FLIPS: dict[str, str] = {
    "<": ">=",
    ">": "<=",
    "<=": ">",
    ">=": "<",
    "==": "!=",
    "!=": "==",
    "===": "!==",
    "!==": "===",
    "&&": "||",
    "||": "&&",
    "+": "-",
    "-": "+",
    "*": "/",
    "/": "*",
}
_OPERATOR_KINDS = {
    "<": "comparison_flip", ">": "comparison_flip", "<=": "comparison_flip",
    ">=": "comparison_flip", "==": "comparison_flip", "!=": "comparison_flip",
    "===": "comparison_flip", "!==": "comparison_flip",
    "&&": "logical_flip", "||": "logical_flip",
    "+": "arithmetic_flip", "-": "arithmetic_flip",
    "*": "arithmetic_flip", "/": "arithmetic_flip",
}
_STRING_NODE_TYPES = {"string", "template_string", "string_literal"}

# 뮤테이션 대상에서 제외할 부모 노드 타입.
# - import 계열: 경로 문자열을 비우면 테스트가 코드 결함이 아니라 로드
#   실패로 죽어 신호가 오염된다.
# - C/C++ 템플릿·전처리기: `vector<int>`의 `<`나 `#include <...>`를 뒤집으면
#   유효한 결함이 아니라 컴파일 불능 코드가 된다 (빌드 게이트가 무효 처리로
#   걸러주지만, 애초에 사이트로 안 잡는 게 예산 낭비가 없다).
_EXCLUDED_ANCESTOR_TYPES = {
    "import_statement", "import_declaration", "call_expression_import",
    "template_argument_list", "template_parameter_list",
    "preproc_include", "preproc_def", "preproc_function_def",
}


@dataclass(frozen=True)
class _SpliceSite:
    start_byte: int
    end_byte: int
    replacement: bytes
    lineno: int
    operator: str
    description: str


def _has_excluded_ancestor(node) -> bool:
    cur = node.parent
    while cur is not None:
        if cur.type in _EXCLUDED_ANCESTOR_TYPES:
            return True
        # require("...") 호출 안의 문자열도 임포트 경로다.
        if cur.type == "call_expression" and cur.text and cur.text.startswith(b"require("):
            return True
        cur = cur.parent
    return False


def _collect_splice_sites(
    source: bytes, ts_language: str, changed_lines: set[int]
) -> list[_SpliceSite]:
    """변경된 줄 위의 뮤테이션 사이트를 바이트 오프셋과 함께 수집한다."""
    from tree_sitter_language_pack import get_parser

    tree = get_parser(ts_language).parse(source)
    sites: list[_SpliceSite] = []

    def walk(node) -> None:
        lineno = node.start_point[0] + 1  # tree-sitter row는 0-기반
        if node.child_count == 0:
            if lineno not in changed_lines:
                return
            text = node.type
            if text in _TOKEN_FLIPS:
                if _has_excluded_ancestor(node):
                    return
                flipped = _TOKEN_FLIPS[text]
                sites.append(_SpliceSite(
                    start_byte=node.start_byte, end_byte=node.end_byte,
                    replacement=flipped.encode(), lineno=lineno,
                    operator=_OPERATOR_KINDS[text],
                    description=f"{_OPERATOR_KINDS[text]} @ line {lineno}: {text} -> {flipped}",
                ))
            elif text in ("true", "false"):
                flipped = "false" if text == "true" else "true"
                sites.append(_SpliceSite(
                    start_byte=node.start_byte, end_byte=node.end_byte,
                    replacement=flipped.encode(), lineno=lineno,
                    operator="bool_flip",
                    description=f"bool_flip @ line {lineno}: {text} -> {flipped}",
                ))
            elif text == "number":
                raw = source[node.start_byte:node.end_byte]
                if raw == b"0":
                    sites.append(_SpliceSite(
                        start_byte=node.start_byte, end_byte=node.end_byte,
                        replacement=b"-1", lineno=lineno,
                        operator="literal_to_minus_one",
                        description=f"literal_to_minus_one @ line {lineno}: 0 -> -1",
                    ))
            return

        if node.type in _STRING_NODE_TYPES:
            if lineno in changed_lines and not _has_excluded_ancestor(node):
                raw = source[node.start_byte:node.end_byte]
                if len(raw) > 2:  # 이미 빈 문자열("")이면 뮤턴트가 무의미
                    quote = raw[:1]
                    sites.append(_SpliceSite(
                        start_byte=node.start_byte, end_byte=node.end_byte,
                        replacement=quote + quote, lineno=lineno,
                        operator="string_to_empty",
                        description=(
                            f"string_to_empty @ line {lineno}: "
                            f"{raw[:40]!r} -> {(quote + quote)!r}"
                        ),
                    ))
            return  # 문자열 내부 토큰은 더 안 들어간다 (내용 치환과 중복)

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return sites


def _apply_splice(source: bytes, site: _SpliceSite) -> bytes:
    return source[: site.start_byte] + site.replacement + source[site.end_byte :]


def _run_language_tests(repo: Path, command: tuple[str, ...]) -> bool | None:
    """테스트 통과 여부. 타임아웃이면 None(판정 불가)."""
    try:
        result = subprocess.run(
            list(command), cwd=repo, capture_output=True, text=True,
            timeout=_TEST_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    return result.returncode == 0


def _no_signal(candidate_id: str, sites_total: int = 0) -> MutationScore:
    return MutationScore(
        candidate_id=candidate_id, mutants_total=0, mutants_killed=0,
        sites_total=sites_total,
    )


def compute_mutation_score_ts(
    candidate: MergeCandidate,
    repo_path: str,
    base_content: str = "",
    *,
    budget: int | None = None,
    trust_threshold: float | None = None,
    spec: LanguageSpec | None = None,
) -> MutationScore:
    """비Python 후보의 변경 영역에 뮤턴트를 주입하고 언어별 테스트로 판정한다.

    verify/mutation.py의 compute_mutation_score와 같은 반환 계약. 신호를 만들
    수 없는 모든 경우(미지원 언어, tree-sitter 미설치, 테스트 명령 없음,
    baseline 실패)는 예외 대신 MutationScore(0/0)로 폴백한다.
    """
    spec = spec or detect_language(candidate.file_path)
    if spec is None or spec.ts_language is None or not candidate.file_path:
        return _no_signal(candidate.id)

    source = candidate.content.encode()
    changed_lines = _changed_line_numbers(base_content, candidate.content)
    try:
        sites = _collect_splice_sites(source, spec.ts_language, changed_lines)
    except Exception:
        # tree-sitter 미설치 / 문법 로드 실패 / 파싱 불가 — 신호 없음 폴백
        return _no_signal(candidate.id)

    if not sites or spec.test_command is None:
        return _no_signal(candidate.id, sites_total=len(sites))

    killed = 0
    total = 0
    runs = 0
    survived: list[str] = []

    with tempfile.TemporaryDirectory(prefix="weld-mutation-ts-") as tmp:
        tmp_repo = Path(tmp) / "repo"
        shutil.copytree(
            repo_path, tmp_repo,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "node_modules", "__pycache__", "target", "dist"
            ),
        )
        target_file = tmp_repo / candidate.file_path

        # baseline: 원본 후보가 (빌드 포함) 초록이어야 "실패 = 뮤턴트를
        # 잡았다"가 성립한다.
        target_file.write_bytes(source)
        if spec.build_command is not None:
            if _run_language_tests(tmp_repo, spec.build_command) is not True:
                return _no_signal(candidate.id, sites_total=len(sites))
        if _run_language_tests(tmp_repo, spec.test_command) is not True:
            return _no_signal(candidate.id, sites_total=len(sites))

        for site in sites:
            if budget is not None and runs >= budget:
                break

            target_file.write_bytes(_apply_splice(source, site))
            runs += 1

            if spec.build_command is not None:
                built = _run_language_tests(tmp_repo, spec.build_command)
                if built is None:
                    continue  # 빌드 타임아웃 — 판정 불가, 집계 제외
                if built is False:
                    # 무효 뮤턴트(컴파일 불능) — kill로 세면 점수가 부풀려지므로
                    # 집계에서 제외한다. 테스트가 결함을 '잡은' 게 아니다.
                    continue

            passed = _run_language_tests(tmp_repo, spec.test_command)
            if passed is None:
                continue  # 타임아웃 — 판정 불가, 집계 제외

            total += 1
            if not passed:
                killed += 1
            else:
                survived.append(site.description)

            if trust_threshold is not None and total >= _MIN_SAMPLES_FOR_EARLY_STOP:
                low, high = _wilson_interval(killed, total)
                if high < trust_threshold or low > trust_threshold:
                    break

    return MutationScore(
        candidate_id=candidate.id,
        mutants_total=total,
        mutants_killed=killed,
        survived_mutants=survived,
        sites_total=len(sites),
    )
