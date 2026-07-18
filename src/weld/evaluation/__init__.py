"""Weld 평가 하네스 — 과거 충돌 사례로 파이프라인의 지표를 측정한다.

- cases: 평가 사례/결과 데이터 모델과 데이터셋 로더 인터페이스
- metrics: 결과 목록에서 지표를 계산하는 순수 함수들
- harness: 사례를 Weld 파이프라인에 돌려 결과를 뽑는 러너
"""

from weld.evaluation.cases import EvalCase, EvalOutcome
from weld.evaluation.metrics import EvalReport, compute_report

__all__ = ["EvalCase", "EvalOutcome", "EvalReport", "compute_report"]
