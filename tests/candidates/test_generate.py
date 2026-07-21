import os
from unittest.mock import patch

import pytest

from weld.candidates.generate import (
    _TEMP_HIGH,
    _TEMP_LOW,
    _normalize_hunk_output,
    _spread_temperatures,
    generate_candidates,
    is_value_conflict,
)


def _patch_llm(**kwargs):
    return (
        patch("weld.candidates.generate._build_client", return_value=None),
        patch("weld.candidates.generate._call_llm", **kwargs),
    )


def _distinct_by_temperature(client, prompt, temperature=0.7):
    """온도별로 서로 다른 응답을 내는 가짜 LLM — 후보들이 실제로 갈릴 때의
    동작(개수/고유성)을 테스트하려면 응답이 겹치면 안 되므로 이걸 쓴다."""
    return f"merged-{temperature}\n"


# --- _call_llm: 디스크 캐싱 (무료 티어 요청 수 절약) --------------------------


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self) -> None:
        self.calls = 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        return _FakeResponse(f"response-{self.calls}")


class _FakeClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def test_call_llm_reuses_cached_response_for_identical_prompt(tmp_path, monkeypatch):
    from weld.candidates import generate as gen

    monkeypatch.chdir(tmp_path)
    client = _FakeClient()

    first = gen._call_llm(client, "same prompt", temperature=0.7)
    second = gen._call_llm(client, "same prompt", temperature=0.7)

    assert first == second
    assert client.models.calls == 1  # 두 번째 호출은 캐시 히트라 API를 안 부름


def test_call_llm_cache_miss_on_different_temperature(tmp_path, monkeypatch):
    from weld.candidates import generate as gen

    monkeypatch.chdir(tmp_path)
    client = _FakeClient()

    gen._call_llm(client, "same prompt", temperature=0.2)
    gen._call_llm(client, "same prompt", temperature=0.9)

    assert client.models.calls == 2  # temperature가 다르면 별개의 캐시 키


def test_call_llm_persists_cache_across_calls(tmp_path, monkeypatch):
    from weld.candidates import generate as gen

    monkeypatch.chdir(tmp_path)
    client_a = _FakeClient()
    gen._call_llm(client_a, "prompt", temperature=0.5)

    cache_file = tmp_path / ".weld_cache" / "llm_responses.json"
    assert cache_file.exists()

    # 새 client(=새 프로세스를 흉내)여도 디스크 캐시가 있으면 재호출 없이 재사용.
    client_b = _FakeClient()
    result = gen._call_llm(client_b, "prompt", temperature=0.5)

    assert client_b.models.calls == 0
    assert result == "response-1"


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


# --- _normalize_hunk_output ------------------------------------------------


def test_normalize_hunk_output_adds_missing_leading_indent():
    hunk = ("    log(name)\n    return 1\n", "    return name\n", "    return 2\n")
    resolved = 'log(name)\n    return f"Hello, {name}!"\n'
    normalized = _normalize_hunk_output(resolved, hunk)
    assert normalized == 'log(name)\n    return f"Hello, {name}!"\n'.replace(
        "log(name)", "    log(name)", 1
    )


def test_normalize_hunk_output_leaves_correct_indent_alone():
    hunk = ("    x = 1\n", "    x = 0\n", "    x = 2\n")
    resolved = "    x = 3\n"
    assert _normalize_hunk_output(resolved, hunk) == "    x = 3\n"


def test_normalize_hunk_output_appends_missing_trailing_newline():
    hunk = ("    a\n", "    b\n", "    c\n")
    resolved = "    d"  # 끝 개행 없음
    assert _normalize_hunk_output(resolved, hunk) == "    d\n"


def test_normalize_hunk_output_noop_when_reference_has_no_indent():
    hunk = ("a\n", "b\n", "c\n")
    resolved = "d\n"
    assert _normalize_hunk_output(resolved, hunk) == "d\n"


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


# --- generate_candidates: diff3가 이미 완전히 합쳐버리는 경우 (LLM 호출 없음) ---


def test_generate_candidates_non_overlapping_change_skips_llm():
    """서로 다른 위치를 건드린 구조적 변경은 git의 텍스트 3-way 병합만으로 이미
    충돌 없이 합쳐진다 — mergiraf가 뭔가를 놓쳐도 여기서 한 번 더 LLM 호출을
    아낄 수 있는 안전망."""
    base = "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n"
    ours = (
        "def add(a, b):\n"
        '    print("adding")\n'
        "    return a + b\n\n\n"
        "def multiply(a, b):\n    return a * b\n"
    )
    theirs = base + "\n\ndef subtract(a, b):\n    return a - b\n"

    with patch("weld.candidates.generate._call_llm") as mock_call:
        candidates = generate_candidates(base, ours, theirs)

    mock_call.assert_not_called()
    assert len(candidates) == 1
    assert candidates[0].strategy == "diff3-verbatim"
    assert "print" in candidates[0].content
    assert "def subtract" in candidates[0].content


# --- generate_candidates: 구조적 충돌 경로 (훙크 단위 LLM 목 호출) -----------


def _multiline_conflict():
    # 줄 수가 다르므로 is_value_conflict는 False — diff3 경로로 간다.
    base = "a\nb\nc\n"
    ours = "a\nOURS_LINE\nc\nEXTRA\n"
    theirs = "a\nTHEIRS_LINE\nc\n"
    return base, ours, theirs


def test_generate_candidates_returns_requested_count():
    build, call = _patch_llm(side_effect=_distinct_by_temperature)
    with build, call:
        candidates = generate_candidates(*_multiline_conflict(), n=2)
    assert len(candidates) == 2


