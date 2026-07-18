"""담당: 김민재

Mergiraf(tree-sitter 기반 구조적 병합 도구)를 체이닝해서 가짜 충돌(구조적으로
안 겹치는 변경)을 자동으로 걸러낸다. 새로 병합 알고리즘을 짜는 게 아니라
기존 Mergiraf 바이너리/라이브러리를 호출하고 결과를 해석하는 어댑터.
"""

from __future__ import annotations

from weld.types import ClassificationResult


def classify_conflict(base: str, ours: str, theirs: str) -> ClassificationResult:
    """3-way 충돌을 Mergiraf에 넘겨 가짜/진짜를 분류한다.

    가짜 충돌이면 resolved_content에 Mergiraf가 만든 최종 병합 결과를 채운다.
    """
    raise NotImplementedError
