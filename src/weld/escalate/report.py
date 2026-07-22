"""담당: 이서영

검증에 실패해 사람에게 넘길 때, 원시 충돌 마커만 던지지 않고 후보안(및
각 후보의 검증 결과)을 함께 담은 리포트를 만든다. 의도 요약은 LLM 호출을
후보 생성(candidates/generate.py) 한 곳으로만 한정하기로 한 팀 결정에 따라
항상 비어 있으므로 출력하지 않는다.

후보 코드는 diff나 살아남은 뮤턴트 원본 목록이 아니라 PASS/FAIL 상태·테스트
통과 개수·뮤테이션 점수로만 보여준다 — MergeCandidate.content 계약(파일
전체, types.py 참고)은 그대로 유지하되, 사람이 값 판단을 내리는 데 diff나
뮤턴트 원목록까지 필요하지는 않다는 판단에 따른 것이다. 대신 PASS했는데도
뮤테이션 점수가 임계값 미달이면(기존 테스트가 이 변경 영역의 결함을 충분히
못 잡아낸다는 뜻) 테스트 파일 보완을 제안하는 메시지를 붙인다.

실패해도 git 표준 충돌 마커는 그대로 유지된다 — 이 리포트는 stderr로 추가
제공되는 참고 정보일 뿐, 병합 자체의 유일한 정보원이 아니다.
"""

from __future__ import annotations

from weld.policy.trust import MUTATION_SCORE_THRESHOLD
from weld.types import EscalationReport, MutationScore, VerificationResult


def _render_candidate(
    index: int,
    verification: VerificationResult,
    mutation: MutationScore,
) -> list[str]:
    status = "PASS" if verification.compiled and verification.tests_passed else "FAIL"
    lines = [f"### 후보 {index}: {verification.candidate_id} [{status}]", ""]

    if not verification.compiled:
        lines.append(f"- 컴파일 실패: {verification.error or '알 수 없는 오류'}")
    elif not verification.tests_passed:
        failed = ", ".join(verification.tests_failed) or "알 수 없음"
        lines.append(f"- 테스트 실패: {failed}")
    else:
        lines.append(f"- 컴파일/테스트 통과 ({len(verification.tests_run)}개 테스트)")

    if mutation.mutants_total > 0:
        lines.append(
            f"- 뮤테이션 점수: {mutation.score:.0%} "
            f"({mutation.mutants_killed}/{mutation.mutants_total})"
        )
        if status == "PASS" and mutation.score < MUTATION_SCORE_THRESHOLD:
            lines.append(
                "- 기존 테스트가 이 변경 영역의 결함을 충분히 잡아내지 못했습니다 "
                "— 테스트 파일을 보완한 뒤 다시 시도하세요."
            )

    lines.append("")
    return lines


def build_escalation_report(report: EscalationReport) -> str:
    """EscalationReport를 사람이 읽을 마크다운/텍스트 리포트로 렌더링한다."""
    lines = [
        "# Weld: 자동 병합 실패 — 사람 확인 필요",
        "",
        "## 시도한 후보",
        "",
    ]

    if not report.candidates:
        lines.append("(생성된 후보 없음)")
    else:
        for i, (verification, mutation) in enumerate(
            zip(report.verifications, report.mutation_scores), start=1
        ):
            lines.extend(_render_candidate(i, verification, mutation))

    return "\n".join(lines).rstrip() + "\n"
