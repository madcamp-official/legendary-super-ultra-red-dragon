import subprocess
from pathlib import Path

from weld.types import MergeCandidate
from weld.verify.sandbox import run_candidates_parallel, run_in_sandbox

_PASSING_FOO = "def foo():\n    return 1\n"
_FAILING_FOO = "def foo():\n    return 2\n"
_SYNTAX_ERROR_FOO = "def foo(:\n    return 1\n"
_TEST_NODE_ID = "test_foo.py::test_foo"


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> str:
    """foo.py + test_foo.py를 커밋한 임시 git 저장소를 만든다(worktree는 HEAD가 필요)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "foo.py").write_text(_PASSING_FOO, encoding="utf-8")
    (repo / "test_foo.py").write_text(
        "from foo import foo\n\n\ndef test_foo():\n    assert foo() == 1\n",
        encoding="utf-8",
    )

    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "weld-test@example.com")
    _run_git(repo, "config", "user.name", "weld-test")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "initial")
    return str(repo)


def test_run_in_sandbox_passing_candidate(tmp_path):
    repo_path = _init_repo(tmp_path)
    candidate = MergeCandidate(id="c-pass", content=_PASSING_FOO, file_path="foo.py")

    result = run_in_sandbox(candidate, repo_path=repo_path, tests=[_TEST_NODE_ID])

    assert result.compiled is True
    assert result.tests_passed is True
    assert _TEST_NODE_ID in result.tests_run
    assert result.tests_failed == []


def test_run_in_sandbox_failing_candidate(tmp_path):
    repo_path = _init_repo(tmp_path)
    candidate = MergeCandidate(id="c-fail", content=_FAILING_FOO, file_path="foo.py")

    result = run_in_sandbox(candidate, repo_path=repo_path, tests=[_TEST_NODE_ID])

    assert result.compiled is True
    assert result.tests_passed is False
    assert _TEST_NODE_ID in result.tests_failed


def test_run_in_sandbox_syntax_error_candidate(tmp_path):
    repo_path = _init_repo(tmp_path)
    candidate = MergeCandidate(id="c-syntax", content=_SYNTAX_ERROR_FOO, file_path="foo.py")

    result = run_in_sandbox(candidate, repo_path=repo_path, tests=[_TEST_NODE_ID])

    assert result.compiled is False


def test_run_candidates_parallel_empty(tmp_path):
    repo_path = _init_repo(tmp_path)

    assert run_candidates_parallel([], repo_path=repo_path) == []


def test_run_candidates_parallel_isolates_results_by_candidate_id(tmp_path):
    repo_path = _init_repo(tmp_path)
    candidates = [
        MergeCandidate(id="c-pass", content=_PASSING_FOO, file_path="foo.py"),
        MergeCandidate(id="c-fail", content=_FAILING_FOO, file_path="foo.py"),
    ]

    results = run_candidates_parallel(candidates, repo_path=repo_path, tests=[_TEST_NODE_ID])

    by_id = {r.candidate_id: r for r in results}
    assert set(by_id) == {"c-pass", "c-fail"}
    assert by_id["c-pass"].tests_passed is True
    assert by_id["c-fail"].tests_passed is False
    assert _TEST_NODE_ID in by_id["c-fail"].tests_failed
