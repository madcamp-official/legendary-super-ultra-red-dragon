"""담당: 김민재 (핵심 기여) / 오퍼레이터 확장(AOR/LCR/문자열/SBR): 이서영

뮤테이션 테스팅-라이트 엔진. "이 줄이 테스트로 실행됐나"가 아니라 "이 줄에
결함을 주입해도 테스트가 진짜로 잡아내나"를 확인한다.

동작 방식:
1. base_content 대비 candidate.content에서 바뀐 줄만 골라 그 범위 안에서만
   뮤턴트를 만든다(파일 전체 금지 — README 구현 규칙 1번).
2. 뮤턴트는 candidate.content를 ast로 파싱한 뒤 특정 노드 하나만 바꿔서
   ast.unparse로 재생성한다. 즉 "문법이 깨진 뮤턴트"는 애초에 나올 수가
   없다 — 별도 문법 체크 없이 규칙 2번(무효 뮤턴트로 사이클 낭비 방지)을
   구조적으로 만족한다.
3. 뮤턴트를 임시로 복제한 저장소의 candidate.file_path 위치에 써넣고,
   coverage로 감싸서 relevant_tests만 실행한다.
4. coverage 데이터로 "그 줄을 테스트가 실제로 실행했는지" 확인한다(규칙 3번).
   실행 안 됐으면 그 뮤턴트는 유효한 신호가 아니므로 집계에서 제외한다 —
   테스트 실패/성공 여부와 무관한 뮤턴트를 "잡았다"고 착각하는 걸 막는다.
5. 100%를 요구하지 않는다(규칙 4) — 동등 뮤턴트가 일부 survive하는 건
   정상이고, 그 목록을 survived_mutants에 그대로 남긴다.

오퍼레이터 8개: 비교연산자 반전, 불리언 반전, 상수 오프바이원(0/-1),
null 체크 제거, 산술연산자 반전(AOR), 논리연산자 반전(LCR), 문자열 리터럴
제거, 문장 삭제(SBR). null 체크 제거는 None 비교의 `is`/`is not`을 반전하는
것으로 구현했다 — 조건을 통째로 지우는 것과 실질적으로 같은 실패 모드
(반대로 동작하는 null 체크)를 포착하면서, AST 노드를 부모 참조 없이
제자리에서만 바꾸는 이 엔진의 단순한 구조에 맞췄다.

문장 삭제(SBR)는 노드를 부모의 body 리스트에서 제거하는 대신, 같은 노드
객체의 `__class__`를 `ast.Pass`로 바꿔치기하고 위치 정보만 이어붙인다 —
부모 참조 없이도 "이 문장을 지워도 테스트가 잡아내는가"를 검증할 수 있다.
compound 문장(if/for/while/함수 등, body를 가진 노드)은 SBR 대상에서 제외한다
— 안 그러면 changed_lines 밖의, 아직 안 건드린 중첩 코드까지 통째로 날아가서
"변경 영역에만 뮤턴트를 넣는다"는 규칙(1번)을 넓은 단위에서 어기게 된다.
로깅 호출(`log.debug(...)` 등)과 독스트링류(문자열 리터럴 단독 문장)도 SBR/
문자열 오퍼레이터 대상에서 제외한다 — 이런 문장은 어떤 테스트도 관찰하지
않아 뮤턴트가 항상 생존해서, 실제 위험 신호 없이 점수만 깎아먹는다.

적응형 뮤턴트 스케줄링(심화 기여):
"모든 뮤턴트를 브루트포스로 다 돌리기"를 "제한된 시간 예산 안에서 최대
확신을 뽑는 순차적 의사결정"으로 격상시킨다. 세 가지로 구성된다.
- 약한 영역 우선 배분: 뮤턴트를 돌리기 전에 원본 후보를 coverage의
  dynamic context(테스트 함수별 커버리지)로 한 번 프로파일링해서, 각 줄을
  몇 개의 테스트가 실제로 지나가는지 센다. 빡세게 커버된 줄의 뮤턴트는
  거의 확실히 잡혀서 정보량이 적고, 약하게 커버된 줄이 바로 위험(오버피팅/
  reward hacking)이 숨는 곳이므로 그쪽부터 먼저 돌린다. 0개 커버(어차피
  집계 제외될) 사이트는 아예 실행하지 않아 낭비를 없앤다.
- 조기 종료: 매 뮤턴트 후 kill-rate에 Wilson 신뢰구간을 씌워, 구간이
  판정 임계값(trust_threshold) 위/아래로 확실히 벗어나면 멈춘다 —
  더 돌려도 결론이 안 바뀌므로.
- 예산: budget으로 실행 횟수 상한을 둔다.
budget/trust_threshold는 opt-in(기본 None)이라 안 주면 예전처럼 전부 돈다.
약한 영역 우선/0커버 제거는 항상 켜지지만 결과값을 바꾸지 않고 낭비만 준다.

주의(의도된 설계): 약한 영역 우선 배분은 초반 표본을 "살아남기 쉬운"
쪽으로 편향시켜서 kill-rate 추정을 초반에 낮게 잡는 경향이 있다. 이는
버그가 아니라 의도다 — 우리 목표(북극성: 오탐률 0%)에는 위험한 뮤턴트를
빨리 발견해 fail-fast 하는 게 유리하기 때문이다.
"""

