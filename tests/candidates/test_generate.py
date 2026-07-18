import os
from unittest.mock import patch

import pytest

from weld.candidates.generate import generate_candidates, is_value_conflict


def _patch_llm(**kwargs):
    return (
        patch("weld.candidates.generate._build_client", return_value=None),
        patch("weld.candidates.generate._call_llm", **kwargs),
    )


# --- is_value_conflict ---------------------------------------------------


def test_is_value_conflict_true_when_same_line_changed_both_sides():
    base = "def greet(name):\n    return f\"Hello {name}\"\n"
    ours = "def greet(name):\n    return f\"Hi there, {name}!\"\n"
    theirs = "def greet(name):\n    return f\"Hello, {name}. Welcome!\"\n"
    assert is_value_conflict(base, ours, theirs) is True


def test_is_value_conflict_false_when_line_count_differs():
    assert is_value_conflict(base="a\n", ours="a\nb\n", theirs="a\nc\n") is False


def test_is_value_conflict_false_when_different_lines_touched():
    base = "a\nb\n"
    ours = "x\nb\n"  # 0번 줄만 변경
    theirs = "a\ny\n"  # 1번 줄만 변경
    assert is_value_conflict(base, ours, theirs) is False


def test_is_value_conflict_false_when_only_one_side_changed():
    assert is_value_conflict(base="a\n", ours="b\n", theirs="a\n") is False


# --- generate_candidates: 값 충돌 경로 (LLM 호출 없음) ----------------------


def test_generate_candidates_value_conflict_returns_verbatim_without_llm():
    base = "def greet(name):\n    return f\"Hello {name}\"\n"
    ours = "def greet(name):\n    return f\"Hi there, {name}!\"\n"
    theirs = "def greet(name):\n    return f\"Hello, {name}. Welcome!\"\n"

    with patch("weld.candidates.generate._call_llm") as mock_call:
        candidates = generate_candidates(base, ours, theirs)

    mock_call.assert_not_called()
    strategies = {c.strategy for c in candidates}
    assert strategies == {"ours-verbatim", "theirs-verbatim"}
    assert next(c for c in candidates if c.strategy == "ours-verbatim").content == ours
    assert next(c for c in candidates if c.strategy == "theirs-verbatim").content == theirs


# --- generate_candidates: 구조적 충돌 경로 (LLM 목 호출) --------------------


def test_generate_candidates_returns_requested_count():
    build, call = _patch_llm(return_value="merged")
    with build, call:
        candidates = generate_candidates(base="", ours="a", theirs="b", n=2)
    assert len(candidates) == 2


def test_generate_candidates_default_covers_three_strategies():
    build, call = _patch_llm(return_value="merged")
    with build, call:
        candidates = generate_candidates(base="", ours="a", theirs="b")
    strategies = {c.strategy for c in candidates}
    assert strategies == {"llm-primary", "llm-alternative-1", "llm-alternative-2"}


def test_generate_candidates_alternative_prompts_reference_previous_candidates():
    prompts: list[str] = []

    def fake_call(client, prompt):
        prompts.append(prompt)
        return f"merged-{len(prompts)}"

    build, call = _patch_llm(side_effect=fake_call)
    with build, call:
        generate_candidates(base="", ours="a", theirs="b", n=3)

    assert "기존 병합안" not in prompts[0]
    assert "merged-1" in prompts[1]
    assert "merged-1" in prompts[2]
    assert "merged-2" in prompts[2]


def test_generate_candidates_ids_are_unique():
    build, call = _patch_llm(return_value="merged")
    with build, call:
        candidates = generate_candidates(base="", ours="a", theirs="b")
    ids = [c.id for c in candidates]
    assert len(ids) == len(set(ids))


def test_generate_candidates_uses_llm_output_as_content():
    build, call = _patch_llm(return_value="resolved content")
    with build, call:
        candidates = generate_candidates(base="", ours="a", theirs="b", n=1)
    assert candidates[0].content == "resolved content"


def test_generate_candidates_prompt_includes_both_sides():
    captured: list[str] = []

    def fake_call(client, prompt):
        captured.append(prompt)
        return "merged"

    build, call = _patch_llm(side_effect=fake_call)
    with build, call:
        # 줄 수가 다른 구조적 충돌이어야 LLM 경로를 탄다 (값 충돌은 verbatim으로 새감).
        generate_candidates(
            base="BASE_TEXT", ours="OURS_TEXT\nEXTRA_LINE", theirs="THEIRS_TEXT", n=1
        )
    assert "OURS_TEXT" in captured[0]
    assert "THEIRS_TEXT" in captured[0]
    assert "BASE_TEXT" in captured[0]


def test_generate_candidates_missing_api_key_raises():
    # 줄 수가 다르므로 구조적 충돌 경로(LLM 호출) — 값 충돌 경로는 API 키가 필요 없다.
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            generate_candidates(base="", ours="a\nb", theirs="c", n=1)


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"), reason="GEMINI_API_KEY 없으면 실제 API 호출 스킵"
)
def test_generate_candidates_real_llm_produces_valid_python():
    """실제 Gemini 호출로 문법적으로 유효한 병합 결과를 내는지 확인 (수동/로컬 전용).

    구조적 충돌(줄 추가)을 써서 값 충돌 경로로 새지 않고 실제로 LLM을 타게 한다.
    """
    candidates = generate_candidates(
        base="def greet(name):\n    return name\n",
        ours='def greet(name):\n    log(name)\n    return f"Hi, {name}"\n',
        theirs='def greet(name):\n    return f"Hello, {name}!"\n    # theirs comment\n',
        n=1,
    )
    compile(candidates[0].content, "<candidate>", "exec")
