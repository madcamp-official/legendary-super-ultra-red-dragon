import pytest

from weld.policy.trust import decide
from weld.types import MutationScore, VerificationResult


def test_decide_is_not_implemented_yet():
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True)
    mutation = MutationScore(candidate_id="c1", mutants_total=0, mutants_killed=0)
    with pytest.raises(NotImplementedError):
        decide(verification, mutation)
