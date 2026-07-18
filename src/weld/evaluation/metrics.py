"""평가 지표 계산 — 순수 함수. 사례/결과 리스트만 있으면 파이프라인 없이도 돈다.

기획서의 지표 4개를 그대로 옮겼다.
- 가짜 충돌 제거율        → 100% 목표
- 검증-자동 해결률        → 가변적(정상)
- 오탐률(★ 북극성)        → 0% 목표
- (실험) 커버리지 vs 자동해결률 상관관계
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from weld.evaluation.cases import EvalCase, EvalOutcome

_AUTO_ACTIONS = ("auto_spurious", "auto_verified")


def spurious_removal_rate(cases: list[EvalCase], outcomes: list[EvalOutcome]) -> float | None:
    """정답이 '가짜 충돌'인 사례 중, 파이프라인이 실제로 자동 처리한 비율. 목표 100%.

    가짜 사례가 하나도 없으면 정의되지 않으므로 None.
    """
    by_id = {o.case_id: o for o in outcomes}
    spurious_cases = [c for c in cases if c.expected_spurious]
    if not spurious_cases:
        return None
    resolved = sum(
        1
        for c in spurious_cases
        if (o := by_id.get(c.id)) is not None and o.action == "auto_spurious"
    )
    return resolved / len(spurious_cases)


def auto_resolution_rate(outcomes: list[EvalOutcome]) -> float | None:
    """전체 사례 중 파이프라인이 자동으로 해결한(가짜+검증통과) 비율."""
    if not outcomes:
        return None
    auto = sum(1 for o in outcomes if o.action in _AUTO_ACTIONS)
    return auto / len(outcomes)


def false_positive_rate(outcomes: list[EvalOutcome]) -> float | None:
    """★ 북극성. 자동 병합한 것 중 실제로 틀렸던 비율. 목표 0%.

    자동 병합이 한 건도 없으면(전부 에스컬레이션) 정의되지 않으므로 None.
    정답을 모르는(correct is None) 자동 병합은 분모에서 제외한다.
    """
    judged = [o for o in outcomes if o.action in _AUTO_ACTIONS and o.correct is not None]
    if not judged:
        return None
    wrong = sum(1 for o in judged if o.correct is False)
    return wrong / len(judged)


def escalation_rate(outcomes: list[EvalOutcome]) -> float | None:
    """사람에게 넘긴 비율."""
    if not outcomes:
        return None
    return sum(1 for o in outcomes if o.action == "escalated") / len(outcomes)


def coverage_vs_autoresolution(outcomes: list[EvalOutcome]) -> float | None:
    """저장소 커버리지와 '자동 해결됐는지(1/0)'의 점-이연 상관계수(Pearson).

    가설: 커버리지가 높은 저장소일수록 검증 게이트를 통과해 자동 해결되는
    비율이 높다(양의 상관). 커버리지가 있는 표본이 2개 미만이거나 한쪽 분산이
    0이면 상관을 정의할 수 없으므로 None.
    """
    pairs = [
        (o.repo_coverage, 1.0 if o.action in _AUTO_ACTIONS else 0.0)
        for o in outcomes
        if o.repo_coverage is not None
    ]
    if len(pairs) < 2:
        return None
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    var_x = sum((x - mx) ** 2 for x in xs)
    var_y = sum((y - my) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / math.sqrt(var_x * var_y)


@dataclass(frozen=True)
class EvalReport:
    """지표 한 묶음. None인 지표는 '표본이 없어 정의 불가'라는 뜻이다."""

    n_cases: int
    spurious_removal_rate: float | None
    auto_resolution_rate: float | None
    false_positive_rate: float | None
    escalation_rate: float | None
    coverage_vs_autoresolution: float | None


def compute_report(cases: list[EvalCase], outcomes: list[EvalOutcome]) -> EvalReport:
    """사례+결과에서 전체 지표를 한 번에 계산한다."""
    return EvalReport(
        n_cases=len(outcomes),
        spurious_removal_rate=spurious_removal_rate(cases, outcomes),
        auto_resolution_rate=auto_resolution_rate(outcomes),
        false_positive_rate=false_positive_rate(outcomes),
        escalation_rate=escalation_rate(outcomes),
        coverage_vs_autoresolution=coverage_vs_autoresolution(outcomes),
    )
