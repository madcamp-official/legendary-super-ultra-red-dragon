"""담당: 이서영

verify(컴파일+테스트)와 mutation(결함 주입 검증) 결과를 종합해 후보 하나를
자동 채택할지, 사람에게 에스컬레이션할지 최종 판정한다.

지금은 최소 버전 — 컴파일+테스트 통과만 확인한다. mutants_total이 0(아직
mutation.py 미완성이거나 관련 뮤턴트가 없는 경우)이면 뮤테이션 점수는 판정에서
제외한다. 팀원A의 mutation.py가 완성되면 여기에 점수 임계값을 추가한다.
"""

from __future__ import annotations

from weld.types import MergeCandidate, MutationScore, TrustDecision, VerificationResult

MUTATION_SCORE_THRESHOLD = 0.8


def decide(verification: VerificationResult, mutation: MutationScore) -> TrustDecision:
    """검증+뮤테이션 결과를 종합해 채택 여부를 판정한다."""
    if not verification.compiled:
        return TrustDecision(
            accepted=False,
            candidate_id=None,
            reason=f"컴파일 실패: {verification.error or '알 수 없는 오류'}",
        )

    if not verification.tests_passed:
        failed = ", ".join(verification.tests_failed) or "알 수 없음"
        return TrustDecision(
            accepted=False,
            candidate_id=None,
            reason=f"테스트 실패: {failed}",
        )

    if mutation.mutants_total > 0 and mutation.score < MUTATION_SCORE_THRESHOLD:
        return TrustDecision(
            accepted=False,
            candidate_id=None,
            reason=f"뮤테이션 점수 미달: {mutation.score:.0%} < {MUTATION_SCORE_THRESHOLD:.0%}",
        )

    return TrustDecision(
        accepted=True,
        candidate_id=verification.candidate_id,
        reason="컴파일+테스트 통과, 뮤테이션 점수 충족",
    )


def decide_among(
    candidates: list[MergeCandidate],
    verifications: list[VerificationResult],
    mutation_scores: list[MutationScore],
) -> TrustDecision:
    """후보 여러 개를 한꺼번에 보고 최종 판정한다 — 값 충돌(같은 줄을 A/B가
    다르게 고쳐 A+B로 합칠 수 없는 경우)에 대한 스왑 테스트 역할을 겸한다.

    `is_value_conflict`가 감지한 값 충돌은 ours-verbatim/theirs-verbatim
    두 후보로 나뉘어 각자 독립적으로 검증+뮤테이션을 거친다. 이 둘(혹은 LLM이
    낸 후보들 중 서로 모순되는 것들)이 **동시에** 통과하면, 그건 "경쟁하는 값
    중 하나를 다른 값으로 바꿔치기해도 테스트가 못 잡아낸다"는 것과 같은
    신호다 — 즉 테스트가 이 충돌을 구분 못 한다는 뜻이라 자동 병합하면 오탐
    위험이 있다. 통과한 후보가 정확히 하나일 때만 자동 채택하고, 0개(게이트
    통과 후보 없음)나 2개 이상(서로 모순되는 후보가 여럿 통과)이면 무조건
    에스컬레이션한다 — README 판정 정책 표와 동일한 규칙.
    """
    decisions = [
        (candidate, decide(verification, mutation))
        for candidate, verification, mutation in zip(candidates, verifications, mutation_scores)
    ]
    accepted = [(candidate, decision) for candidate, decision in decisions if decision.accepted]

    if len(accepted) == 1:
        return accepted[0][1]

    if not accepted:
        return TrustDecision(
            accepted=False,
            candidate_id=None,
            reason="게이트를 통과한 후보가 없음",
        )

    ids = ", ".join(candidate.id for candidate, _ in accepted)
    return TrustDecision(
        accepted=False,
        candidate_id=None,
        reason=(
            f"여러 후보({ids})가 동시에 통과함 — 테스트가 이 충돌을 구분하지 "
            "못한다는 뜻이라(스왑 테스트가 걸러내려는 것과 같은 신호) 에스컬레이션함"
        ),
    )