from __future__ import annotations

import ast
import copy
import difflib
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from coverage import CoverageData

from weld.types import MergeCandidate, MutationScore, TestId

_TEST_TIMEOUT_S = 60
_MIN_SAMPLES_FOR_EARLY_STOP = 5

_COMPARISON_FLIPS: dict[type, type] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}

_ARITHMETIC_FLIPS: dict[type, type] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
}

_LOGICAL_FLIPS: dict[type, type] = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}

# body를 가진(compound) 문장 — SBR 대상에서 제외한다. 통째로 pass화하면
# changed_lines 밖의, 아직 안 건드린 중첩 코드까지 날아가서 "변경 영역에만
# 뮤턴트를 넣는다"는 규칙을 넓은 단위에서 어기게 된다.
_COMPOUND_STMT_TYPES: tuple[type, ...] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)
_SBR_EXCLUDED_TYPES: tuple[type, ...] = (
    ast.Return,
    ast.Pass,
    ast.Global,
    ast.Nonlocal,
    ast.Import,
    ast.ImportFrom,
    *_COMPOUND_STMT_TYPES,
)

_LOGGING_METHOD_NAMES = {
    "debug", "info", "warning", "warn", "error", "critical", "exception", "log",
}


@dataclass
class _MutationSite:
    lineno: int
    col_offset: int
    node_type: type
    operator: str
    description: str
    mutate: Callable[[ast.AST], None]


def _changed_line_numbers(base_content: str, candidate_content: str) -> set[int]:
    """base_content 대비 candidate_content에서 바뀐 줄 번호(1-indexed) 집합.

    base_content가 없으면(호출자가 안 넘겨줬으면) 비교 대상이 없으니
    안전한 기본값으로 파일 전체를 변경 영역 취급한다.
    """
    if not base_content:
        return set(range(1, len(candidate_content.splitlines()) + 1))
    matcher = difflib.SequenceMatcher(
        a=base_content.splitlines(), b=candidate_content.splitlines()
    )
    changed: set[int] = set()
    for tag, _, _, b_start, b_end in matcher.get_opcodes():
        if tag != "equal":
            changed.update(range(b_start + 1, b_end + 1))
    return changed


def _is_none_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _make_op_flip(index: int, new_op_type: type) -> Callable[[ast.AST], None]:
    def mutate(node: ast.AST) -> None:
        node.ops[index] = new_op_type()  # type: ignore[attr-defined]

    return mutate


def _flip_bool(node: ast.AST) -> None:
    node.value = not node.value  # type: ignore[attr-defined]


def _set_constant(target_value: int | str) -> Callable[[ast.AST], None]:
    def mutate(node: ast.AST) -> None:
        node.value = target_value  # type: ignore[attr-defined]

    return mutate


def _make_op_attr_flip(new_op_type: type) -> Callable[[ast.AST], None]:
    """BinOp/BoolOp처럼 연산자를 단일 `.op` 속성으로 갖는 노드용 반전 헬퍼."""

    def mutate(node: ast.AST) -> None:
        node.op = new_op_type()  # type: ignore[attr-defined]

    return mutate


def _replace_with_pass(node: ast.AST) -> None:
    """문장 노드를 그 자리에서 `ast.Pass`로 바꿔치기한다(SBR).

    부모의 body 리스트에서 노드를 제거하는 대신, 같은 객체의 `__class__`를
    바꾸고 위치 정보만 이어붙인다 — 이 엔진이 부모 참조 없이 노드 하나를
    제자리에서만 변형하는 구조이기 때문에 택한 방식. `ast.unparse`의 Pass
    방문자는 위치 정보 외 다른 필드를 참조하지 않으므로 안전하게 동작한다.
    """
    lineno = node.lineno  # type: ignore[attr-defined]
    col_offset = node.col_offset  # type: ignore[attr-defined]
    end_lineno = getattr(node, "end_lineno", lineno)
    end_col_offset = getattr(node, "end_col_offset", col_offset)
    node.__dict__.clear()
    node.__class__ = ast.Pass
    node.lineno = lineno
    node.col_offset = col_offset
    node.end_lineno = end_lineno
    node.end_col_offset = end_col_offset


