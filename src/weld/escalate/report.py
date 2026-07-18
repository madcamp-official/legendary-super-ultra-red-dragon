"""담당: 나

검증에 실패해 사람에게 넘길 때, 원시 충돌 마커만 던지지 않고 의도 요약과
후보안(및 각 후보의 검증 결과)을 함께 담은 리포트를 만든다.

실패해도 git 표준 충돌 마커는 그대로 유지된다 — 이 리포트는 stderr로 추가
제공되는 참고 정보일 뿐, 병합 자체의 유일한 정보원이 아니다.
"""

from __future__ import annotations

from weld.types import EscalationReport, MutationScore, VerificationResult


def _render_candidate(
    index: int, verification: VerificationResult, mutation: MutationScore
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
        if mutation.survived_mutants:
            lines.append(f"- 살아남은 뮤턴트: {', '.join(mutation.survived_mutants)}")

    lines.append("")
    return lines


def build_escalation_report(report: EscalationReport) -> str:
    """EscalationReport를 사람이 읽을 마크다운/텍스트 리포트로 렌더링한다."""
    lines = [
        "# Weld: 자동 병합 실패 — 사람 확인 필요",
        "",
        "## 의도 요약",
        report.intent_summary or "(요약 없음)",
        "",
        "## 시도한 후보",
        "",
    ]

    if not report.candidates:
        lines.append("(생성된 후보 없음)")
    else:
        for i, (candidate, verification, mutation) in enumerate(
            zip(report.candidates, report.verifications, report.mutation_scores), start=1
        ):
            lines.extend(_render_candidate(i, verification, mutation))
            lines.append(f"```\n{candidate.content}\n```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
