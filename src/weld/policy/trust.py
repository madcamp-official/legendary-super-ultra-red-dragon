"""담당: 이서영 / mutants_total==0 이유별 분기: 김민재

verify(컴파일+테스트)와 mutation(결함 주입 검증) 결과를 종합해 후보 하나를
자동 채택할지, 사람에게 에스컬레이션할지 최종 판정한다.

mutants_total==0은 "뮤테이션 통과"가 아니라 "뮤테이션이 아무 신호도 못 줬다"는
뜻이다. 예전엔 이 경우 뮤테이션 검사를 통째로 건너뛰고 채택해서, 검증 근거가
전혀 없는 후보(예: 테스트가 안 지나가는 버전 문자열 변경)도 "뮤테이션 점수
충족"으로 포장됐다. 지금은 0이 나온 이유(MutationScore.sites_total /
mutants_uncovered / VerificationResult.tests_run)에 따라 갈라 판정한다:

  - 실행된 테스트가 아예 없음            → 에스컬레이션 (검증 근거 없음)
  - 사이트는 있는데 테스트가 커버 안 함   → 에스컬레이션 (변경 영역 미커버)
  - 변형할 코드 자체가 없음 + verbatim   → 채택 (합성 리스크 없음, 테스트 통과)
  - 변형할 코드 자체가 없음 + LLM 합성   → 에스컬레이션 (합성 코드인데 근거 없음)
"""

from __future__ import annotations

from weld.types import MergeCandidate, MutationScore, TrustDecision, VerificationResult

MUTATION_SCORE_THRESHOLD = 0.8


def decide(
    verification: VerificationResult,
    mutation: MutationScore,
    candidate: MergeCandidate | None = None,
) -> TrustDecision:
    """검증+뮤테이션 결과를 종합해 채택 여부를 판정한다.

    candidate가 주어지면 mutants_total==0일 때 후보의 strategy(verbatim인지
    LLM 합성인지)까지 반영한다. 안 주어지면(구버전 호출부) 합성으로 보고
    보수적으로 판정한다.
    """
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

    if not verification.tests_run:
        return TrustDecision(
            accepted=False,
            candidate_id=None,
            reason="실행된 테스트가 없음 — '통과'가 공허해서 검증 근거가 없음, 에스컬레이션",
        )

    if mutation.mutants_total > 0:
        if mutation.score < MUTATION_SCORE_THRESHOLD:
            return TrustDecision(
                accepted=False,
                candidate_id=None,
                reason=f"뮤테이션 점수 미달: {mutation.score:.0%} < {MUTATION_SCORE_THRESHOLD:.0%}",
            )
        return TrustDecision(
            accepted=True,
            candidate_id=verification.candidate_id,
            reason=(
                f"컴파일+테스트 통과, 뮤테이션 검증 통과 "
                f"({mutation.mutants_killed}/{mutation.mutants_total} kill)"
            ),
        )

    # ---- mutants_total == 0: 이유별 분기 (모듈 docstring의 표와 동일) ----

    if mutation.sites_total > 0:
        # 변형할 코드는 있었는데 판정된 뮤턴트가 0 — 테스트가 변경 영역을
        # 지나가지 않았다는 뜻(uncovered). 테스트가 못 보는 코드를 자동
        # 병합하면 안 된다.
        return TrustDecision(
            accepted=False,
            candidate_id=None,
            reason=(
                f"변경 영역을 지나가는 테스트 없음 (뮤테이션 사이트 "
                f"{mutation.sites_total}개 중 판정 0개, 미커버 "
                f"{mutation.mutants_uncovered}개) — 에스컬레이션"
            ),
        )

    # sites_total == 0: 변경 영역에 변형 가능한 코드 자체가 없음 (버전 문자열,
    # 주석/독스트링만 바뀐 경우 등). verbatim(한쪽 그대로)은 합성 리스크가
    # 없으니 테스트 통과만으로 채택하고, LLM이 지어낸 코드는 뮤테이션 근거
    # 없이 믿지 않는다.
    if candidate is not None and candidate.strategy.endswith("verbatim"):
        return TrustDecision(
            accepted=True,
            candidate_id=verification.candidate_id,
            reason=(
                "컴파일+테스트 통과 — 변경 영역에 변형 가능한 코드가 없어 "
                "뮤테이션은 해당 없음, verbatim 후보(합성 리스크 없음)라 채택"
            ),
        )

    return TrustDecision(
        accepted=False,
        candidate_id=None,
        reason=(
            "LLM 합성 후보인데 뮤테이션이 아무 신호도 못 줌 (변형 가능한 "
            "코드 없음) — 합성 코드를 근거 없이 믿지 않고 에스컬레이션"
        ),
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
        (candidate, decide(verification, mutation, candidate))
        for candidate, verification, mutation in zip(candidates, verifications, mutation_scores)
    ]
    accepted = [(candidate, decision) for candidate, decision in decisions if decision.accepted]

    # 내용이 같은 후보는 하나로 센다 — 온도만 다른 LLM 후보 둘이 같은 병합
    # 결과에 수렴해 둘 다 통과하는 건 "서로 모순되는 후보가 여럿 통과"가
    # 아니라 합의라서, 스왑 테스트가 걸러내려는 신호가 아니다.
    unique_accepted: dict[str, tuple[MergeCandidate, TrustDecision]] = {}
    for candidate, decision in accepted:
        unique_accepted.setdefault(candidate.content.strip(), (candidate, decision))
    accepted = list(unique_accepted.values())

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
