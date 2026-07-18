"""담당: 이서영

에스컬레이션 시 사람에게 보여줄 "이 충돌은 무엇을 하려던 변경인가"를 요약한다.

generate.py와 같은 Gemini 연동(_build_client, _call_llm)을 재사용한다 — 별도
LLM 호출 경로를 새로 만들지 않는다.
"""

from __future__ import annotations

from weld.candidates.generate import _build_client, _call_llm

_PROMPT_TEMPLATE = """다음은 git 3-way 병합 충돌이다. base는 공통 조상, ours와 \
theirs는 각각 여기서 갈라져 나온 두 버전이다.

--- base ---
{base}

--- ours ---
{ours}

--- theirs ---
{theirs}

ours와 theirs 각각이 base 대비 실제로 무엇을 하려고 했는지(의도), 그리고 왜
서로 충돌했는지를 사람이 읽을 2~3문장짜리 한국어 요약으로 설명하라. 코드를
그대로 반복해서 보여주지 말고 자연어 설명만 출력하라.
"""


def summarize_intent(base: str, ours: str, theirs: str) -> str:
    """커밋 메시지/diff를 바탕으로 양쪽 변경의 의도를 자연어로 요약한다."""
    client = _build_client()
    prompt = _PROMPT_TEMPLATE.format(base=base, ours=ours, theirs=theirs)
    return _call_llm(client, prompt)
