"""담당: 나

진짜 충돌에 대해 LLM으로 병합 후보 N개를 전략별로 생성한다.
"""

from __future__ import annotations

from weld.types import MergeCandidate


def generate_candidates(base: str, ours: str, theirs: str, n: int = 3) -> list[MergeCandidate]:
    """3-way 충돌에 대해 서로 다른 전략으로 후보 n개를 생성한다."""
    raise NotImplementedError
