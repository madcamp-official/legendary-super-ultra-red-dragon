"""담당: 나

에스컬레이션 시 사람에게 보여줄 "이 충돌은 무엇을 하려던 변경인가"를 요약한다.
"""

from __future__ import annotations


def summarize_intent(base: str, ours: str, theirs: str) -> str:
    """커밋 메시지/diff를 바탕으로 양쪽 변경의 의도를 자연어로 요약한다."""
    raise NotImplementedError
