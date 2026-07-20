from weld.policy.trust import decide, decide_among
from weld.types import MergeCandidate, MutationScore, VerificationResult


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


# --- decide_among: 값 충돌(스왑 테스트) 판정 ---


def _passing(candidate_id):
    return VerificationResult(candidate_id=candidate_id, compiled=True, tests_passed=True)


def test_decide_among_accepts_when_exactly_one_candidate_passes():
    candidates = [
        MergeCandidate(id="c-0", content="ours", strategy="ours-verbatim"),
        MergeCandidate(id="c-1", content="theirs", strategy="theirs-verbatim"),
    ]
    verifications = [
        _passing("c-0"),
        VerificationResult(candidate_id="c-1", compiled=True, tests_passed=False, tests_failed=["t"]),
    ]
    mutations = [
        MutationScore(candidate_id="c-0", mutants_total=0, mutants_killed=0),
        MutationScore(candidate_id="c-1", mutants_total=0, mutants_killed=0),
    ]
    decision = decide_among(candidates, verifications, mutations)
    assert decision.accepted is True
    assert decision.candidate_id == "c-0"


def test_decide_among_escalates_when_no_candidate_passes():
    candidates = [MergeCandidate(id="c-0", content="ours", strategy="ours-verbatim")]
    verifications = [
        VerificationResult(candidate_id="c-0", compiled=True, tests_passed=False, tests_failed=["t"])
    ]
    mutations = [MutationScore(candidate_id="c-0", mutants_total=0, mutants_killed=0)]
    decision = decide_among(candidates, verifications, mutations)
    assert decision.accepted is False
    assert decision.candidate_id is None


def test_decide_among_escalates_when_multiple_candidates_pass():
    """값 충돌 — ours/theirs 둘 다 통과하면(스왑해도 테스트가 구분 못 함)
    어느 쪽도 자동 채택하지 않고 에스컬레이션해야 한다."""
    candidates = [
        MergeCandidate(id="c-0", content="ours", strategy="ours-verbatim"),
        MergeCandidate(id="c-1", content="theirs", strategy="theirs-verbatim"),
    ]
    verifications = [_passing("c-0"), _passing("c-1")]
    mutations = [
        MutationScore(candidate_id="c-0", mutants_total=0, mutants_killed=0),
        MutationScore(candidate_id="c-1", mutants_total=0, mutants_killed=0),
    ]
    decision = decide_among(candidates, verifications, mutations)
    assert decision.accepted is False
    assert decision.candidate_id is None
    assert "c-0" in decision.reason and "c-1" in decision.reason
