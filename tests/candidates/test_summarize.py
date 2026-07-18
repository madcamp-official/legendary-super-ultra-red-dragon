import os
from unittest.mock import patch

import pytest

from weld.candidates.summarize import summarize_intent


def _patch_llm(**kwargs):
    return (
        patch("weld.candidates.summarize._build_client", return_value=None),
        patch("weld.candidates.summarize._call_llm", **kwargs),
    )


def test_summarize_intent_returns_llm_output():
    build, call = _patch_llm(return_value="ours는 검증을, theirs는 로깅을 추가했다.")
    with build, call:
        summary = summarize_intent(base="", ours="a", theirs="b")
    assert summary == "ours는 검증을, theirs는 로깅을 추가했다."


def test_summarize_intent_prompt_includes_all_three_versions():
    captured: list[str] = []

    def fake_call(client, prompt):
        captured.append(prompt)
        return "요약"

    build, call = _patch_llm(side_effect=fake_call)
    with build, call:
        summarize_intent(base="BASE_TEXT", ours="OURS_TEXT", theirs="THEIRS_TEXT")

    assert "BASE_TEXT" in captured[0]
    assert "OURS_TEXT" in captured[0]
    assert "THEIRS_TEXT" in captured[0]


def test_summarize_intent_missing_api_key_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            summarize_intent(base="", ours="a", theirs="b")


@pytest.mark.skipif(
    not os.environ.get("WELD_RUN_LLM_TESTS"),
    reason="WELD_RUN_LLM_TESTS=1 없으면 스킵 (쿼터/비용 절약 — 일반 pytest에선 안 돈다)",
)
def test_summarize_intent_real_llm_produces_nonempty_summary():
    summary = summarize_intent(
        base="def process_order(order):\n    total = 0\n    return total\n",
        ours="def process_order(order):\n    if not order.items:\n        raise ValueError('empty')\n    total = 0\n    return total\n",
        theirs="def process_order(order):\n    logger.info('processing')\n    total = 0\n    return total\n",
    )
    assert summary.strip() != ""
