from weld.policy.trust import decide, decide_among
from weld.types import MergeCandidate, MutationScore, VerificationResult

_CANDIDATE = MergeCandidate(id="c1", content="x = 1\n", strategy="llm-hunk-t0.2")
_VERBATIM_CANDIDATE = MergeCandidate(id="c1", content="x = 1\n", strategy="ours-verbatim")


def _passing(candidate_id="c1", tests_run=("test_x",)):
    return VerificationResult(
        candidate_id=candidate_id, compiled=True, tests_passed=True, tests_run=list(tests_run)
    )


def _no_signal(candidate_id="c1"):
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
    """tests_passed=True인데 tests_run이 비었으면 공허한 통과 — 에스컬레이션."""
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True, tests_run=[])
    decision = decide(verification, _no_signal("c1"))
    assert decision.accepted is False
    assert "실행된 테스트가 없음" in decision.reason


def test_decide_accepts_no_mutable_code_when_verbatim():
    verification = _passing()
    mutation = MutationScore(candidate_id="c1", mutants_total=0, mutants_killed=0, sites_total=0)
    decision = decide(verification, mutation, _VERBATIM_CANDIDATE)
    assert decision.accepted is True
    assert decision.candidate_id == "c1"


def test_decide_escalates_no_mutable_code_when_llm_synthesized():
    verification = _passing()
    mutation = MutationScore(candidate_id="c1", mutants_total=0, mutants_killed=0, sites_total=0)
    decision = decide(verification, mutation, _CANDIDATE)
    assert decision.accepted is False
    assert "LLM 합성" in decision.reason


def test_decide_without_candidate_is_conservative():
    """candidate 정보가 없으면(구버전 호출부) verbatim 여부를 확인할 수 없으니
    변형할 코드가 없어도 합성으로 보고 에스컬레이션한다."""
    decision = decide(_passing(), _no_signal())
    assert decision.accepted is False


def test_decide_escalates_when_sites_exist_but_all_uncovered():
    """뮤테이션 사이트는 있었는데(sites_total>0) 전부 미커버라 판정 못함(mutants_total==0)."""
    verification = _passing()
    mutation = MutationScore(
        candidate_id="c1", mutants_total=0, mutants_killed=0, sites_total=3, mutants_uncovered=3
    )
    decision = decide(verification, mutation, _CANDIDATE)
    assert decision.accepted is False
    assert "미커버" in decision.reason


def test_decide_rejects_low_mutation_score():
    verification = _passing()
    mutation = MutationScore(candidate_id="c1", mutants_total=10, mutants_killed=5, sites_total=10)
    decision = decide(verification, mutation, _CANDIDATE)
    assert decision.accepted is False


def test_decide_accepts_high_mutation_score():
    verification = _passing()
    mutation = MutationScore(candidate_id="c1", mutants_total=10, mutants_killed=9, sites_total=10)
    decision = decide(verification, mutation, _CANDIDATE)
    assert decision.accepted is True


# --- decide_among: 값 충돌(스왑 테스트) 판정 ---


def test_decide_among_accepts_when_exactly_one_candidate_passes():
    candidates = [
        MergeCandidate(id="c-0", content="ours", strategy="ours-verbatim"),
        MergeCandidate(id="c-1", content="theirs", strategy="theirs-verbatim"),
    ]
    verifications = [
        _passing("c-0"),
        VerificationResult(
            candidate_id="c-1", compiled=True, tests_passed=False, tests_failed=["t"], tests_run=["t"]
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
            candidate_id="c-0", compiled=True, tests_passed=False, tests_failed=["t"], tests_run=["t"]
        )
    ]
    mutations = [_no_signal("c-0")]
    decision = decide_among(candidates, verifications, mutations)
    assert decision.accepted is False
    assert decision.candidate_id is None


def test_decide_among_escalates_when_multiple_candidates_pass_with_different_content():
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


def test_decide_among_accepts_when_multiple_candidates_pass_with_identical_content():
    """서로 다른 전략(예: 온도 다른 LLM 호출)이 우연히 같은 내용으로 수렴하고
    각자 뮤테이션 검증까지 통과하면, "경쟁하는 후보 여럿"이 아니라 하나로
    취급해 채택해야 한다(사례 10 패턴: 온도 다른 두 호출이 같은 텍스트로 수렴)."""
    candidates = [
        MergeCandidate(id="c-0", content="merged", strategy="llm-hunk-t0.2"),
        MergeCandidate(id="c-1", content="merged", strategy="llm-hunk-t0.7"),
    ]
    verifications = [_passing("c-0"), _passing("c-1")]
    mutations = [
        MutationScore(candidate_id="c-0", mutants_total=6, mutants_killed=5, sites_total=6),
        MutationScore(candidate_id="c-1", mutants_total=6, mutants_killed=5, sites_total=6),
    ]
    decision = decide_among(candidates, verifications, mutations)
    assert decision.accepted is True
    assert decision.candidate_id == "c-0"
