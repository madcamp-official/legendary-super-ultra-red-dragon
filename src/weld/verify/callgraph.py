"""tree-sitter 기반 다국어 정적 call graph (+ 가벼운 RTA).

verify/impact.py(python coverage 엔진)의 커버리지 도구가 없는 언어들을 위한
1차 선별 메커니즘이자, python 자체의 baseline 구멍(변경된 줄이 baseline
실행 당시엔 없던 신규 줄이라 커버리지 매핑이 없는 경우)을 메우는 보조
신호다. verify/mutation_ts.py가 mutation.py(python ast 엔진) 곁에 tree-sitter
다국어 엔진을 두는 것과 같은 배치.

핵심 아이디어:
  1. 함수/메서드 정의, 호출 사이트, "인스턴스화" 사이트(`new X()`, 구조체
     리터럴 등)를 언어별 tree-sitter 노드 타입으로 뽑는다.
  2. 직접 호출(`foo()`)은 이름으로 바로 잇는다. 메서드 호출(`obj.method()`)은
     정적으로 obj의 타입을 알 수 없으니(특히 JS/TS 덕타이핑), Rapid Type
     Analysis 아이디어를 단순화해 적용한다: 프로그램 어딘가에서 실제
     인스턴스화된 타입들 중 그 메서드 이름을 가진 것들로만 팬아웃한다(전체
     타입이 아니라 "살아있는" 타입만 후보로 좁힌다). 인스턴스화된 타입을
     하나도 못 찾으면 같은 이름의 메서드 전부로 보수적으로 넓힌다.
  3. 테스트 노드에서부터가 아니라, 바뀐 줄 → 그 줄을 감싼 함수 → 그 함수를
     부르는 caller들을 역방향으로 타고 올라가며(BFS) 테스트 노드에 닿으면
     그 테스트가 관련 있다고 본다.

네임스페이스 정규화: 모든 식별자는 `f"{rel_path}::{name}"`으로 시작한다 —
서로 다른 파일의 동명 클래스/함수가 절대 안 섞인다. RTA의 "이 메서드
이름을 가진 인스턴스화된 타입 찾기" 단계에서만 이 네임스페이스가 붙은 채로
비교하므로, 충돌 방지(식별자 단계)와 RTA의 의도적 과대추정(같은 메서드
이름을 가진 여러 타입을 다 후보로 인정하는 것)이 서로 안 섞인다.

캐시: 저장소 루트의 `.weld_cache/callgraph.json`에 파일별 content-hash와
함께 그래프 조각을 저장해 CLI 실행(프로세스) 간 재사용한다. 주의 — 이건
tree-sitter의 진짜 `tree.edit()` 바이트오프셋 증분 재파싱이 아니다. 그건
이전에 파싱된 Tree 객체가 메모리에 있어야 하는데, weld는 매 실행이 새
프로세스라 그 Tree를 넘길 방법이 없다. 그래서 "증분"의 실체는 파일
단위 해시 게이팅이다: 파일 내용이 안 바뀌었으면 그 파일만 재파싱을
건너뛴다(바뀐 파일만 다시 파싱). 전체 그래프(RTA용 인스턴스화 집합 포함)는
매 실행 조각들을 다시 합쳐 in-memory로 재구성한다 — 이 합치기 자체는
파일 재파싱이 없어 충분히 싸다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from weld.langs import LanguageSpec, _LANGUAGES

_EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".weld_cache",
}

# impact.py의 python 테스트 식별 규칙과 동일한 값 — impact.py를 그대로
# import하면 verify/__init__.py 초기화 도중 순환 참조가 생기니 상수만 복제.
_TEST_FUNC_PREFIX = "test_"
_TEST_CLASS_PREFIX = "Test"

_SPEC_BY_NAME: dict[str, LanguageSpec] = {spec.name: spec for spec in _LANGUAGES}

_CACHE_VERSION = 1


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GraphNode:
    qualified_name: str
    rel_path: str
    start_line: int
    end_line: int
    is_test: bool
    simple_name: str
    owner_type: str | None  # 메서드를 담은 타입의 namespaced qualified name. 자유 함수면 None.


@dataclasses.dataclass(frozen=True)
class CallSite:
    caller_qname: str
    callee_name: str
    is_method_call: bool


@dataclasses.dataclass
class FileGraphFragment:
    """파일 하나를 파싱해서 뽑은 조각 — 캐시의 단위."""

    content_hash: str
    nodes: list[GraphNode]
    calls: list[CallSite]
    instantiations: list[str]


class CallGraph:
    """여러 FileGraphFragment를 합쳐 만든 저장소 전체 그래프. 매 실행 in-memory 재구성."""

    def __init__(self) -> None:
        self.nodes_by_qname: dict[str, GraphNode] = {}
        self.line_index: dict[tuple[str, int], str] = {}
        self.reverse_edges: dict[str, set[str]] = defaultdict(set)
        self.test_nodes_by_file: dict[str, set[str]] = defaultdict(set)
        self.test_nodes_by_lang: dict[str, set[str]] = defaultdict(set)


def _qname(rel_path: str, name: str) -> str:
    return f"{rel_path}::{name}"


def _method_qname(rel_path: str, owner: str | None, name: str) -> str:
    return f"{rel_path}::{owner}.{name}" if owner else f"{rel_path}::{name}"


# ---------------------------------------------------------------------------
# tree-sitter 노드 탐색 공용 헬퍼
# ---------------------------------------------------------------------------


def _iter_all(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _ancestor(node, types: set[str]):
    current = node.parent
    while current is not None:
        if current.type in types:
            return current
        current = current.parent
    return None


def _span(node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 언어별 추출 — 함수 정의 / 호출 / 인스턴스화 노드 타입은 실제 grammar를
# 파싱해서 확인한 값이다(추측 아님).
# ---------------------------------------------------------------------------


def _extract_python(rel_path: str, source: bytes, root):
    nodes: list[GraphNode] = []
    calls: list[CallSite] = []
    instantiations: list[str] = []
    qname_by_id: dict[int, str] = {}
    all_nodes = list(_iter_all(root))

    for node in all_nodes:
        if node.type != "function_definition":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        owner_node = _ancestor(node, {"class_definition"})
        owner = None
        if owner_node is not None:
            owner_name = owner_node.child_by_field_name("name")
            owner = _text(owner_name, source) if owner_name is not None else None
        start, end = _span(node)
        qname = _method_qname(rel_path, owner, name)
        is_test = (
            name.startswith(_TEST_FUNC_PREFIX)
            if owner is None
            else owner.startswith(_TEST_CLASS_PREFIX) and name.startswith(_TEST_FUNC_PREFIX)
        )
        nodes.append(
            GraphNode(
                qname, rel_path, start, end, is_test, name,
                _qname(rel_path, owner) if owner else None,
            )
        )
        qname_by_id[node.id] = qname

    for node in all_nodes:
        if node.type != "call":
            continue
        callee = node.child_by_field_name("function")
        if callee is None:
            continue
        if callee.type == "identifier":
            callee_name, is_method = _text(callee, source), False
        elif callee.type == "attribute":
            attr = callee.child_by_field_name("attribute")
            if attr is None:
                continue
            callee_name, is_method = _text(attr, source), True
        else:
            continue

        enclosing = _ancestor(node, {"function_definition"})
        caller_qname = qname_by_id.get(enclosing.id) if enclosing is not None else None
        if caller_qname is not None:
            calls.append(CallSite(caller_qname, callee_name, is_method))

        if not is_method and callee_name[:1].isupper():
            instantiations.append(_qname(rel_path, callee_name))

    return nodes, calls, instantiations


_JS_TEST_CALL_NAMES = {"test", "it"}
_JS_CALLBACK_TYPES = {"arrow_function", "function_expression"}


def _js_call_info(node, source: bytes) -> tuple[str, bool] | None:
    callee = node.child_by_field_name("function")
    if callee is None:
        return None
    if callee.type == "identifier":
        return _text(callee, source), False
    if callee.type == "member_expression":
        prop = callee.child_by_field_name("property")
        if prop is None:
            return None
        return _text(prop, source), True
    return None


def _extract_js(rel_path: str, source: bytes, root):
    """javascript/typescript 공용 — 두 grammar가 구조적으로 동일하다."""
    nodes: list[GraphNode] = []
    calls: list[CallSite] = []
    instantiations: list[str] = []
    qname_by_id: dict[int, str] = {}
    all_nodes = list(_iter_all(root))

    for node in all_nodes:
        if node.type not in ("function_declaration", "method_definition"):
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        owner = None
        if node.type == "method_definition":
            owner_node = _ancestor(node, {"class_declaration"})
            if owner_node is not None:
                owner_name = owner_node.child_by_field_name("name")
                owner = _text(owner_name, source) if owner_name is not None else None
        start, end = _span(node)
        qname = _method_qname(rel_path, owner, name)
        nodes.append(
            GraphNode(
                qname, rel_path, start, end, False, name,
                _qname(rel_path, owner) if owner else None,
            )
        )
        qname_by_id[node.id] = qname

    # node:test 컨벤션의 test(...)/it(...) 콜백 — 이름 있는 함수가 아니라
    # 익명 함수라 위 루프에 안 잡히니 따로 찾아 합성 테스트 노드로 등록한다.
    test_call_ids: set[int] = set()
    for node in all_nodes:
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None or callee.type != "identifier":
            continue
        if _text(callee, source) not in _JS_TEST_CALL_NAMES:
            continue
        args = node.child_by_field_name("arguments")
        if args is None:
            continue
        callback = next((a for a in args.children if a.type in _JS_CALLBACK_TYPES), None)
        if callback is None:
            continue
        label_node = next((a for a in args.children if a.type in ("string", "template_string")), None)
        label = _text(label_node, source).strip("'\"`") if label_node is not None else "anonymous"
        start, end = _span(node)
        qname = _qname(rel_path, f"test:{label}")
        nodes.append(GraphNode(qname, rel_path, start, end, True, label, None))
        qname_by_id[node.id] = qname
        test_call_ids.add(node.id)

        for inner in _iter_all(callback):
            if inner.type != "call_expression":
                continue
            info = _js_call_info(inner, source)
            if info is not None:
                calls.append(CallSite(qname, info[0], info[1]))

    for node in all_nodes:
        if node.type != "call_expression" or node.id in test_call_ids:
            continue
        info = _js_call_info(node, source)
        if info is None:
            continue
        enclosing = _ancestor(node, {"function_declaration", "method_definition"})
        caller_qname = qname_by_id.get(enclosing.id) if enclosing is not None else None
        if caller_qname is not None:
            calls.append(CallSite(caller_qname, info[0], info[1]))

    for node in all_nodes:
        if node.type != "new_expression":
            continue
        ctor = node.child_by_field_name("constructor")
        if ctor is not None and ctor.type == "identifier":
            instantiations.append(_qname(rel_path, _text(ctor, source)))

    return nodes, calls, instantiations


def _go_receiver_type(receiver, source: bytes) -> str | None:
    for child in receiver.children:
        if child.type != "parameter_declaration":
            continue
        type_node = child.child_by_field_name("type")
        if type_node is None:
            continue
        if type_node.type == "pointer_type" and type_node.children:
            type_node = type_node.children[-1]
        if type_node.type == "type_identifier":
            return _text(type_node, source)
    return None


def _go_single_param(func_node) -> bool:
    params = func_node.child_by_field_name("parameters")
    if params is None:
        return False
    return sum(1 for c in params.children if c.type == "parameter_declaration") == 1


def _extract_go(rel_path: str, source: bytes, root):
    nodes: list[GraphNode] = []
    calls: list[CallSite] = []
    instantiations: list[str] = []
    qname_by_id: dict[int, str] = {}
    all_nodes = list(_iter_all(root))

    for node in all_nodes:
        if node.type not in ("function_declaration", "method_declaration"):
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        owner = None
        if node.type == "method_declaration":
            receiver = node.child_by_field_name("receiver")
            owner = _go_receiver_type(receiver, source) if receiver is not None else None
        start, end = _span(node)
        qname = _method_qname(rel_path, owner, name)
        is_test = (
            owner is None
            and name.startswith("Test")
            and len(name) > len("Test")
            and _go_single_param(node)
        )
        nodes.append(
            GraphNode(
                qname, rel_path, start, end, is_test, name,
                _qname(rel_path, owner) if owner else None,
            )
        )
        qname_by_id[node.id] = qname

    for node in all_nodes:
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None:
            continue
        if callee.type == "identifier":
            callee_name, is_method = _text(callee, source), False
        elif callee.type == "selector_expression":
            field = callee.child_by_field_name("field")
            if field is None:
                continue
            callee_name, is_method = _text(field, source), True
        else:
            continue
        enclosing = _ancestor(node, {"function_declaration", "method_declaration"})
        caller_qname = qname_by_id.get(enclosing.id) if enclosing is not None else None
        if caller_qname is not None:
            calls.append(CallSite(caller_qname, callee_name, is_method))

    for node in all_nodes:
        if node.type != "composite_literal":
            continue
        type_node = node.child_by_field_name("type")
        if type_node is not None and type_node.type == "type_identifier":
            instantiations.append(_qname(rel_path, _text(type_node, source)))

    return nodes, calls, instantiations


def _rust_has_test_attr(func_node, source: bytes) -> bool:
    sibling = func_node.prev_sibling
    while sibling is not None and sibling.type == "attribute_item":
        if "test" in _text(sibling, source):
            return True
        sibling = sibling.prev_sibling
    return False


def _extract_rust(rel_path: str, source: bytes, root):
    nodes: list[GraphNode] = []
    calls: list[CallSite] = []
    instantiations: list[str] = []
    qname_by_id: dict[int, str] = {}
    all_nodes = list(_iter_all(root))

    for node in all_nodes:
        if node.type != "function_item":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        owner_node = _ancestor(node, {"impl_item"})
        owner = None
        if owner_node is not None:
            type_node = owner_node.child_by_field_name("type")
            owner = _text(type_node, source) if type_node is not None else None
        start, end = _span(node)
        qname = _method_qname(rel_path, owner, name)
        is_test = _rust_has_test_attr(node, source)
        nodes.append(
            GraphNode(
                qname, rel_path, start, end, is_test, name,
                _qname(rel_path, owner) if owner else None,
            )
        )
        qname_by_id[node.id] = qname

    for node in all_nodes:
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None:
            continue
        if callee.type == "identifier":
            callee_name, is_method = _text(callee, source), False
        elif callee.type == "field_expression":
            field = callee.child_by_field_name("field")
            if field is None:
                continue
            callee_name, is_method = _text(field, source), True
        else:
            continue
        enclosing = _ancestor(node, {"function_item"})
        caller_qname = qname_by_id.get(enclosing.id) if enclosing is not None else None
        if caller_qname is not None:
            calls.append(CallSite(caller_qname, callee_name, is_method))

    for node in all_nodes:
        if node.type != "struct_expression":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is not None and name_node.type == "type_identifier":
            instantiations.append(_qname(rel_path, _text(name_node, source)))

    # assert_eq!(make(), 1) 같은 매크로 호출은 rust grammar가 인자를
    # call_expression이 아니라 raw token_tree로 파싱한다(매크로를 모르니
    # 당연함) — 그 안의 "식별자 바로 뒤에 '('로 시작하는 token_tree"
    # 패턴을 직접 찾아 직접 호출로 간주한다. 테스트 본문 대부분이
    # assert!/assert_eq! 한 줄이라 이게 없으면 rust는 사실상 테스트를
    # 못 찾는다. 매크로 안의 메서드 호출(obj.method())까지는 안 다룸.
    for node in all_nodes:
        if node.type != "macro_invocation":
            continue
        enclosing = _ancestor(node, {"function_item"})
        caller_qname = qname_by_id.get(enclosing.id) if enclosing is not None else None
        if caller_qname is None:
            continue
        for callee_name in _rust_macro_calls(node, source):
            calls.append(CallSite(caller_qname, callee_name, False))

    return nodes, calls, instantiations


def _rust_macro_calls(macro_node, source: bytes) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:
        children = node.children
        for i in range(len(children) - 1):
            cur, nxt = children[i], children[i + 1]
            if cur.type == "identifier" and nxt.type == "token_tree":
                if source[nxt.start_byte : nxt.start_byte + 1] == b"(":
                    found.append(_text(cur, source))
        for child in children:
            if child.type == "token_tree":
                walk(child)

    walk(macro_node)
    return found


def _java_has_test_annotation(method_node, source: bytes) -> bool:
    for child in method_node.children:
        if child.type == "modifiers":
            return "@Test" in _text(child, source)
    return False


def _extract_java(rel_path: str, source: bytes, root):
    nodes: list[GraphNode] = []
    calls: list[CallSite] = []
    instantiations: list[str] = []
    qname_by_id: dict[int, str] = {}
    all_nodes = list(_iter_all(root))

    for node in all_nodes:
        if node.type != "method_declaration":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        owner_node = _ancestor(node, {"class_declaration"})
        owner = None
        if owner_node is not None:
            owner_name = owner_node.child_by_field_name("name")
            owner = _text(owner_name, source) if owner_name is not None else None
        start, end = _span(node)
        qname = _method_qname(rel_path, owner, name)
        is_test = _java_has_test_annotation(node, source)
        nodes.append(
            GraphNode(
                qname, rel_path, start, end, is_test, name,
                _qname(rel_path, owner) if owner else None,
            )
        )
        qname_by_id[node.id] = qname

    for node in all_nodes:
        if node.type != "method_invocation":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        enclosing = _ancestor(node, {"method_declaration"})
        caller_qname = qname_by_id.get(enclosing.id) if enclosing is not None else None
        if caller_qname is not None:
            # java는 자유 함수가 없다 — 한정 없는 호출도(this.의 생략) 항상
            # 메서드 호출로 취급해 RTA(살아있는 타입) 매칭을 태운다.
            calls.append(CallSite(caller_qname, _text(name_node, source), True))

    for node in all_nodes:
        if node.type != "object_creation_expression":
            continue
        type_node = node.child_by_field_name("type")
        if type_node is not None and type_node.type == "type_identifier":
            instantiations.append(_qname(rel_path, _text(type_node, source)))

    return nodes, calls, instantiations


def _c_declarator_name(func_node):
    """function_definition의 declarator 체인(포인터/참조 반환 포함)을 타고
    내려가 실제 함수/메서드 이름 identifier를 찾는다."""
    current = func_node.child_by_field_name("declarator")
    depth = 0
    while current is not None and current.type not in ("identifier", "field_identifier") and depth < 8:
        current = current.child_by_field_name("declarator")
        depth += 1
    return current if current is not None and current.type in ("identifier", "field_identifier") else None


def _extract_c(rel_path: str, source: bytes, root):
    nodes: list[GraphNode] = []
    calls: list[CallSite] = []
    qname_by_id: dict[int, str] = {}
    all_nodes = list(_iter_all(root))

    for node in all_nodes:
        if node.type != "function_definition":
            continue
        name_node = _c_declarator_name(node)
        if name_node is None:
            continue
        name = _text(name_node, source)
        start, end = _span(node)
        qname = _qname(rel_path, name)
        # C는 표준 단위테스트 프레임워크 컨벤션이 없어 is_test 판별 불가 —
        # 항상 False. 파일/언어 단위 폴백이 자연스럽게 대신한다.
        nodes.append(GraphNode(qname, rel_path, start, end, False, name, None))
        qname_by_id[node.id] = qname

    for node in all_nodes:
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None or callee.type != "identifier":
            continue
        enclosing = _ancestor(node, {"function_definition"})
        caller_qname = qname_by_id.get(enclosing.id) if enclosing is not None else None
        if caller_qname is not None:
            calls.append(CallSite(caller_qname, _text(callee, source), False))

    return nodes, calls, []


_CPP_TEST_MACROS = {"TEST", "TEST_F", "TEST_P"}


def _collect_cpp_test_case_blocks(
    root, source: bytes, rel_path: str, nodes: list[GraphNode], calls: list[CallSite]
) -> None:
    """Catch2 `TEST_CASE("...") { ... }` — tree-sitter는 매크로를 모르니
    call_expression과 뒤따르는 compound_statement가 형제로 따로 파싱된다.
    그 형제 쌍을 직접 찾아 하나의 테스트 노드로 합성한다.

    이 body는 진짜 function_definition이 아니라서(그냥 조립된 형제 쌍) 일반
    call-extraction 루프의 `_ancestor(node, {"function_definition"})` 탐색이
    여길 못 찾는다 — JS의 익명 test 콜백과 같은 이유로, body 안의 호출은
    여기서 직접 훑어서 붙인다."""
    for node in _iter_all(root):
        if node.type not in ("translation_unit", "compound_statement"):
            continue
        children = node.children
        for i in range(len(children) - 1):
            stmt = children[i]
            if stmt.type != "expression_statement" or not stmt.children:
                continue
            call = stmt.children[0]
            if call.type != "call_expression":
                continue
            callee = call.child_by_field_name("function")
            if callee is None or _text(callee, source) != "TEST_CASE":
                continue
            body = children[i + 1]
            if body.type != "compound_statement":
                continue
            args = call.child_by_field_name("arguments")
            label = _text(args, source).strip("()") if args is not None else "TEST_CASE"
            start = stmt.start_point[0] + 1
            end = body.end_point[0] + 1
            qname = _qname(rel_path, f"TEST_CASE:{label}")
            nodes.append(GraphNode(qname, rel_path, start, end, True, label, None))

            for inner in _iter_all(body):
                if inner.type != "call_expression":
                    continue
                inner_callee = inner.child_by_field_name("function")
                if inner_callee is None:
                    continue
                if inner_callee.type == "identifier":
                    calls.append(CallSite(qname, _text(inner_callee, source), False))
                elif inner_callee.type == "field_expression":
                    field = inner_callee.child_by_field_name("field")
                    if field is not None:
                        calls.append(CallSite(qname, _text(field, source), True))


def _extract_cpp(rel_path: str, source: bytes, root):
    nodes: list[GraphNode] = []
    calls: list[CallSite] = []
    instantiations: list[str] = []
    qname_by_id: dict[int, str] = {}
    all_nodes = list(_iter_all(root))

    for node in all_nodes:
        if node.type != "function_definition":
            continue
        name_node = _c_declarator_name(node)
        if name_node is None:
            continue
        raw_name = _text(name_node, source)
        start, end = _span(node)

        if raw_name in _CPP_TEST_MACROS:
            declarator = node.child_by_field_name("declarator")
            params = declarator.child_by_field_name("parameters") if declarator is not None else None
            parts = (
                [_text(p, source) for p in params.children if p.type == "parameter_declaration"]
                if params is not None
                else []
            )
            label = ".".join(parts) if parts else raw_name
            qname = _qname(rel_path, f"{raw_name}:{label}")
            nodes.append(GraphNode(qname, rel_path, start, end, True, label, None))
            qname_by_id[node.id] = qname
            continue

        owner_node = _ancestor(node, {"class_specifier", "struct_specifier"})
        owner = None
        if owner_node is not None:
            owner_name = owner_node.child_by_field_name("name")
            owner = _text(owner_name, source) if owner_name is not None else None
        qname = _method_qname(rel_path, owner, raw_name)
        nodes.append(
            GraphNode(
                qname, rel_path, start, end, False, raw_name,
                _qname(rel_path, owner) if owner else None,
            )
        )
        qname_by_id[node.id] = qname

    _collect_cpp_test_case_blocks(root, source, rel_path, nodes, calls)

    for node in all_nodes:
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None:
            continue
        if callee.type == "identifier":
            callee_name, is_method = _text(callee, source), False
        elif callee.type == "field_expression":
            field = callee.child_by_field_name("field")
            if field is None:
                continue
            callee_name, is_method = _text(field, source), True
        else:
            continue
        enclosing = _ancestor(node, {"function_definition"})
        caller_qname = qname_by_id.get(enclosing.id) if enclosing is not None else None
        if caller_qname is not None:
            calls.append(CallSite(caller_qname, callee_name, is_method))

    for node in all_nodes:
        if node.type != "new_expression":
            continue
        type_node = node.child_by_field_name("type")
        if type_node is not None and type_node.type == "type_identifier":
            instantiations.append(_qname(rel_path, _text(type_node, source)))

    return nodes, calls, instantiations


_EXTRACTORS = {
    "python": _extract_python,
    "javascript": _extract_js,
    "typescript": _extract_js,
    "go": _extract_go,
    "rust": _extract_rust,
    "java": _extract_java,
    "c": _extract_c,
    "cpp": _extract_cpp,
}


# ---------------------------------------------------------------------------
# 캐시 I/O + 그래프 빌드
# ---------------------------------------------------------------------------


def _repo_cache_path(repo_root: Path) -> Path:
    return repo_root / ".weld_cache" / "callgraph.json"


def _content_hash(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def _fragment_to_json(frag: FileGraphFragment) -> dict:
    return {
        "content_hash": frag.content_hash,
        "nodes": [dataclasses.asdict(n) for n in frag.nodes],
        "calls": [dataclasses.asdict(c) for c in frag.calls],
        "instantiations": list(frag.instantiations),
    }


def _fragment_from_json(data: dict) -> FileGraphFragment:
    return FileGraphFragment(
        content_hash=data["content_hash"],
        nodes=[GraphNode(**n) for n in data["nodes"]],
        calls=[CallSite(**c) for c in data["calls"]],
        instantiations=list(data["instantiations"]),
    )


def _load_cache(repo_root: Path) -> dict[str, dict]:
    try:
        data = json.loads(_repo_cache_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _save_cache(repo_root: Path, files: dict[str, dict]) -> None:
    path = _repo_cache_path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": _CACHE_VERSION, "files": files}), encoding="utf-8")
    except OSError:
        pass  # 캐시 저장 실패해도 기능엔 영향 없음 — 다음 실행이 그냥 다시 파싱한다.


def _iter_language_files(repo_root: Path, spec: LanguageSpec) -> list[Path]:
    files: list[Path] = []
    for ext in spec.extensions:
        files.extend(
            p for p in repo_root.rglob(f"*{ext}") if not _EXCLUDED_DIR_NAMES.intersection(p.parts)
        )
    return files


def _extract_fragment(
    lang: str, ts_language: str, rel_path: str, source: bytes, content_hash: str
) -> FileGraphFragment:
    from tree_sitter_language_pack import get_parser

    tree = get_parser(ts_language).parse(source)
    nodes, calls, instantiations = _EXTRACTORS[lang](rel_path, source, tree.root_node)
    return FileGraphFragment(content_hash, nodes, calls, instantiations)


def build_or_load_graph(repo_root: Path, languages: set[str]) -> CallGraph:
    """요청된 언어들의 call graph를 만든다. 파일별 content-hash가 캐시와
    같으면 재파싱을 건너뛴다(모듈 docstring의 "증분" 절 참고)."""
    cache = _load_cache(repo_root)
    updated = dict(cache)
    fragments: dict[str, FileGraphFragment] = {}
    lang_of: dict[str, str] = {}
    dirty = False

    for lang in languages:
        spec = _SPEC_BY_NAME.get(lang)
        if spec is None or spec.ts_language is None or lang not in _EXTRACTORS:
            continue
        for path in _iter_language_files(repo_root, spec):
            rel_path = path.relative_to(repo_root).as_posix()
            try:
                source = path.read_bytes()
            except OSError:
                continue
            content_hash = _content_hash(source)
            lang_of[rel_path] = lang

            cached_entry = cache.get(rel_path)
            if cached_entry and cached_entry.get("content_hash") == content_hash:
                try:
                    fragments[rel_path] = _fragment_from_json(cached_entry)
                    continue
                except (KeyError, TypeError):
                    pass  # 캐시 손상 — 재파싱으로 폴백

            try:
                fragment = _extract_fragment(lang, spec.ts_language, rel_path, source, content_hash)
            except Exception:
                # tree-sitter 미설치/문법 로드 실패/파싱 불가 — 이 파일은
                # "노드 없음"으로 취급(다른 언어/파일은 계속 처리).
                fragment = FileGraphFragment(content_hash, [], [], [])
            fragments[rel_path] = fragment
            updated[rel_path] = _fragment_to_json(fragment)
            dirty = True

    if dirty:
        _save_cache(repo_root, updated)

    return _build_graph(fragments, lang_of)


def _build_graph(fragments: dict[str, FileGraphFragment], lang_of: dict[str, str]) -> CallGraph:
    graph = CallGraph()
    all_nodes = [n for frag in fragments.values() for n in frag.nodes]

    for node in all_nodes:
        graph.nodes_by_qname[node.qualified_name] = node
        if node.is_test:
            graph.test_nodes_by_file[node.rel_path].add(node.qualified_name)
            lang = lang_of.get(node.rel_path)
            if lang:
                graph.test_nodes_by_lang[lang].add(node.qualified_name)

    # 넓은(바깥) 범위부터 채우고 좁은(안쪽) 범위로 덮어써서, 중첩 함수의
    # 줄은 항상 가장 안쪽 함수가 line_index를 갖도록 한다.
    for node in sorted(all_nodes, key=lambda n: n.end_line - n.start_line, reverse=True):
        for ln in range(node.start_line, node.end_line + 1):
            graph.line_index[(node.rel_path, ln)] = node.qualified_name

    live_types = {t for frag in fragments.values() for t in frag.instantiations}
    methods_by_simple: dict[str, list[str]] = defaultdict(list)
    free_by_simple: dict[str, list[str]] = defaultdict(list)
    for node in all_nodes:
        if node.owner_type is not None:
            methods_by_simple[node.simple_name].append(node.qualified_name)
        else:
            free_by_simple[node.simple_name].append(node.qualified_name)

    for frag in fragments.values():
        for call in frag.calls:
            if call.is_method_call:
                candidates = methods_by_simple.get(call.callee_name, [])
                # RTA: "실제 인스턴스화된" 타입의 메서드로만 우선 좁히고,
                # 그런 타입을 못 찾으면(추적 못한 인스턴스화 등) 같은 이름의
                # 메서드 전부로 보수적으로 되돌아간다.
                live = [q for q in candidates if graph.nodes_by_qname[q].owner_type in live_types]
                chosen = live or candidates
            else:
                caller_rel_path = call.caller_qname.split("::", 1)[0]
                all_candidates = free_by_simple.get(call.callee_name, [])
                same_file = [q for q in all_candidates if q.startswith(caller_rel_path + "::")]
                chosen = same_file or all_candidates
            for callee_qname in chosen:
                graph.reverse_edges[callee_qname].add(call.caller_qname)

    return graph


# ---------------------------------------------------------------------------
# 조회 API
# ---------------------------------------------------------------------------


def climb_callers(graph: CallGraph, start_qname: str, *, max_depth: int = 12):
    """start_qname에서 reverse_edges를 BFS로 타고 올라가며 만나는 모든
    qualified_name을 세대 순서로 yield한다(자기 자신부터). 호출자가 원하는
    정지 조건을 직접 검사하도록 제너레이터로 둔다."""
    visited = {start_qname}
    frontier = [start_qname]
    depth = 0
    while frontier and depth < max_depth:
        next_frontier: list[str] = []
        for qname in frontier:
            yield qname
            for caller in graph.reverse_edges.get(qname, ()):
                if caller not in visited:
                    visited.add(caller)
                    next_frontier.append(caller)
        frontier = next_frontier
        depth += 1


def find_reachable_tests(graph: CallGraph, rel_path: str, lineno: int, *, max_depth: int = 12) -> set[str]:
    """lineno를 감싼 함수에서 caller들을 타고 올라가 도달하는 테스트 노드
    전부. 감싸는 함수를 못 찾으면(주석/공백 줄 등) 빈 집합."""
    start = graph.line_index.get((rel_path, lineno))
    if start is None:
        return set()
    found: set[str] = set()
    for qname in climb_callers(graph, start, max_depth=max_depth):
        node = graph.nodes_by_qname.get(qname)
        if node is not None and node.is_test:
            found.add(qname)
    return found


def fallback_tests_for_file(graph: CallGraph, rel_path: str) -> set[str]:
    """해당 파일 안의 테스트 노드 전부(제한적 폴백의 1단)."""
    return set(graph.test_nodes_by_file.get(rel_path, ()))


def fallback_tests_for_language(graph: CallGraph, language: str) -> set[str]:
    """같은 언어의 저장소 전체 테스트 노드 전부(제한적 폴백의 마지막 단)."""
    return set(graph.test_nodes_by_lang.get(language, ()))
