from weld.escalate.report import build_escalation_report
from weld.types import EscalationReport, MergeCandidate, MutationScore, VerificationResult


def test_build_escalation_report_handles_empty_candidates():
    report = EscalationReport(
        intent_summary="", candidates=[], verifications=[], mutation_scores=[]
    )
    output = build_escalation_report(report)
    assert "자동 병합 실패" in output
    assert "생성된 후보 없음" in output


def test_build_escalation_report_omits_intent_summary():
    """LLM 호출이 candidates/generate.py 한 곳으로 한정되어 intent_summary는
    항상 빈 값이므로, 리포트에 의도 요약 섹션 자체가 나오면 안 된다."""
    report = EscalationReport(
        intent_summary="양쪽 다 로깅 함수를 수정함",
        candidates=[],
        verifications=[],
        mutation_scores=[],
    )
    output = build_escalation_report(report)
    assert "의도 요약" not in output
    assert "양쪽 다 로깅 함수를 수정함" not in output


def test_build_escalation_report_shows_failing_candidate():
    candidate = MergeCandidate(id="c1", content="merged content", strategy="mock-ours-first")
    verification = VerificationResult(
        candidate_id="c1", compiled=True, tests_passed=False, tests_failed=["test_x"]
    )
    mutation = MutationScore(
        candidate_id="c1", mutants_total=4, mutants_killed=2, survived_mutants=["m1"]
    )
    report = EscalationReport(
        intent_summary="요약",
        candidates=[candidate],
        verifications=[verification],
        mutation_scores=[mutation],
    )
    output = build_escalation_report(report)
    assert "c1" in output
    assert "test_x" in output
    assert "50%" in output


def test_build_escalation_report_omits_diff_and_raw_survived_mutants():
    """리포트는 후보 파일 전체나 base 대비 diff를 보여주지 않고, 살아남은
    뮤턴트 원본 목록도 출력하지 않는다 — 사람이 값 판단에 필요한 건 PASS/FAIL
    상태·테스트 통과 개수·뮤테이션 점수뿐이라는 판단에 따른 것."""
    candidate = MergeCandidate(
        id="c1", content="line1\nline2\nCHANGED\nline4\nline5\n", strategy="llm-hunk-t0.2"
    )
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True)
    mutation = MutationScore(
        candidate_id="c1", mutants_total=4, mutants_killed=4, survived_mutants=[]
    )
    report = EscalationReport(
        intent_summary="요약",
        candidates=[candidate],
        verifications=[verification],
        mutation_scores=[mutation],
    )
    output = build_escalation_report(report)
    assert "CHANGED" not in output
    assert "```diff" not in output
    assert "@@" not in output


def test_build_escalation_report_suggests_test_improvement_when_score_low():
    """PASS했는데도 뮤테이션 점수가 임계값 미달이면 — 기존 테스트가 이 변경
    영역의 결함을 못 잡아낸다는 뜻이므로 — 테스트 파일 보완을 제안해야 한다."""
    candidate = MergeCandidate(id="c1", content="merged content", strategy="llm-hunk-t0.2")
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True)
    mutation = MutationScore(
        candidate_id="c1", mutants_total=20, mutants_killed=12, survived_mutants=["m1", "m2"]
    )
    report = EscalationReport(
        intent_summary="",
        candidates=[candidate],
        verifications=[verification],
        mutation_scores=[mutation],
    )
    output = build_escalation_report(report)
    assert "m1" not in output
    assert "테스트 파일을 보완" in output
