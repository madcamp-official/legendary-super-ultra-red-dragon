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


def test_build_escalation_report_shows_diff_against_base_not_full_file():
    """리포트는 파일 전체를 덤프하지 않고 base 대비 diff만 보여줘야 한다 —
    안 바뀐 줄까지 사람이 스크롤하게 만들면 안 됨(큰 파일일수록 중요)."""
    base = "line1\nline2\nline3\nline4\nline5\n"
    candidate = MergeCandidate(
        id="c1", content="line1\nline2\nCHANGED\nline4\nline5\n", strategy="llm-hunk-t0.2"
    )
    verification = VerificationResult(candidate_id="c1", compiled=True, tests_passed=True)
    mutation = MutationScore(candidate_id="c1", mutants_total=0, mutants_killed=0)
    report = EscalationReport(
        intent_summary="요약",
        candidates=[candidate],
        verifications=[verification],
        mutation_scores=[mutation],
    )
    output = build_escalation_report(report, base)
    assert "CHANGED" in output
    assert "-line3" in output
    assert "+CHANGED" in output
    # 안 바뀐 줄(line1)이 diff 컨텍스트로는 나올 수 있어도, 파일 전체 덤프처럼
    # 그대로 반복 출력되진 않는지 diff 마커(@@)로 확인.
    assert "@@" in output
