import pytest

from weld.types import MergeCandidate
from weld.verify.mutation import compute_mutation_score


def test_compute_mutation_score_is_not_implemented_yet():
    candidate = MergeCandidate(id="c1", content="")
    with pytest.raises(NotImplementedError):
        compute_mutation_score(candidate, relevant_tests=[], repo_path=".")
