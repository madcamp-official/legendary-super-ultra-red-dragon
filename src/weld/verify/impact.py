"""담당: 이재준

변경된 파일/줄과 관련된 테스트만 선별한다. 뮤테이션 테스팅과 병렬 검증 둘 다의
속도를 이 선별 결과가 떠받친다.

반환값은 파일명이 아니라 **개별 테스트 함수(pytest 노드 ID)** 목록이다.

구현(line-level coverage 매핑, import 그래프 대체):
"누가 이 파일을 import하는가"라는 정적 근사 대신 "어떤 테스트가 실행 중
실제로 이 줄을 지나갔는가"를 직접 기록한다. coverage.py의
`dynamic_context = test_function` 설정(verify/mutation.py의
_profile_line_coverage가 쓰는 것과 같은 메커니즘)을 켠 채로 저장소 전체
테스트 스위트를 한 번 돌리면, 커버리지 데이터에 "줄 -> 그 줄을 지나간
테스트 집합"이 컨텍스트로 그대로 남는다. 이게 Bazel의 명시적 의존성
그래프가 Google에 제공하는 것과 같은 역할(파일 단위보다 정밀하고, "import는
하지만 실행 경로는 안 지나가는" 거짓양성이 없음)을 한다.

이 전체-스위트 실행(baseline)은 프로세스당 한 번만 하면 되는 초기 비용이라
(_baseline_cache로 저장소 경로별 메모이즈), 후보 여러 개를 검증하는 동안
재계산하지 않고 재사용한다. changed_files만 주어지면 그 파일의 모든 줄이
매핑하는 테스트 합집합을 쓰고(파일 단위 근사, 이전 import-그래프보다는
여전히 더 정밀함 — "실제로 실행됐는지"는 보장), changed_lines로 정확한
변경 줄 번호까지 주어지면 그 줄만 보고 더 좁힌다.

baseline 실행 자체가 실패하거나(coverage/pytest 미설치, 타임아웃) 특정
파일이 baseline에 없으면(신규 파일 등) 보수적으로 저장소의 모든 테스트를
반환한다 — "관련 테스트를 못 찾으면 전부 돌린다"는 이 파일의 기존 정책과
같다.

두 가지는 verify/callgraph.py(tree-sitter 정적 call graph + RTA)가 보완한다:
  - python 자체의 구멍: changed_lines로 들어온 줄이 baseline 실행 당시엔
    없던 신규 줄(기존 파일에 새로 추가된 코드)이면 커버리지 매핑이 아예
    없다 — 이 줄을 감싼 함수에서 caller를 타고 올라가며 baseline에 걸리는
    지점을 찾는다(_callgraph_gap_fallback).
  - coverage.py 같은 "테스트별 실행 컨텍스트"가 없는 언어(JS/TS/Go/Rust/
    Java/C/C++): 정적 call graph 도달성으로 1차 선별하고, 못 찾으면
    파일→언어 단위로 제한적으로 폴백한다(_select_via_callgraph). 이 결과는
    아직 verify/sandbox.py 실행에는 안 쓰인다 — 선별까지만이 이번 범위.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from coverage import CoverageData

from weld.langs import detect_language
from weld.types import TestId
from weld.verify import callgraph
from weld.verify.callgraph import _EXCLUDED_DIR_NAMES

_TEST_FUNC_PREFIX = "test_"
_TEST_CLASS_PREFIX = "Test"

# baseline 서브프로세스임을 알리는 플래그. baseline이 돌리는 테스트 스위트
# 안에서 다시 select_relevant_tests가 불리면(예: weld 저장소 자신에 대고
# weld merge를 실행하는 테스트/실사용), 또 baseline을 띄워 무한 재귀하는
# 것을 막는다 — 플래그가 서 있으면 baseline 없이 정적 폴백으로만 답한다.
_BASELINE_ENV_FLAG = "WELD_IMPACT_BASELINE_RUNNING"
_BASELINE_TIMEOUT_SECONDS = 600

# 저장소 경로(resolve된 문자열) -> {rel_path: {lineno: {test_id, ...}}}.
# 프로세스 생애 동안만 유지되는 메모이즈 캐시 — 디스크에 영속화하지 않는다
# (소스가 바뀌면 캐시가 stale해지는 문제를 새로 만들지 않기 위해서다;
# 새 프로세스에서는 항상 다시 baseline을 돈다).
_baseline_cache: dict[str, dict[str, dict[int, set[TestId]]]] = {}


def _iter_python_files(repo_root: Path) -> list[Path]:
    return [
        path
        for path in repo_root.rglob("*.py")
        if not _EXCLUDED_DIR_NAMES.intersection(path.parts)
    ]


def _normalize(repo_root: Path, file_path: str) -> str:
    path = Path(file_path)
    if path.is_absolute():
        try:
            path = path.relative_to(repo_root)
        except ValueError:
            pass
    return path.as_posix()


def _is_test_file(rel_path: str) -> bool:
    name = Path(rel_path).name
    return name.startswith("test_") or name.endswith("_test.py")


def _test_node_ids(repo_root: Path, rel_path: str) -> list[TestId]:
    try:
        tree = ast.parse((repo_root / rel_path).read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []

    node_ids: list[TestId] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith(_TEST_FUNC_PREFIX):
                node_ids.append(f"{rel_path}::{node.name}")
        elif isinstance(node, ast.ClassDef) and node.name.startswith(_TEST_CLASS_PREFIX):
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith(_TEST_FUNC_PREFIX):
                    node_ids.append(f"{rel_path}::{node.name}::{member.name}")
    return node_ids


def _all_test_node_ids(repo_root: Path) -> list[TestId]:
    """저장소의 모든 테스트를 pytest 노드 ID로 펼친다 (보수적 폴백용)."""
    test_files = sorted(
        rel
        for rel in (_normalize(repo_root, str(p)) for p in _iter_python_files(repo_root))
        if _is_test_file(rel)
    )
    node_ids: list[TestId] = []
    for test_file in test_files:
        ids = _test_node_ids(repo_root, test_file)
        node_ids.extend(ids if ids else [test_file])
    return node_ids


def _module_name_candidates(rel_path: Path) -> list[str]:
    """파일의 저장소 상대 경로에서 pytest가 붙였을 법한 dotted 모듈 이름 후보.

    pytest는 (패키지에 `__init__.py`가 없으면) 테스트 모듈을 파일명만으로
    최상위 모듈("test_foo")로 임포트하는 경우가 흔하다. 어느 쪽으로
    임포트됐을지 미리 알 수 없으니 저장소 루트 기준 dotted 경로와, 파일명만
    쓰는 후보 둘 다 남긴다.
    """
    parts = list(rel_path.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    if not parts:
        return []
    return list({".".join(parts), parts[-1]})


def _build_module_to_file(repo_root: Path) -> dict[str, str]:
    module_to_file: dict[str, str] = {}
    for file in _iter_python_files(repo_root):
        rel_posix = file.relative_to(repo_root).as_posix()
        for name in _module_name_candidates(file.relative_to(repo_root)):
            module_to_file.setdefault(name, rel_posix)
    return module_to_file


def _resolve_context(context: str, module_to_file: dict[str, str]) -> TestId | None:
    """coverage dynamic_context 문자열("modname.Class.method" 등)을 pytest 노드 ID로 바꾼다.

    coverage.py의 `dynamic_context = test_function`은 pytest 노드 ID가 아니라
    "테스트 함수의 모듈 dotted 이름 + 코드상 qualname"을 컨텍스트로 남긴다
    (예: "test_foo.TestFooClass.test_method"). 앞쪽 dotted prefix를 줄여가며
    모듈 이름과 매칭한 뒤, 나머지 qualname 부분을 "::"로 이어 붙인다.
    매칭되는 모듈을 못 찾으면(예: 동적으로 생성된 컨텍스트) None을 반환한다.
    """
    parts = context.split(".")
    for i in range(len(parts), 0, -1):
        rel_path = module_to_file.get(".".join(parts[:i]))
        if rel_path is None:
            continue
        remainder = parts[i:]
        if not remainder:
            return None
        return f"{rel_path}::{'::'.join(remainder)}"
    return None


def _run_baseline_coverage(repo_root: Path) -> Path | None:
    """저장소 전체 테스트 스위트를 dynamic_context 켠 채로 한 번 돌린다.

    coverage/pytest가 없거나 타임아웃/기타 오류가 나면 None을 반환하고,
    호출부는 그 경우 baseline이 없는 것으로 취급해 보수적 폴백으로 넘어간다.
    바이너리 이름("coverage")으로 바로 부르면 PATH에 스크립트가 안 잡히는
    환경이 있어(예: pip --user 설치), 항상 `sys.executable -m coverage`로
    현재 인터프리터를 통해 부른다.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="weld-impact-baseline-"))
    rc_file = tmp_dir / ".weld-baseline-coveragerc"
    rc_file.write_text("[run]\ndynamic_context = test_function\n")
    data_file = tmp_dir / ".weld-baseline-coverage"
    env = os.environ.copy()
    env[_BASELINE_ENV_FLAG] = "1"
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
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            timeout=_BASELINE_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return data_file if data_file.exists() else None


