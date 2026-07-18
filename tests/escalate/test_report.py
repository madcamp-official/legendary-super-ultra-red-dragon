from weld.escalate.report import build_escalation_report
from weld.types import EscalationReport, MergeCandidate, MutationScore, VerificationResult


def test_build_escalation_report_handles_empty_candidates():
    report = EscalationReport(
        intent_summary="", candidates=[], verifications=[], mutation_scores=[]
    )
    output = build_escalation_report(report)
    assert "자동 병합 실패" in output
    assert "생성된 후보 없음" in output


def test_build_escalation_report_includes_intent_summary():
    report = EscalationReport(
        intent_summary="양쪽 다 로깅 함수를 수정함",
        candidates=[],
        verifications=[],
        mutation_scores=[],
    )
    output = build_escalation_report(report)
    assert "양쪽 다 로깅 함수를 수정함" in output


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
    assert "merged content" in output
    assert "m1" in output
    assert "50%" in output