def _is_low_signal_statement(node: ast.AST) -> bool:
    """뮤턴트로 바꿔도 어떤 테스트도 관찰할 수 없어 항상 생존하는 문장인지.

    로깅 호출(`log.debug(...)` 등)과 독스트링류(문자열 리터럴 단독 문장)가
    대표적이다 — 이런 문장을 SBR 후보에 넣으면 실제 위험 신호 없이 뮤테이션
    점수만 깎여서, 멀쩡한 후보가 억울하게 에스컬레이션될 수 있다.
    """
    if not isinstance(node, ast.Expr):
        return False
    if isinstance(node.value, ast.Call):
        func = node.value.func
        if isinstance(func, ast.Attribute) and func.attr in _LOGGING_METHOD_NAMES:
            return True
        if isinstance(func, ast.Name) and func.id in _LOGGING_METHOD_NAMES:
            return True
        return False
    return isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _collect_mutation_sites(tree: ast.AST, changed_lines: set[int]) -> list[_MutationSite]:
    sites: list[_MutationSite] = []
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None or lineno not in changed_lines:
            continue

        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                op_type = type(op)
                if op_type not in _COMPARISON_FLIPS:
                    continue
                new_op_type = _COMPARISON_FLIPS[op_type]
                comparator = node.comparators[i]
                left = node.left if i == 0 else node.comparators[i - 1]
                is_null_check = op_type in (ast.Is, ast.IsNot) and (
                    _is_none_constant(left) or _is_none_constant(comparator)
                )
                operator = "null_check_removal" if is_null_check else "comparison_flip"
                sites.append(
                    _MutationSite(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        node_type=ast.Compare,
                        operator=operator,
                        description=(
                            f"{operator} @ line {node.lineno}: "
                            f"{op_type.__name__} -> {new_op_type.__name__}"
                        ),
                        mutate=_make_op_flip(i, new_op_type),
                    )
                )

        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                sites.append(
                    _MutationSite(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        node_type=ast.Constant,
                        operator="boolean_flip",
                        description=(
                            f"boolean_flip @ line {node.lineno}: "
                            f"{node.value} -> {not node.value}"
                        ),
                        mutate=_flip_bool,
                    )
                )
            elif isinstance(node.value, (int, float)):
                if node.value != 0:
                    sites.append(
                        _MutationSite(
                            lineno=node.lineno,
                            col_offset=node.col_offset,
                            node_type=ast.Constant,
                            operator="literal_to_zero",
                            description=(
                                f"literal_to_zero @ line {node.lineno}: {node.value!r} -> 0"
                            ),
                            mutate=_set_constant(0),
                        )
                    )
                if node.value != -1:
                    sites.append(
                        _MutationSite(
                            lineno=node.lineno,
                            col_offset=node.col_offset,
                            node_type=ast.Constant,
                            operator="literal_to_minus_one",
                            description=(
                                f"literal_to_minus_one @ line {node.lineno}: {node.value!r} -> -1"
                            ),
                            mutate=_set_constant(-1),
                        )
                    )
            elif isinstance(node.value, str) and node.value != "":
                sites.append(
                    _MutationSite(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        node_type=ast.Constant,
                        operator="string_to_empty",
                        description=(
                            f"string_to_empty @ line {node.lineno}: {node.value!r} -> ''"
                        ),
                        mutate=_set_constant(""),
                    )
                )

        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type in _ARITHMETIC_FLIPS:
                new_op_type = _ARITHMETIC_FLIPS[op_type]
                sites.append(
                    _MutationSite(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        node_type=ast.BinOp,
                        operator="arithmetic_flip",
                        description=(
                            f"arithmetic_flip @ line {node.lineno}: "
                            f"{op_type.__name__} -> {new_op_type.__name__}"
                        ),
                        mutate=_make_op_attr_flip(new_op_type),
                    )
                )

        elif isinstance(node, ast.BoolOp):
            op_type = type(node.op)
            if op_type in _LOGICAL_FLIPS:
                new_op_type = _LOGICAL_FLIPS[op_type]
                sites.append(
                    _MutationSite(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        node_type=ast.BoolOp,
                        operator="logical_flip",
                        description=(
                            f"logical_flip @ line {node.lineno}: "
                            f"{op_type.__name__} -> {new_op_type.__name__}"
                        ),
                        mutate=_make_op_attr_flip(new_op_type),
                    )
                )

        if (
            isinstance(node, ast.stmt)
            and not isinstance(node, _SBR_EXCLUDED_TYPES)
            and not _is_low_signal_statement(node)
        ):
            sites.append(
                _MutationSite(
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    node_type=type(node),
                    operator="statement_removal",
                    description=(
                        f"statement_removal @ line {node.lineno}: "
                        f"{type(node).__name__} -> pass"
                    ),
                    mutate=_replace_with_pass,
                )
            )
    return sites


