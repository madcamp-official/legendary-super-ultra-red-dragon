"""담당: 이서영

검증에 실패해 사람에게 넘길 때, 원시 충돌 마커만 던지지 않고 후보안(및
각 후보의 검증 결과)을 함께 담은 리포트를 만든다. 의도 요약은 LLM 호출을
후보 생성(candidates/generate.py) 한 곳으로만 한정하기로 한 팀 결정에 따라
항상 비어 있으므로 출력하지 않는다.

후보 코드는 전체 파일이 아니라 base 대비 diff로 보여준다 — MergeCandidate.
content 계약(파일 전체, types.py 참고)은 그대로 유지하되, 사람이 읽는 화면
에서까지 안 바뀐 수백 줄을 스크롤하게 만들 필요는 없다. 큰 파일(예: 800줄
넘는 core.py)일수록 이 차이가 크다.

실패해도 git 표준 충돌 마커는 그대로 유지된다 — 이 리포트는 stderr로 추가
제공되는 참고 정보일 뿐, 병합 자체의 유일한 정보원이 아니다.
"""

from __future__ import annotations

import difflib

from weld.types import EscalationReport, MergeCandidate, MutationScore, VerificationResult


def _render_diff(base: str, content: str) -> str:
    """base 대비 content의 unified diff. 동일하면 그 사실을 짧게 알린다."""
    diff_lines = list(
        difflib.unified_diff(
            base.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile="base",
            tofile="candidate",
        )
    )
    return "".join(diff_lines) if diff_lines else "(base와 동일 — 변경 없음)"


def _render_candidate(
    index: int,
    base: str,
    candidate: MergeCandidate,
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
        if mutation.survived_mutants:
            lines.append(f"- 살아남은 뮤턴트: {', '.join(mutation.survived_mutants)}")

    lines.append("")
    lines.append(f"```diff\n{_render_diff(base, candidate.content)}\n```")
    lines.append("")
    return lines


def build_escalation_report(report: EscalationReport, base: str = "") -> str:
    """EscalationReport를 사람이 읽을 마크다운/텍스트 리포트로 렌더링한다.

    base: 충돌 전 공통 조상 파일 내용. 넘기면 후보 코드를 diff로 보여주고,
    안 넘기면(기본값 "") base 자체가 없는 것으로 보고 전체가 추가된 것처럼
    diff가 나온다 — 그래도 "파일 전체를 그대로 덤프"보다는 낫다.
    """
    lines = [
        "# Weld: 자동 병합 실패 — 사람 확인 필요",
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
            lines.extend(_render_candidate(i, base, candidate, verification, mutation))

    return "\n".join(lines).rstrip() + "\n"
