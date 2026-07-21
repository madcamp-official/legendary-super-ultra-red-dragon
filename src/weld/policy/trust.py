"""담당: 이서영

verify(컴파일+테스트)와 mutation(결함 주입 검증) 결과를 종합해 후보 하나를
자동 채택할지, 사람에게 에스컬레이션할지 최종 판정한다.

README "판정 정책" 표 그대로, 뮤테이션 신호가 0(mutants_total==0)일 때를
이유별로 나눠서 다르게 판정한다 — 전부 "검증 근거 없음"으로 뭉뚱그리면 안
되는 이유는 그 각각이 위험도가 다르기 때문이다:

1. 실행된 테스트가 없음(verification.tests_run이 빔) → 에스컬레이션.
   exit code 0은 "통과"가 아니라 "아무것도 안 돌았다"는 공허한 통과일 수 있다.
2. 뮤테이션 사이트는 있었는데(mutation.sites_total>0) 전부 미커버라 하나도
   판정 못함(mutation.mutants_total==0) → 에스컬레이션. 변경 영역을 테스트가
   아예 못 본다는 뜻이라 자동 채택하면 근거 없는 베팅이 된다.
3. 변형할 코드 자체가 없고(sites_total==0) 후보가 verbatim(ours/theirs/diff3
   원문 그대로) → 채택. 검증할 로직이 없으니 합성 리스크도 없다.
4. 변형할 코드가 없고 후보가 LLM 합성 → 에스컬레이션. 검증 근거도 없는데
   내용까지 LLM이 지어낸 것이라 더 엄격하게 본다.
5. (decide_among) 서로 다른 내용의 후보 여럿이 동시에 채택되면 → 에스컬레이션.
   테스트가 이 충돌을 구분 못 한다는 뜻(스왑 테스트가 잡으려는 것과 같은 신호).
"""

from __future__ import annotations

from weld.types import MergeCandidate, MutationScore, TrustDecision, VerificationResult

MUTATION_SCORE_THRESHOLD = 0.8


def _is_verbatim(strategy: str) -> bool:
    """LLM이 새로 쓴 게 아니라 base/ours/theirs/diff3 원문을 그대로 가져온 후보인지.

    generate.py가 이런 후보엔 전부 "-verbatim" 접미사를 붙이는 명명 규칙을 쓴다
    ("ours-verbatim", "theirs-verbatim", "diff3-verbatim").
    """
    return strategy.endswith("-verbatim")


def _reject(reason: str) -> TrustDecision:
    return TrustDecision(accepted=False, candidate_id=None, reason=reason)


def decide(
    candidate: MergeCandidate, verification: VerificationResult, mutation: MutationScore
) -> TrustDecision:
    """검증+뮤테이션 결과를 종합해 후보 하나의 채택 여부를 판정한다."""
    if not verification.compiled:
        return _reject(f"컴파일 실패: {verification.error or '알 수 없는 오류'}")

    if not verification.tests_passed:
        failed = ", ".join(verification.tests_failed) or "알 수 없음"
        return _reject(f"테스트 실패: {failed}")

    if not verification.tests_run:
        return _reject("실행된 테스트가 없음 (공허한 통과) — 에스컬레이션")

    has_mutable_code = mutation.sites_total > 0 or mutation.mutants_total > 0
    if not has_mutable_code:
        if _is_verbatim(candidate.strategy):
            return TrustDecision(
                accepted=True,
                candidate_id=verification.candidate_id,
                reason="변형할 코드 없음 + verbatim 후보 — 합성 리스크 없어 채택",
            )
        return _reject("변형할 코드 없음 + LLM 합성 후보 — 검증 근거 없이 채택하지 않음")

    if mutation.mutants_total == 0:
        return _reject(
            f"뮤테이션 사이트 {mutation.sites_total}개가 변경 영역 테스트에 전부 미커버 "
            "— 검증 근거 없음"
        )

    if mutation.score < MUTATION_SCORE_THRESHOLD:
        return _reject(f"뮤테이션 점수 미달: {mutation.score:.0%} < {MUTATION_SCORE_THRESHOLD:.0%}")

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

    다만 "몇 개 통과했나"를 세기 전에 내용(candidate.content) 기준으로 먼저
    중복 제거한다 — 서로 다른 전략(예: 온도 다른 LLM 호출 두 번)이 우연히
    같은 문자열로 수렴한 경우까지 "여러 후보가 경쟁 중"으로 오인해 에스컬레이션
    하면 안 되기 때문이다.
    """
    decisions = [
        (candidate, decide(candidate, verification, mutation))
        for candidate, verification, mutation in zip(candidates, verifications, mutation_scores)
    ]
    accepted = [(candidate, decision) for candidate, decision in decisions if decision.accepted]

    if not accepted:
        return TrustDecision(
            accepted=False,
            candidate_id=None,
            reason="게이트를 통과한 후보가 없음",
        )

    unique_contents = {candidate.content for candidate, _ in accepted}
    if len(unique_contents) == 1:
        return accepted[0][1]

    ids = ", ".join(candidate.id for candidate, _ in accepted)
    return TrustDecision(
        accepted=False,
        candidate_id=None,
        reason=(
            f"여러 후보({ids})가 동시에 통과함 — 테스트가 이 충돌을 구분하지 "
            "못한다는 뜻이라(스왑 테스트가 걸러내려는 것과 같은 신호) 에스컬레이션함"
        ),
    )
