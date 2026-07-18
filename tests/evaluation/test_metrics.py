from weld.evaluation.cases import EvalCase, EvalOutcome
from weld.evaluation.metrics import (
    auto_resolution_rate,
    compute_report,
    coverage_vs_autoresolution,
    escalation_rate,
    false_positive_rate,
    spurious_removal_rate,
)


def _case(cid, *, spurious=False, coverage=None):
    return EvalCase(
        id=cid,
        base="",
        ours="",
        theirs="",
        file_path="f.py",
        expected_spurious=spurious,
        repo_coverage=coverage,
    )


def _outcome(cid, action, *, correct=None, coverage=None):
    return EvalOutcome(case_id=cid, action=action, correct=correct, repo_coverage=coverage)


def test_spurious_removal_rate_counts_only_expected_spurious_cases():
    cases = [_case("a", spurious=True), _case("b", spurious=True), _case("c", spurious=False)]
    outcomes = [
        _outcome("a", "auto_spurious"),
        _outcome("b", "escalated"),  # 가짜였는데 못 걸러냄
        _outcome("c", "auto_verified"),  # 얜 진짜 충돌 사례라 분모에서 제외
    ]
    assert spurious_removal_rate(cases, outcomes) == 0.5


def test_spurious_removal_rate_none_when_no_spurious_cases():
    cases = [_case("a", spurious=False)]
    outcomes = [_outcome("a", "auto_verified")]
    assert spurious_removal_rate(cases, outcomes) is None


def test_auto_resolution_rate_counts_both_auto_actions():
    outcomes = [
        _outcome("a", "auto_spurious"),
        _outcome("b", "auto_verified"),
        _outcome("c", "escalated"),
        _outcome("d", "error"),
    ]
    assert auto_resolution_rate(outcomes) == 0.5


def test_false_positive_rate_is_wrong_over_auto_merged():
    outcomes = [
        _outcome("a", "auto_verified", correct=True),
        _outcome("b", "auto_verified", correct=False),  # 자동 병합했는데 틀림
        _outcome("c", "auto_spurious", correct=True),
        _outcome("d", "escalated"),  # 자동 병합 아님 → 분모 제외
    ]
    # 자동 병합 3건 중 1건 오답
    assert false_positive_rate(outcomes) == 1 / 3


def test_false_positive_rate_ignores_unknown_ground_truth():
    outcomes = [
        _outcome("a", "auto_verified", correct=None),  # 정답 모름 → 제외
        _outcome("b", "auto_verified", correct=False),
    ]
    assert false_positive_rate(outcomes) == 1.0


def test_false_positive_rate_none_when_nothing_auto_merged():
    outcomes = [_outcome("a", "escalated"), _outcome("b", "error")]
    assert false_positive_rate(outcomes) is None


def test_false_positive_rate_zero_is_the_north_star_success():
    outcomes = [
        _outcome("a", "auto_verified", correct=True),
        _outcome("b", "auto_spurious", correct=True),
    ]
    assert false_positive_rate(outcomes) == 0.0


def test_escalation_rate():
    outcomes = [
        _outcome("a", "escalated"),
        _outcome("b", "escalated"),
        _outcome("c", "auto_verified"),
        _outcome("d", "error"),
    ]
    assert escalation_rate(outcomes) == 0.5


def test_coverage_vs_autoresolution_positive_correlation():
    # 커버리지 높을수록 자동 해결 → 양의 상관
    outcomes = [
        _outcome("a", "auto_verified", coverage=0.9),
        _outcome("b", "auto_verified", coverage=0.8),
        _outcome("c", "escalated", coverage=0.2),
        _outcome("d", "escalated", coverage=0.1),
    ]
    r = coverage_vs_autoresolution(outcomes)
    assert r is not None
    assert r > 0.9


def test_coverage_vs_autoresolution_none_when_insufficient_or_no_variance():
    # 커버리지 있는 표본 1개 → None
    assert coverage_vs_autoresolution([_outcome("a", "auto_verified", coverage=0.5)]) is None
    # 결과가 전부 자동 해결(y 분산 0) → None
    outcomes = [
        _outcome("a", "auto_verified", coverage=0.9),
        _outcome("b", "auto_verified", coverage=0.1),
    ]
    assert coverage_vs_autoresolution(outcomes) is None


def test_compute_report_aggregates_all_metrics():
    cases = [_case("a", spurious=True, coverage=0.9), _case("b", spurious=False, coverage=0.2)]
    outcomes = [
        _outcome("a", "auto_spurious", correct=True, coverage=0.9),
        _outcome("b", "escalated", coverage=0.2),
    ]
    report = compute_report(cases, outcomes)
    assert report.n_cases == 2
    assert report.spurious_removal_rate == 1.0
    assert report.auto_resolution_rate == 0.5
    assert report.escalation_rate == 0.5
    # 자동 병합 1건이 정답 → 오탐률 0
    assert report.false_positive_rate == 0.0