def _load_baseline_mapping(
    data_file: Path, repo_root: Path
) -> dict[str, dict[int, set[TestId]]]:
    mapping: dict[str, dict[int, set[TestId]]] = {}
    try:
        cov_data = CoverageData(basename=str(data_file))
        cov_data.read()
    except Exception:
        return mapping

    module_to_file = _build_module_to_file(repo_root)

    for measured in cov_data.measured_files():
        rel_path = _normalize(repo_root, measured)
        line_map: dict[int, set[TestId]] = {}
        for lineno, contexts in cov_data.contexts_by_lineno(measured).items():
            # 빈 컨텍스트("")는 테스트 밖(임포트/컬렉션 시점)에서 실행된 것.
            # 결과가 빈 set이어도 그대로 기록한다 — "이 줄은 봤지만 테스트가
            # 없다"(collection-time 전용 줄 등)와 "이 줄 자체를 아예 못
            # 봤다"(baseline 이후 추가된 신규 줄)를 구분해야, gap-fallback이
            # 후자에서만 발동한다(_select_python의 `tests is None` 체크).
            line_map[lineno] = {
                resolved
                for c in contexts
                if c and (resolved := _resolve_context(c, module_to_file)) is not None
            }
        if line_map:
            mapping[rel_path] = line_map
    return mapping


