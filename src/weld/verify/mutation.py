"""담당: 김민재 (핵심 기여)

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

MVP 오퍼레이터 4개: 비교연산자 반전, 불리언 반전, 상수 오프바이원(0/-1),
null 체크 제거. null 체크 제거는 None 비교의 `is`/`is not`을 반전하는
것으로 구현했다 — 조건을 통째로 지우는 것과 실질적으로 같은 실패 모드
(반대로 동작하는 null 체크)를 포착하면서, AST 노드를 부모 참조 없이
제자리에서만 바꾸는 이 엔진의 단순한 구조에 맞췄다.

적응형 뮤턴트 스케줄링(시간 예산 안에서 조기 종료 + 약한 영역 우선 배분)은
스트레치 — 지금은 changed_lines 안의 모든 사이트를 다 돈다.
"""

from __future__ import annotations

import ast
import copy
import difflib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from coverage import CoverageData

from weld.types import MergeCandidate, MutationScore, TestId

_TEST_TIMEOUT_S = 60

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


def _set_constant(target_value: int) -> Callable[[ast.AST], None]:
    def mutate(node: ast.AST) -> None:
        node.value = target_value  # type: ignore[attr-defined]

    return mutate


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


def _run_tests_with_coverage(
    tmp_repo: Path, target_file: Path, lineno: int, tests: list[TestId]
) -> tuple[bool, bool]:
    """(그 줄이 실행됐는지, 테스트가 실패했는지)를 반환한다."""
    data_file = tmp_repo / ".weld-mutation-coverage"
    try:
        result = subprocess.run(
            ["coverage", "run", f"--data-file={data_file}", "-m", "pytest", "-q", *tests],
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


def compute_mutation_score(
    candidate: MergeCandidate,
    relevant_tests: list[TestId],
    repo_path: str,
    base_content: str = "",
) -> MutationScore:
    """candidate의 변경 영역에 뮤턴트를 주입하고 relevant_tests(pytest 노드 ID)가 잡아내는지 측정한다."""
    changed_lines = _changed_line_numbers(base_content, candidate.content)
    tree = ast.parse(candidate.content)
    sites = _collect_mutation_sites(tree, changed_lines)

    if not sites or not relevant_tests or not candidate.file_path:
        return MutationScore(candidate_id=candidate.id, mutants_total=0, mutants_killed=0)

    killed = 0
    total = 0
    survived: list[str] = []

    with tempfile.TemporaryDirectory(prefix="weld-mutation-") as tmp:
        tmp_repo = Path(tmp) / "repo"
        shutil.copytree(
            repo_path,
            tmp_repo,
            ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", "*.pyc", ".pytest_cache"),
        )
        target_file = tmp_repo / candidate.file_path

        for site in sites:
            mutated_source = _apply_site(tree, site)
            target_file.write_text(mutated_source)

            executed, failed = _run_tests_with_coverage(
                tmp_repo, target_file, site.lineno, relevant_tests
            )

            if not executed:
                # 이 뮤턴트는 테스트가 그 줄을 지나가지도 않았다 — 판단 불가, 집계 제외.
                continue

            total += 1
            if failed:
                killed += 1
            else:
                survived.append(site.description)

    return MutationScore(
        candidate_id=candidate.id,
        mutants_total=total,
        mutants_killed=killed,
        survived_mutants=survived,
    )
