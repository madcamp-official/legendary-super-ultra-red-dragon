"""담당: 이서영

verify(컴파일+테스트)와 mutation(결함 주입 검증) 결과를 종합해 후보 하나를
자동 채택할지, 사람에게 에스컬레이션할지 최종 판정한다.

지금은 최소 버전 — 컴파일+테스트 통과만 확인한다. mutants_total이 0(아직
mutation.py 미완성이거나 관련 뮤턴트가 없는 경우)이면 뮤테이션 점수는 판정에서
제외한다. 팀원A의 mutation.py가 완성되면 여기에 점수 임계값을 추가한다.
"""

from __future__ import annotations

from weld.types import MutationScore, TrustDecision, VerificationResult

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