def _ensure_baseline(repo_root: Path) -> dict[str, dict[int, set[TestId]]]:
    key = str(repo_root.resolve())
    if key in _baseline_cache:
        return _baseline_cache[key]

    mapping: dict[str, dict[int, set[TestId]]] = {}
    data_file = _run_baseline_coverage(repo_root)
    if data_file is not None:
        mapping = _load_baseline_mapping(data_file, repo_root)

    _baseline_cache[key] = mapping
    return mapping


def _callgraph_gap_fallback(
    graph: callgraph.CallGraph,
    rel_path: str,
    lineno: int,
    baseline: dict[str, dict[int, set[TestId]]],
) -> set[TestId]:
    """baseline 실행 당시엔 없던 신규 줄이라 line_map에 매핑이 없을 때만
    쓰인다. 그 줄을 감싼 함수에서 caller를 하나씩 타고 올라가며, caller의
    줄 범위가 baseline coverage에 걸리는 첫 지점을 찾아 그 테스트 집합을
    채택한다 — coverage 신호를 tree-sitter 신호보다 우선시하되, coverage가
    비어있는 지점만 tree-sitter로 메운다. 끝까지 못 찾으면 빈 집합(기존
    python 경로의 안전성을 낮추지 않는다 — 새 신호를 얹을 뿐이다)."""
    start = graph.line_index.get((rel_path, lineno))
    if start is None:
        return set()
    for qname in callgraph.climb_callers(graph, start):
        node = graph.nodes_by_qname.get(qname)
        if node is None:
            continue
        line_map = baseline.get(node.rel_path)
        if line_map is None:
            continue
        covered: set[TestId] = set()
        for ln in range(node.start_line, node.end_line + 1):
            covered |= line_map.get(ln, set())
        if covered:
            return covered
    return set()


