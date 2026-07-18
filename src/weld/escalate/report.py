"""담당: 이서영

검증에 실패해 사람에게 넘길 때, 원시 충돌 마커만 던지지 않고 의도 요약과
후보안(및 각 후보의 검증 결과)을 함께 담은 리포트를 만든다.
"""

from __future__ import annotations

from weld.types import EscalationReport


def build_escalation_report(report: EscalationReport) -> str:
    """EscalationReport를 사람이 읽을 마크다운/텍스트 리포트로 렌더링한다."""
    raise NotImplementedError