def test_generate_candidates_default_n_is_two():
    build, call = _patch_llm(side_effect=_distinct_by_temperature)
    with build, call:
        candidates = generate_candidates(*_multiline_conflict())
    assert len(candidates) == 2


def test_generate_candidates_ids_are_unique():
    build, call = _patch_llm(side_effect=_distinct_by_temperature)
    with build, call:
        candidates = generate_candidates(*_multiline_conflict())
    ids = [c.id for c in candidates]
    assert len(ids) == len(set(ids))


def test_generate_candidates_strategies_carry_distinct_temperatures():
    build, call = _patch_llm(side_effect=_distinct_by_temperature)
    with build, call:
        candidates = generate_candidates(*_multiline_conflict(), n=3)
    strategies = {c.strategy for c in candidates}
    assert len(strategies) == 3
    assert all(s.startswith("llm-hunk-t") for s in strategies)


# --- generate_candidates: 중복 후보 제거 ------------------------------------


def test_generate_candidates_dedupes_identical_content():
    """서로 다른 temperature로 뽑아도 결과가 완전히 같은 내용으로 수렴하면
    하나만 남기고 나머지는 버려 검증/뮤테이션을 중복으로 안 돌린다."""
    build, call = _patch_llm(return_value="SAME\n")
    with build, call:
        candidates = generate_candidates(*_multiline_conflict(), n=2)
    assert len(candidates) == 1


def test_generate_candidates_keeps_distinct_content():
    build, call = _patch_llm(side_effect=_distinct_by_temperature)
    with build, call:
        candidates = generate_candidates(*_multiline_conflict(), n=2)
    contents = {c.content for c in candidates}
    assert len(contents) == 2


# --- _spread_temperatures ---------------------------------------------------


def test_spread_temperatures_single_value_uses_low():
    assert _spread_temperatures(1) == [_TEMP_LOW]


def test_spread_temperatures_two_values_maximize_gap():
    # n=2(현재 기본값)일 때 후보 다양성을 최대화하려면 구간 양 끝을 써야 한다.
    assert _spread_temperatures(2) == [_TEMP_LOW, _TEMP_HIGH]


def test_spread_temperatures_evenly_spaced():
    values = _spread_temperatures(3)
    assert values[0] == _TEMP_LOW
    assert values[-1] == _TEMP_HIGH
    assert len(values) == 3
    assert len(set(values)) == 3  # 전부 서로 다른 온도


def test_generate_candidates_splices_hunk_resolution_into_surrounding_text():
    build, call = _patch_llm(return_value="RESOLVED\n")
    with build, call:
        candidates = generate_candidates(*_multiline_conflict(), n=1)
    # 충돌 안 난 앞/뒤 줄(a, c)은 그대로 남고, 충돌 훙크만 LLM 출력으로 바뀐다.
    assert candidates[0].content == "a\nRESOLVED\nc\nEXTRA\n"


def test_generate_candidates_resolves_multiple_far_apart_hunks():
    # ours에 EXTRA 줄을 하나 더 넣어 줄 수를 다르게 만든다 — 안 그러면
    # changed_by_ours == changed_by_theirs가 돼 is_value_conflict가 True를
    # 반환해서(둘 다 같은 줄들만 건드림) verbatim 경로로 빠져버린다.
    base = "a\nb\nc\nd\ne\n"
    ours = "a\nOURS1\nc\nOURS2\ne\nEXTRA\n"
    theirs = "a\nTHEIRS1\nc\nTHEIRS2\ne\n"

    calls: list[str] = []

    def fake_call(client, prompt, temperature=0.7):
        calls.append(prompt)
        if "OURS1" in prompt:
            return "MERGED1\n"
        return "MERGED2\n"

    build, call = _patch_llm(side_effect=fake_call)
    with build, call:
        candidates = generate_candidates(base, ours, theirs, n=1)

    assert len(calls) == 2  # 훙크 2개 × 후보 1개
    assert candidates[0].content == "a\nMERGED1\nc\nMERGED2\ne\nEXTRA\n"


def test_generate_candidates_hunk_prompt_includes_all_three_versions():
    captured: list[str] = []

    def fake_call(client, prompt, temperature=0.7):
        captured.append(prompt)
        return "merged\n"

    build, call = _patch_llm(side_effect=fake_call)
    with build, call:
        generate_candidates(*_multiline_conflict(), n=1)

    assert "OURS_LINE" in captured[0]
    assert "THEIRS_LINE" in captured[0]
    assert "b" in captured[0]  # base 쪽 훙크 내용


def test_generate_candidates_missing_api_key_raises():
    # clear=True로 os.environ 전체를 비우면 PATH도 같이 날아가서 내부의
    # git subprocess 호출이 깨진다 — GEMINI_API_KEY만 타겟으로 비운다.
    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            generate_candidates(*_multiline_conflict(), n=1)


@pytest.mark.skipif(
    not os.environ.get("WELD_RUN_LLM_TESTS"),
    reason="WELD_RUN_LLM_TESTS=1 없으면 스킵 (쿼터/비용 절약 — 일반 pytest에선 안 돈다)",
)
def test_generate_candidates_real_llm_produces_valid_python():
    """실제 Gemini 호출로 문법적으로 유효한 병합 결과를 내는지 확인 (수동/로컬 전용)."""
    base = "def greet(name):\n    return name\n"
    ours = 'def greet(name):\n    log(name)\n    return f"Hi, {name}"\n'
    theirs = 'def greet(name):\n    return f"Hello, {name}!"\n    # theirs comment\n'
    candidates = generate_candidates(base, ours, theirs, n=1)
    compile(candidates[0].content, "<candidate>", "exec")