def _select_python(
    repo_root: Path,
    changed_files: list[str],
    changed_lines: dict[str, set[int]] | None,
) -> set[TestId]:
    if os.environ.get(_BASELINE_ENV_FLAG):
        # baseline 서브프로세스가 돌린 테스트 안에서 재진입한 경우 — 또
        # baseline을 띄우면 무한 재귀. 서브프로세스 없이 정적 폴백으로 답한다.
        return set(_all_test_node_ids(repo_root))

    baseline = _ensure_baseline(repo_root)
    changed = {_normalize(repo_root, f) for f in changed_files}

    # tree-sitter call graph는 gap이 실제로 하나라도 나올 때만 만든다(대부분의
    # 변경은 baseline에 다 걸려 있어 이 비용 자체가 필요 없는 경우가 많다).
    graph_holder: list[callgraph.CallGraph] = []

    def gap_fallback(rel_path: str, lineno: int) -> set[TestId]:
        if not graph_holder:
            graph_holder.append(callgraph.build_or_load_graph(repo_root, {"python"}))
        return _callgraph_gap_fallback(graph_holder[0], rel_path, lineno, baseline)

    relevant: set[TestId] = set()
    for rel_path in changed:
        line_map = baseline.get(rel_path)
        if line_map is None:
            # baseline에 없는 파일(신규 파일, baseline 실행 실패 등) — 좁히길
            # 포기하고 저장소 전체 테스트로 보수적으로 폴백한다.
            return set(_all_test_node_ids(repo_root))

        lines = changed_lines.get(rel_path) if changed_lines else None
        target_lines = lines if lines else line_map.keys()
        for lineno in target_lines:
            tests = line_map.get(lineno)
            if tests is None and lines:
                # line_map.keys()를 도는 파일 단위 근사 경로에서는 lineno가
                # 애초에 그 dict의 키라 gap이 있을 수 없다 — changed_lines로
                # 명시된 줄만 이 gap이 생긴다.
                tests = gap_fallback(rel_path, lineno)
            relevant.update(tests or ())

    return relevant


def _select_via_callgraph(
    repo_root: Path,
    language: str,
    changed_files: list[str],
    changed_lines: dict[str, set[int]] | None,
) -> set[TestId]:
    """coverage.py 같은 테스트별 실행 컨텍스트가 없는 언어의 1차 선별.
    tree-sitter 정적 call graph 도달성으로 찾고, 못 찾으면 파일→언어 단위로
    제한적으로 폴백한다(저장소 전체·전체 언어로는 안 넓힌다)."""
    graph = callgraph.build_or_load_graph(repo_root, {language})
    relevant: set[TestId] = set()

    for f in changed_files:
        rel_path = _normalize(repo_root, f)
        lines = changed_lines.get(rel_path) if changed_lines else None
        if not lines:
            lines = {ln for (rp, ln) in graph.line_index if rp == rel_path}

        found: set[TestId] = set()
        for lineno in lines:
            found |= callgraph.find_reachable_tests(graph, rel_path, lineno)
        if not found:
            found = callgraph.fallback_tests_for_file(graph, rel_path)
        if not found:
            found = callgraph.fallback_tests_for_language(graph, language)
        relevant |= found

    return relevant


def select_relevant_tests(
    changed_files: list[str],
    repo_path: str,
    changed_lines: dict[str, set[int]] | None = None,
) -> list[TestId]:
    """changed_files(및 선택적으로 changed_lines)와 관련된 테스트의 ID 목록을 반환한다.

    changed_lines: {rel_path: {lineno, ...}}. 주어지면 그 줄만 봐서 더 좁힌다.
    안 주면(None) 파일 단위로 근사 — 그 파일의 어느 줄이든 지나간 테스트 전부.

    언어별로 나눠 처리한다 — python은 기존 coverage 엔진(+ tree-sitter
    gap-fallback), 그 외는 tree-sitter call graph 1차 선별로 각각 반환한
    결과를 그대로 합친다(한 커밋에 여러 언어가 섞여 바뀌어도 각자 자기
    메커니즘으로 선별된다).
    """
    if not changed_files:
        return []

    repo_root = Path(repo_path)

    by_language: dict[str, list[str]] = defaultdict(list)
    for f in changed_files:
        spec = detect_language(f)
        lang = "python" if spec is None or spec.name == "python" else spec.name
        by_language[lang].append(f)

    relevant: set[TestId] = set()

    python_files = by_language.pop("python", [])
    if python_files:
        relevant |= _select_python(repo_root, python_files, changed_lines)

    for lang, files in by_language.items():
        relevant |= _select_via_callgraph(repo_root, lang, files, changed_lines)

    return sorted(relevant)
