from pathlib import Path

from weld.verify.impact import select_relevant_tests


def _write(root: Path, rel_path: str, content: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_sample_repo(root: Path) -> None:
    # 패키지 이름은 "weld"가 아닌 걸로 골랐다 — 실제 설치된 weld 패키지와
    # 이름이 겹치면, 이 tmp 저장소에서 pytest를 서브프로세스로 돌릴 때 어느
    # "weld"가 임포트되는지 꼬일 수 있어서다 (verify/mutation.py 테스트가
    # "pkg"를 쓰는 것과 같은 이유).
    _write(root, "conftest.py", "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n")
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/foo.py", "def foo():\n    return 1\n")
    _write(
        root,
        "pkg/bar.py",
        "from pkg.foo import foo\n\n\ndef bar():\n    return foo() + 1\n",
    )
    _write(root, "pkg/unrelated.py", "def baz():\n    return 0\n")

    _write(
        root,
        "tests/test_foo.py",
        "from pkg.foo import foo\n\n\n"
        "def test_foo():\n    assert foo() == 1\n\n\n"
        "class TestFooClass:\n    def test_method(self):\n        assert foo() == 1\n",
    )
    _write(
        root,
        "tests/test_bar.py",
        "from pkg.bar import bar\n\n\ndef test_bar():\n    assert bar() == 2\n",
    )
    _write(
        root,
        "tests/test_unrelated.py",
        "from pkg.unrelated import baz\n\n\ndef test_baz():\n    assert baz() == 0\n",
    )


def test_select_relevant_tests_finds_direct_importer(tmp_path):
    _build_sample_repo(tmp_path)

    result = select_relevant_tests(["pkg/foo.py"], repo_path=str(tmp_path))

    assert "tests/test_foo.py::test_foo" in result
    assert "tests/test_foo.py::TestFooClass::test_method" in result


def test_select_relevant_tests_finds_transitive_importer(tmp_path):
    _build_sample_repo(tmp_path)

    # bar() 안에서 foo()를 실제로 호출하므로, foo.py가 바뀌면 커버리지상
    # test_bar도 그 줄을 지나간 테스트로 잡혀야 한다 (import 그래프가 아니라
    # 실행 여부 기준이라, "import만 하고 안 부르는" 경우는 안 잡히는 게 맞다).
    result = select_relevant_tests(["pkg/foo.py"], repo_path=str(tmp_path))

    assert "tests/test_bar.py::test_bar" in result


def test_select_relevant_tests_excludes_unrelated_tests(tmp_path):
    _build_sample_repo(tmp_path)

    result = select_relevant_tests(["pkg/foo.py"], repo_path=str(tmp_path))

    assert "tests/test_unrelated.py::test_baz" not in result


def test_select_relevant_tests_includes_changed_test_file_itself(tmp_path):
    _build_sample_repo(tmp_path)

    result = select_relevant_tests(["tests/test_unrelated.py"], repo_path=str(tmp_path))

    assert result == ["tests/test_unrelated.py::test_baz"]


def test_select_relevant_tests_empty_changed_files_returns_empty(tmp_path):
    _build_sample_repo(tmp_path)

    assert select_relevant_tests([], repo_path=str(tmp_path)) == []


def test_select_relevant_tests_narrows_by_changed_lines(tmp_path):
    _build_sample_repo(tmp_path)

    # pkg/foo.py의 "return 1" 줄(2번)만 바뀌었다고 명시하면, 그 줄을 실제로
    # 지나간 테스트만 나온다 (foo()가 호출될 때마다 실행되는 줄이라 test_foo,
    # TestFooClass.test_method, 그리고 foo()를 내부에서 부르는 test_bar까지
    # 전부 포함돼야 한다).
    result = select_relevant_tests(
        ["pkg/foo.py"], repo_path=str(tmp_path), changed_lines={"pkg/foo.py": {2}}
    )

    assert "tests/test_foo.py::test_foo" in result
    assert "tests/test_bar.py::test_bar" in result
    assert "tests/test_unrelated.py::test_baz" not in result

    # "def foo():" 줄(1번)은 임포트/컬렉션 시점에만 실행되고("" 컨텍스트라
    # 걸러짐) 어떤 테스트 실행 중에도 지나가지 않으므로, 그 줄만 바뀐 걸로
    # 좁히면 baseline엔 파일이 있어도 관련 테스트가 0개가 될 수 있다.
    result_def_line = select_relevant_tests(
        ["pkg/foo.py"], repo_path=str(tmp_path), changed_lines={"pkg/foo.py": {1}}
    )
    assert result_def_line == []


def test_select_relevant_tests_falls_back_to_all_tests_for_unmapped_file(tmp_path):
    _build_sample_repo(tmp_path)

    # baseline 실행 시점엔 없던 신규 파일 — baseline 매핑에 없으므로 좁히길
    # 포기하고 저장소 전체 테스트로 보수적 폴백한다.
    _write(tmp_path, "pkg/new_module.py", "def new_thing():\n    return 1\n")

    result = select_relevant_tests(["pkg/new_module.py"], repo_path=str(tmp_path))

    assert "tests/test_foo.py::test_foo" in result
    assert "tests/test_bar.py::test_bar" in result
    assert "tests/test_unrelated.py::test_baz" in result
