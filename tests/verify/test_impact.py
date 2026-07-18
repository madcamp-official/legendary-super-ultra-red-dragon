from pathlib import Path

from weld.verify.impact import select_relevant_tests


def _write(root: Path, rel_path: str, content: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_sample_repo(root: Path) -> None:
    _write(root, "src/weld/foo.py", "def foo():\n    return 1\n")
    _write(
        root,
        "src/weld/bar.py",
        "from weld.foo import foo\n\n\ndef bar():\n    return foo() + 1\n",
    )
    _write(root, "src/weld/unrelated.py", "def baz():\n    return 0\n")

    _write(
        root,
        "tests/test_foo.py",
        "from weld.foo import foo\n\n\n"
        "def test_foo():\n    assert foo() == 1\n\n\n"
        "class TestFooClass:\n    def test_method(self):\n        assert foo() == 1\n",
    )
    _write(
        root,
        "tests/test_bar.py",
        "from weld.bar import bar\n\n\ndef test_bar():\n    assert bar() == 2\n",
    )
    _write(
        root,
        "tests/test_unrelated.py",
        "from weld.unrelated import baz\n\n\ndef test_baz():\n    assert baz() == 0\n",
    )


def test_select_relevant_tests_finds_direct_importer(tmp_path):
    _build_sample_repo(tmp_path)

    result = select_relevant_tests(["src/weld/foo.py"], repo_path=str(tmp_path))

    assert "tests/test_foo.py::test_foo" in result
    assert "tests/test_foo.py::TestFooClass::test_method" in result


def test_select_relevant_tests_finds_transitive_importer(tmp_path):
    _build_sample_repo(tmp_path)

    result = select_relevant_tests(["src/weld/foo.py"], repo_path=str(tmp_path))

    assert "tests/test_bar.py::test_bar" in result


def test_select_relevant_tests_excludes_unrelated_tests(tmp_path):
    _build_sample_repo(tmp_path)

    result = select_relevant_tests(["src/weld/foo.py"], repo_path=str(tmp_path))

    assert "tests/test_unrelated.py::test_baz" not in result


def test_select_relevant_tests_includes_changed_test_file_itself(tmp_path):
    _build_sample_repo(tmp_path)

    result = select_relevant_tests(["tests/test_unrelated.py"], repo_path=str(tmp_path))

    assert result == ["tests/test_unrelated.py::test_baz"]


def test_select_relevant_tests_empty_changed_files_returns_empty(tmp_path):
    _build_sample_repo(tmp_path)

    assert select_relevant_tests([], repo_path=str(tmp_path)) == []
