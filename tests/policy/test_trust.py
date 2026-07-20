from weld.policy.trust import decide, decide_among
from weld.types import MergeCandidate, MutationScore, VerificationResult


def _passing(candidate_id):
    return VerificationResult(
        candidate_id=candidate_id, compiled=True, tests_passed=True, tests_run=["t1"]
    )


def _no_signal(candidate_id):
    """뮤테이션이 아무 신호도 못 준 결과 (사이트 자체가 없음)."""
    return MutationScore(candidate_id=candidate_id, mutants_total=0, mutants_killed=0)


def test_decide_rejects_compile_failure():
    verification = VerificationResult(
        candidate_id="c1", compiled=False, tests_passed=False, error="syntax error"
    )
    decision = decide(verification, _no_signal("c1"))
    assert decision.accepted is False
    assert decision.candidate_id is None


def test_decide_rejects_test_failure():
    verification = VerificationResult(
        candidate_id="c1", compiled=True, tests_passed=False, tests_failed=["test_x"]
    )
    decision = decide(verification, _no_signal("c1"))
    assert decision.accepted is False


def test_decide_escalates_when_no_tests_ran():
    """테스트가 하나도 실행 안 됐으면 '통과'는 공허하다 — 채택 근거가 없다."""
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True)
    decision = decide(verification, _no_signal("c1"))
    assert decision.accepted is False
    assert "실행된 테스트가 없음" in decision.reason


def test_decide_rejects_low_mutation_score():
    mutation = MutationScore(candidate_id="c1", mutants_total=10, mutants_killed=5)
    decision = decide(_passing("c1"), mutation)
    assert decision.accepted is False


def test_decide_accepts_high_mutation_score():
    mutation = MutationScore(candidate_id="c1", mutants_total=10, mutants_killed=9)
    decision = decide(_passing("c1"), mutation)
    assert decision.accepted is True
    assert decision.candidate_id == "c1"


# --- mutants_total == 0: 이유별 분기 ---


def test_decide_escalates_when_sites_exist_but_uncovered():
    """변형할 코드는 있는데 테스트가 그 줄을 안 지나감 — 미커버 코드를 자동
    병합하면 안 된다."""
    mutation = MutationScore(
        candidate_id="c1", mutants_total=0, mutants_killed=0,
        sites_total=3, mutants_uncovered=3,
    )
    decision = decide(_passing("c1"), mutation)
    assert decision.accepted is False
    assert "지나가는 테스트 없음" in decision.reason


def test_decide_accepts_verbatim_when_nothing_mutable():
    """변형 가능한 코드 자체가 없고(버전 문자열 등) 후보가 verbatim이면
    합성 리스크가 없으니 테스트 통과만으로 채택."""
    candidate = MergeCandidate(id="c1", content="x", strategy="ours-verbatim")
    decision = decide(_passing("c1"), _no_signal("c1"), candidate)
    assert decision.accepted is True


def test_decide_escalates_llm_candidate_when_nothing_mutable():
    """LLM이 합성한 코드인데 뮤테이션 근거가 전혀 없으면 믿지 않는다."""
    candidate = MergeCandidate(id="c1", content="x", strategy="llm-conservative")
    decision = decide(_passing("c1"), _no_signal("c1"), candidate)
    assert decision.accepted is False


def test_decide_without_candidate_is_conservative():
    """candidate 정보가 없으면(구버전 호출부) 합성으로 보고 에스컬레이션."""
    decision = decide(_passing("c1"), _no_signal("c1"))
    assert decision.accepted is False


# --- decide_among: 값 충돌(스왑 테스트) 판정 ---


def test_decide_among_accepts_when_exactly_one_candidate_passes():
    candidates = [
        MergeCandidate(id="c-0", content="ours", strategy="ours-verbatim"),
        MergeCandidate(id="c-1", content="theirs", strategy="theirs-verbatim"),
    ]
    verifications = [
        _passing("c-0"),
        VerificationResult(
            candidate_id="c-1", compiled=True, tests_passed=False,
            tests_failed=["t"], tests_run=["t"],
        ),
    ]
    mutations = [_no_signal("c-0"), _no_signal("c-1")]
    decision = decide_among(candidates, verifications, mutations)
    assert decision.accepted is True
    assert decision.candidate_id == "c-0"


def test_decide_among_escalates_when_no_candidate_passes():
    candidates = [MergeCandidate(id="c-0", content="ours", strategy="ours-verbatim")]
    verifications = [
        VerificationResult(
            candidate_id="c-0", compiled=True, tests_passed=False,
            tests_failed=["t"], tests_run=["t"],
        )
    ]
    mutations = [_no_signal("c-0")]
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
    mutations = [_no_signal("c-0"), _no_signal("c-1")]
    decision = decide_among(candidates, verifications, mutations)
    assert decision.accepted is False
    assert decision.candidate_id is None
    assert "c-0" in decision.reason and "c-1" in decision.reason
