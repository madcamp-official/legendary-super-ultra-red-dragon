"""담당: 이재준

변경된 파일과 관련된 테스트만 의존성 그래프로 선별한다 (MVP: import 그래프
기반, 스트레치: call-graph 정밀화). 뮤테이션 테스팅과 병렬 검증 둘 다의
속도를 이 선별 결과가 떠받친다.

반환값은 파일명이 아니라 **개별 테스트 함수(pytest 노드 ID)** 목록이다.
import 그래프로 얻는 건 "관련 있을 후보 파일"까지고, 그 파일 안의 테스트
함수 단위까지 펼치는 건 이 함수의 책임이다 — verify/mutation.py가 "이
줄을 어떤 테스트가 실제로 실행했는지" 확인하려면 파일 단위로는 부족하다.

구현(MVP, 파일 단위 정적 분석):
1. ast로 저장소의 모든 .py 파일을 파싱해 "이 파일이 어떤 파일을 import하는가"
   그래프를 만든다 (런타임 실행/타입 추론 없이 소스만 본다 — call-graph
   정밀화는 스트레치 목표라 여기선 안 함).
2. changed_files에서 시작해 역방향(누가 이 파일을 import하는가)으로 그래프를
   훑어 "변경에 (직·간접으로) 의존하는 파일" 집합을 구한다. 변경 파일 자체도
   포함한다 — 테스트 파일이 직접 바뀐 경우 그 안의 테스트가 바로 관련 있다.
3. 그 집합에서 pytest 테스트 파일(test_*.py / *_test.py)만 걸러 ast로 다시
   열어, 모듈 최상위 test_* 함수와 Test* 클래스 안의 test_* 메서드를
   pytest 노드 ID로 펼친다.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.types import TestId

_EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}
_TEST_FUNC_PREFIX = "test_"
_TEST_CLASS_PREFIX = "Test"


def _iter_python_files(repo_root: Path) -> list[Path]:
    return [
        path
        for path in repo_root.rglob("*.py")
        if not _EXCLUDED_DIR_NAMES.intersection(path.parts)
    ]


def _module_names_for(rel_path: Path) -> list[str]:
    """파일의 저장소 상대 경로에서 가능한 dotted 모듈 이름 후보를 만든다.

    src 레이아웃(src/weld/...)이라 import는 "weld.xxx"지 "src.weld.xxx"가
    아니다. 어느 루트를 기준으로 할지 모르니 저장소 루트 기준과, 최상위
    디렉터리(src, tests 등) 하나를 벗긴 기준 둘 다 후보로 남긴다.
    """
    parts = list(rel_path.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    if not parts:
        return []

    names = {".".join(parts)}
    if len(parts) > 1:
        names.add(".".join(parts[1:]))
    return list(names)


def _imported_module_names(tree: ast.AST) -> set[str]:
    """이 파일이 import하는 dotted 이름 전부(모듈 자체 + "모듈.심볼" 형태 포함)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue  # 상대 임포트: 이 저장소 레이아웃에서 안 씀 — 스킵
            names.add(node.module)
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
    return names


def _build_import_graph(repo_root: Path) -> dict[str, set[str]]:
    """저장소 상대 경로(posix) -> 그 파일이 (직접) import하는 파일 경로 집합."""
    files = _iter_python_files(repo_root)

    module_to_file: dict[str, str] = {}
    rel_paths: dict[Path, str] = {}
    for file in files:
        rel_posix = file.relative_to(repo_root).as_posix()
        rel_paths[file] = rel_posix
        for name in _module_names_for(file.relative_to(repo_root)):
            module_to_file.setdefault(name, rel_posix)

    depends_on: dict[str, set[str]] = {rel: set() for rel in rel_paths.values()}
    for file in files:
        rel_posix = rel_paths[file]
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for imported in _imported_module_names(tree):
            # "weld.verify.impact.select_relevant_tests"처럼 심볼까지 붙은
            # 이름도 매칭되도록 dotted path를 뒤에서부터 줄여가며 찾는다.
            parts = imported.split(".")
            while parts:
                target = module_to_file.get(".".join(parts))
                if target is not None:
                    if target != rel_posix:
                        depends_on[rel_posix].add(target)
                    break
                parts.pop()

    return depends_on


def _reverse_graph(depends_on: dict[str, set[str]]) -> dict[str, set[str]]:
    """importee -> importer(그 파일을 import하는 파일들) 집합."""
    importers: dict[str, set[str]] = {rel: set() for rel in depends_on}
    for importer, importees in depends_on.items():
        for importee in importees:
            importers.setdefault(importee, set()).add(importer)
    return importers


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


def select_relevant_tests(changed_files: list[str], repo_path: str) -> list[TestId]:
    """changed_files와 의존 관계가 있는 테스트 함수의 pytest 노드 ID 목록을 반환한다."""
    if not changed_files:
        return []

    repo_root = Path(repo_path)
    depends_on = _build_import_graph(repo_root)
    importers = _reverse_graph(depends_on)

    changed = {_normalize(repo_root, f) for f in changed_files}

    relevant_files: set[str] = set(changed)
    stack = list(changed)
    while stack:
        current = stack.pop()
        for dependent in importers.get(current, ()):
            if dependent not in relevant_files:
                relevant_files.add(dependent)
                stack.append(dependent)

    test_files = sorted(f for f in relevant_files if _is_test_file(f))

    node_ids: list[TestId] = []
    for test_file in test_files:
        node_ids.extend(_test_node_ids(repo_root, test_file))

    if not node_ids and test_files:
        # ast로 개별 테스트 함수를 못 뽑아낸 경우(예: parametrize, 동적 정의) —
        # 좁히길 포기하고 관련 테스트 파일 전체를 pytest 노드ID로 돌린다.
        # 파일 경로 자체도 유효한 pytest 노드ID라 그대로 넘기면 파일 전체가 돈다.
        return test_files

    return node_ids
