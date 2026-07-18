from weld.policy.trust import decide
from weld.types import MutationScore, VerificationResult


def test_decide_rejects_compile_failure():
    verification = VerificationResult(
        candidate_id="c1", compiled=False, tests_passed=False, error="syntax error"
    )
    mutation = MutationScore(candidate_id="c1", mutants_total=0, mutants_killed=0)
    decision = decide(verification, mutation)
    assert decision.accepted is False
    assert decision.candidate_id is None


def test_decide_rejects_test_failure():
    verification = VerificationResult(
        candidate_id="c1", compiled=True, tests_passed=False, tests_failed=["test_x"]
    )
    mutation = MutationScore(candidate_id="c1", mutants_total=0, mutants_killed=0)
    decision = decide(verification, mutation)
    assert decision.accepted is False


def test_decide_accepts_when_no_mutants_and_tests_pass():
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True)
    mutation = MutationScore(candidate_id="c1", mutants_total=0, mutants_killed=0)
    decision = decide(verification, mutation)
    assert decision.accepted is True
    assert decision.candidate_id == "c1"


def test_decide_rejects_low_mutation_score():
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True)
    mutation = MutationScore(candidate_id="c1", mutants_total=10, mutants_killed=5)
    decision = decide(verification, mutation)
    assert decision.accepted is False


def test_decide_accepts_high_mutation_score():
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True)
    mutation = MutationScore(candidate_id="c1", mutants_total=10, mutants_killed=9)
    decision = decide(verification, mutation)
    assert decision.accepted is True
