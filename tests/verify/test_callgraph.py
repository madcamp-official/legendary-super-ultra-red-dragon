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


# --- C/C++ 테스트 파일 인식 (파일 이름/디렉터리 관례) ---


def test_is_test_file_by_name():
    for good in (
        "tests/test_mean.c", "src/foo_test.c", "test-foo.cpp",
        "test/mean.cc", "a/b/foo_unittest.cpp", "x_spec.cxx",
    ):
        assert cg._is_test_file_by_name(good), good
    # 구분자 없는 test* 접두는 일부러 안 잡는다(latest/contest 오탐 방지).
    for bad in ("src/mean.c", "lib/foo.cpp", "src/latest.c", "contest.c", "TestFoo.cpp"):
        assert not cg._is_test_file_by_name(bad), bad


@needs_ts
def test_c_test_file_recognized_and_reaches_source(tmp_path):
    """C는 함수 이름 컨벤션이 없어 파일 관례로 테스트를 인식한다 —
    demo(tests/test_mean.c: main이 assert로 mean() 검증)와 같은 형태."""
    _write(
        tmp_path,
        "src/mean.c",
        "int mean(const int *xs, int n) {\n    return xs[0] / n;\n}\n",
    )
    _write(
        tmp_path,
        "tests/test_mean.c",
        "#include <assert.h>\nint mean(const int *xs, int n);\n"
        "int main(void) {\n    int a[] = {6};\n    assert(mean(a, 1) == 6);\n    return 0;\n}\n",
    )

    graph = cg.build_or_load_graph(tmp_path, {"c"})

    # 테스트 파일 안의 함수(main)가 테스트 노드로 인식된다.
    assert graph.test_nodes_by_lang.get("c") == {"tests/test_mean.c::main"}
    # mean() 본문 줄에서 caller(main)를 타고 올라가 테스트에 도달.
    reached = cg.find_reachable_tests(graph, "src/mean.c", 2)
    assert reached == {"tests/test_mean.c::main"}


@needs_ts
def test_cpp_plain_test_file_recognized_alongside_macros(tmp_path):
    """C++ 소박한 assert-in-main 테스트도 파일 관례로 인식(매크로 없이도)."""
    _write(
        tmp_path,
        "src/mean.cpp",
        "int mean(const int *xs, int n) {\n    return xs[0] / n;\n}\n",
    )
    _write(
        tmp_path,
        "tests/mean_test.cpp",
        "#include <cassert>\nint mean(const int *xs, int n);\n"
        "int main() {\n    int a[] = {6};\n    assert(mean(a, 1) == 6);\n    return 0;\n}\n",
    )

    graph = cg.build_or_load_graph(tmp_path, {"cpp"})

    assert graph.test_nodes_by_lang.get("cpp") == {"tests/mean_test.cpp::main"}
    assert cg.find_reachable_tests(graph, "src/mean.cpp", 2) == {"tests/mean_test.cpp::main"}


# --- green-verify 배치 실행 ---


def _two_js_test_files(tmp_path):
    _write(tmp_path, "src/a.js", "function a() {\n    return 1;\n}\nmodule.exports = { a };\n")
    _write(
        tmp_path,
        "src/a.test.js",
        "const { a } = require('./a');\ntest('a', () => {\n    a();\n});\n",
    )
    _write(tmp_path, "src/b.js", "function b() {\n    return 2;\n}\nmodule.exports = { b };\n")
    _write(
        tmp_path,
        "src/b.test.js",
        "const { b } = require('./b');\ntest('b', () => {\n    b();\n});\n",
    )
    return cg.build_or_load_graph(tmp_path, {"javascript"})


@needs_ts
def test_verify_relevant_tests_runs_single_batch_when_all_green(tmp_path, monkeypatch):
    graph = _two_js_test_files(tmp_path)
    qnames = set(graph.test_nodes_by_lang["javascript"])
    assert len(qnames) == 2

    calls: list[list[str]] = []

    def fake_run(repo_root, spec, rel_paths):
        calls.append(list(rel_paths))
        return True

    monkeypatch.setattr(cg, "_run_tests", fake_run)

    verified = cg.verify_relevant_tests(tmp_path, "javascript", graph, qnames)

    assert verified == qnames
    # 두 파일이 한 번의 배치로 함께 돈다(파일마다 한 번씩이 아니라).
    assert len(calls) == 1
    assert sorted(calls[0]) == ["src/a.test.js", "src/b.test.js"]


@needs_ts
def test_verify_relevant_tests_isolates_when_batch_fails(tmp_path, monkeypatch):
    graph = _two_js_test_files(tmp_path)
    qnames = set(graph.test_nodes_by_lang["javascript"])

    def fake_run(repo_root, spec, rel_paths):
        rel_paths = list(rel_paths)
        if len(rel_paths) > 1:
            return False  # 배치는 빨강 — 어느 파일 탓인지 모른다.
        return rel_paths == ["src/a.test.js"]  # 격리: a만 초록, b는 빨강.

    monkeypatch.setattr(cg, "_run_tests", fake_run)

    verified = cg.verify_relevant_tests(tmp_path, "javascript", graph, qnames)

    # b는 빨강이라 탈락, a만 남는다.
    assert verified == {q for q in qnames if q.startswith("src/a.test.js")}
