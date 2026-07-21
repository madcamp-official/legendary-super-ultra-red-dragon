"""tree-sitter 정적 call graph + RTA 테스트 (JS/Go 픽스처).

tree-sitter-language-pack이 없는 환경에서는 skip한다(mutation_ts.py 테스트와
같은 컨벤션).
"""

from pathlib import Path

import pytest

from weld.verify import callgraph as cg

try:
    import tree_sitter_language_pack  # noqa: F401

    _TS_OK = True
except ImportError:
    _TS_OK = False

needs_ts = pytest.mark.skipif(not _TS_OK, reason="tree-sitter-language-pack 미설치")


def _write(root: Path, rel_path: str, content: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@needs_ts
def test_find_reachable_tests_resolves_direct_call_chain(tmp_path):
    _write(
        tmp_path,
        "src/foo.js",
        "function make() {\n    return 1;\n}\nmodule.exports = { make };\n",
    )
    _write(
        tmp_path,
        "src/foo.test.js",
        "const { make } = require('./foo');\n"
        "test('make works', () => {\n    make();\n});\n",
    )

    graph = cg.build_or_load_graph(tmp_path, {"javascript"})
    reached = cg.find_reachable_tests(graph, "src/foo.js", 2)  # make()의 본문 줄

    assert reached == {"src/foo.test.js::test:make works"}


@needs_ts
def test_find_reachable_tests_resolves_rta_polymorphic_dispatch(tmp_path):
    """obj.method() 처럼 정적으로 타입을 알 수 없는 호출 — RTA가 실제
    인스턴스화된 타입(new Foo())의 메서드로 팬아웃해야 한다."""
    _write(
        tmp_path,
        "src/foo.js",
        "class Foo {\n    bar() { return 1; }\n}\n"
        "function make() {\n    const f = new Foo();\n    return f.bar();\n}\n"
        "module.exports = { make };\n",
    )
    _write(
        tmp_path,
        "src/foo.test.js",
        "const { make } = require('./foo');\n"
        "test('make works', () => {\n    make();\n});\n",
    )

    graph = cg.build_or_load_graph(tmp_path, {"javascript"})
    reached = cg.find_reachable_tests(graph, "src/foo.js", 2)  # Foo.bar()의 본문 줄

    assert reached == {"src/foo.test.js::test:make works"}


@needs_ts
def test_go_receiver_method_and_test_detection(tmp_path):
    _write(
        tmp_path,
        "pkg/foo.go",
        "package pkg\n\ntype Foo struct{}\n\n"
        "func (f Foo) Bar() int { return 1 }\n\n"
        "func Make() int {\n    f := Foo{}\n    return f.Bar()\n}\n",
    )
    _write(
        tmp_path,
        "pkg/foo_test.go",
        'package pkg\n\nimport "testing"\n\n'
        "func TestMake(t *testing.T) {\n    if Make() != 1 {\n        t.Fatal(\"fail\")\n    }\n}\n",
    )

    graph = cg.build_or_load_graph(tmp_path, {"go"})

    assert graph.test_nodes_by_lang.get("go") == {"pkg/foo_test.go::TestMake"}
    reached = cg.find_reachable_tests(graph, "pkg/foo.go", 5)  # Foo.Bar()의 본문 줄
    assert reached == {"pkg/foo_test.go::TestMake"}


@needs_ts
def test_fallback_tiers_stay_within_file_then_language(tmp_path):
    _write(tmp_path, "src/orphan.js", "function orphan() {\n    return 1;\n}\n")
    _write(
        tmp_path,
        "src/other.test.js",
        "test('unrelated', () => {\n    1 + 1;\n});\n",
    )

    graph = cg.build_or_load_graph(tmp_path, {"javascript"})

    # orphan()을 부르는 caller가 없으니 도달성 선별은 빈 집합.
    assert cg.find_reachable_tests(graph, "src/orphan.js", 2) == set()
    # 그 파일 안엔 테스트가 없으니 파일 단위 폴백도 빈 집합.
    assert cg.fallback_tests_for_file(graph, "src/orphan.js") == set()
    # 마지막 단인 언어 단위 폴백만 저장소의 JS 테스트를 낸다.
    assert cg.fallback_tests_for_language(graph, "javascript") == {
        "src/other.test.js::test:unrelated"
    }


@needs_ts
def test_build_or_load_graph_skips_reparse_on_cache_hit(tmp_path, monkeypatch):
    _write(tmp_path, "src/foo.js", "function make() {\n    return 1;\n}\n")

    cg.build_or_load_graph(tmp_path, {"javascript"})
    assert (tmp_path / ".weld_cache" / "callgraph.json").exists()

    calls = []
    original = cg._extract_fragment

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(cg, "_extract_fragment", spy)
    cg.build_or_load_graph(tmp_path, {"javascript"})

    assert calls == []


@needs_ts
def test_build_or_load_graph_reparses_changed_file_only(tmp_path, monkeypatch):
    _write(tmp_path, "src/foo.js", "function make() {\n    return 1;\n}\n")
    _write(tmp_path, "src/bar.js", "function other() {\n    return 2;\n}\n")

    cg.build_or_load_graph(tmp_path, {"javascript"})

    _write(tmp_path, "src/foo.js", "function make() {\n    return 2;\n}\n")

    calls = []
    original = cg._extract_fragment

    def spy(lang, ts_language, rel_path, source, content_hash):
        calls.append(rel_path)
        return original(lang, ts_language, rel_path, source, content_hash)

    monkeypatch.setattr(cg, "_extract_fragment", spy)
    cg.build_or_load_graph(tmp_path, {"javascript"})

    assert calls == ["src/foo.js"]
