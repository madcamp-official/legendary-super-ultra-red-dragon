import pytest

from weld.escalate.report import build_escalation_report
from weld.types import EscalationReport


def test_build_escalation_report_is_not_implemented_yet():
    report = EscalationReport(
        intent_summary="", candidates=[], verifications=[], mutation_scores=[]
    )
    with pytest.raises(NotImplementedError):
        build_escalation_report(report)