def _find_node(tree: ast.AST, lineno: int, col_offset: int, node_type: type) -> ast.AST:
    for node in ast.walk(tree):
        if (
            isinstance(node, node_type)
            and getattr(node, "lineno", None) == lineno
            and getattr(node, "col_offset", None) == col_offset
        ):
            return node
    raise LookupError(f"뮤테이션 지점(line {lineno}, col {col_offset})을 복사된 트리에서 못 찾음")


def _apply_site(tree: ast.AST, site: _MutationSite) -> str:
    mutated_tree = copy.deepcopy(tree)
    node = _find_node(mutated_tree, site.lineno, site.col_offset, site.node_type)
    site.mutate(node)
    ast.fix_missing_locations(mutated_tree)
    return ast.unparse(mutated_tree)


def _wilson_interval(killed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """kill-rate(killed/total)의 Wilson 스코어 신뢰구간 (low, high).

    비율 추정에 정규근사(Wald)보다 안정적이라 표본이 적거나 0/1에 치우쳐도
    잘 동작한다. total==0이면 아무것도 모르므로 (0, 1)을 준다.
    """
    if total == 0:
        return (0.0, 1.0)
    p = killed / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (max(0.0, center - half), min(1.0, center + half))


def _profile_line_coverage(
    tmp_repo: Path, target_file: Path, tests: list[TestId]
) -> dict[int, int]:
    """원본(비뮤턴트) 후보를 한 번 돌려, target_file의 각 줄을 몇 개의 서로 다른
    테스트 함수가 실행했는지 센다({lineno: 테스트 수}).

    coverage의 dynamic context 기능으로 테스트 함수별 커버리지를 기록한다.
    프로파일링이 실패하거나 컨텍스트가 안 잡히면 빈 dict을 반환하고, 호출부는
    그 경우 우선순위 배분/사전 필터를 끄고 예전처럼 전부 돈다(안전한 폴백).
    """
    rc_file = tmp_repo / ".weld-coveragerc"
    rc_file.write_text("[run]\ndynamic_context = test_function\n")
    data_file = tmp_repo / ".weld-profile-coverage"
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--rcfile={rc_file}",
                f"--data-file={data_file}",
                "-m",
                "pytest",
                "-q",
                *tests,
            ],
            cwd=tmp_repo,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {}

    counts: dict[int, int] = {}
    try:
        cov_data = CoverageData(basename=str(data_file))
        cov_data.read()
        target_resolved = target_file.resolve()
        for measured in cov_data.measured_files():
            if Path(measured).resolve() != target_resolved:
                continue
            ctx_map = cov_data.contexts_by_lineno(measured)
            for lineno, contexts in ctx_map.items():
                # 빈 컨텍스트("")는 테스트 밖에서 실행된 것 — 테스트 수에서 뺀다.
                counts[lineno] = len({c for c in contexts if c})
            break
    except Exception:
        return {}
    return counts


def _run_tests_with_coverage(
    tmp_repo: Path, target_file: Path, lineno: int, tests: list[TestId]
) -> tuple[bool, bool]:
    """(그 줄이 실행됐는지, 테스트가 실패했는지)를 반환한다."""
    data_file = tmp_repo / ".weld-mutation-coverage"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "coverage", "run", f"--data-file={data_file}", "-m", "pytest", "-q", *tests],
            cwd=tmp_repo,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, True

    failed = result.returncode != 0

    executed_lines: set[int] = set()
    try:
        cov_data = CoverageData(basename=str(data_file))
        cov_data.read()
        target_resolved = target_file.resolve()
        for measured in cov_data.measured_files():
            if Path(measured).resolve() == target_resolved:
                executed_lines = set(cov_data.lines(measured) or [])
                break
    except Exception:
        executed_lines = set()

    return lineno in executed_lines, failed


