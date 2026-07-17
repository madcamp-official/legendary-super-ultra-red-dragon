import pytest

from weld.types import MergeCandidate
from weld.verify.sandbox import run_candidates_parallel, run_in_sandbox


def test_run_in_sandbox_is_not_implemented_yet():
    candidate = MergeCandidate(id="c1", content="")
    with pytest.raises(NotImplementedError):
        run_in_sandbox(candidate, repo_path=".")


def test_run_candidates_parallel_is_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        run_candidates_parallel([], repo_path=".")