def _prioritize_sites(
    sites: list[_MutationSite], line_coverage: dict[int, int]
) -> list[_MutationSite]:
    """약한 영역 우선 배분: 프로파일링이 성공했으면 0커버 사이트를 버리고
    약하게 커버된(테스트 수 적은) 줄부터 오도록 정렬한다. 프로파일링이
    쓸모 있는 데이터를 못 냈으면(전부 0) 원래 순서 그대로 둔다(안전한 폴백)."""
    if not line_coverage or not any(count > 0 for count in line_coverage.values()):
        return sites
    covered = [s for s in sites if line_coverage.get(s.lineno, 0) > 0]
    covered.sort(key=lambda s: line_coverage.get(s.lineno, 0))
    return covered


def compute_mutation_score(
    candidate: MergeCandidate,
    relevant_tests: list[TestId],
    repo_path: str,
    base_content: str = "",
    *,
    budget: int | None = None,
    trust_threshold: float | None = None,
) -> MutationScore:
    """candidate의 변경 영역에 뮤턴트를 주입하고 relevant_tests(pytest 노드 ID)가 잡아내는지 측정한다.

    budget: 뮤턴트 실행(테스트 한 바퀴) 횟수 상한. None이면 무제한.
    trust_threshold: 이 값이 주어지면 조기 종료를 켠다. kill-rate의 Wilson
        신뢰구간이 이 임계값 위/아래로 확실히 벗어나면 판정이 굳은 것으로
        보고 남은 뮤턴트를 안 돌린다. None이면 조기 종료 없음.

    다국어 라우팅: 후보 파일이 Python(.py)이 아니면 tree-sitter 기반 엔진
    (verify/mutation_ts.py)으로 위임한다. relevant_tests는 pytest 노드 ID라
    비Python 언어에는 의미가 없어 그쪽 엔진은 언어별 테스트 명령 전체를
    돌린다. 공개 시그니처는 그대로라 호출부(cli.py 등)는 바뀌지 않는다.
    """
    if candidate.file_path and not candidate.file_path.endswith(".py"):
        from weld.verify.mutation_ts import compute_mutation_score_ts

        return compute_mutation_score_ts(
            candidate,
            repo_path=repo_path,
            base_content=base_content,
            budget=budget,
            trust_threshold=trust_threshold,
        )

    changed_lines = _changed_line_numbers(base_content, candidate.content)
    try:
        tree = ast.parse(candidate.content)
    except SyntaxError:
        # LLM이 코드가 아닌 응답(산문 등)을 내놓은 후보 — sandbox의 컴파일
        # 게이트가 어차피 거르므로, 여기서 크래시로 파이프라인 전체를 죽이지
        # 말고 "신호 없음"으로 조용히 반환한다.
        return MutationScore(candidate_id=candidate.id, mutants_total=0, mutants_killed=0)
    sites = _collect_mutation_sites(tree, changed_lines)
    sites_total = len(sites)

    if not sites or not relevant_tests or not candidate.file_path:
        return MutationScore(
            candidate_id=candidate.id, mutants_total=0, mutants_killed=0, sites_total=sites_total
        )

    killed = 0
    total = 0
    runs = 0
    uncovered = 0
    survived: list[str] = []

    with tempfile.TemporaryDirectory(prefix="weld-mutation-") as tmp:
        tmp_repo = Path(tmp) / "repo"
        shutil.copytree(
            repo_path,
            tmp_repo,
            ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", "*.pyc", ".pytest_cache"),
        )
        target_file = tmp_repo / candidate.file_path

        # 뮤턴트 돌리기 전에 원본을 한 번 프로파일링해서 약한 영역 우선순위를 잡는다.
        target_file.write_text(candidate.content)
        line_coverage = _profile_line_coverage(tmp_repo, target_file, relevant_tests)
        sites = _prioritize_sites(sites, line_coverage)

        for site in sites:
            if budget is not None and runs >= budget:
                break

            mutated_source = _apply_site(tree, site)
            target_file.write_text(mutated_source)

            executed, failed = _run_tests_with_coverage(
                tmp_repo, target_file, site.lineno, relevant_tests
            )
            runs += 1

            if not executed:
                # 이 뮤턴트는 테스트가 그 줄을 지나가지도 않았다 — 판단 불가, 집계 제외.
                uncovered += 1
                continue

            total += 1
            if failed:
                killed += 1
            else:
                survived.append(site.description)

            # 조기 종료: 신뢰구간이 임계값 한쪽으로 확실히 벗어나면 결론이 굳었다.
            if trust_threshold is not None and total >= _MIN_SAMPLES_FOR_EARLY_STOP:
                low, high = _wilson_interval(killed, total)
                if high < trust_threshold or low > trust_threshold:
                    break

    return MutationScore(
        candidate_id=candidate.id,
        mutants_total=total,
        mutants_killed=killed,
        survived_mutants=survived,
        sites_total=sites_total,
        mutants_uncovered=uncovered,
    )
