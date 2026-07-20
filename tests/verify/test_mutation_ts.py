"""tree-sitter 다국어 뮤테이션 엔진 테스트 (JS 픽스처 + 실제 node --test 실행).

node 또는 tree-sitter-language-pack이 없는 환경에서는 해당 테스트를 skip한다
(엔진 자체가 그 경우 '신호 없음' 폴백이므로 파이프라인은 안 깨진다).
"""

import shutil
import textwrap

import pytest

from weld.types import MergeCandidate
from weld.verify.mutation import compute_mutation_score
from weld.verify.mutation_ts import (
    _apply_splice,
    _collect_splice_sites,
    compute_mutation_score_ts,
)

try:
    import tree_sitter_language_pack  # noqa: F401

    _TS_OK = True
except ImportError:
    _TS_OK = False

needs_ts = pytest.mark.skipif(not _TS_OK, reason="tree-sitter-language-pack 미설치")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")

_BASE_JS = textwrap.dedent("""\
    function clamp(x, lo, hi) {
      if (x < lo) {
        return lo;
      }
      return x;
    }
    module.exports = { clamp };
    """)

_CANDIDATE_JS = textwrap.dedent("""\
    function clamp(x, lo, hi) {
      if (x < lo) {
        return lo;
      }
      if (x > hi) {
        return hi;
      }
      return x;
    }
    module.exports = { clamp };
    """)

_STRONG_TESTS = textwrap.dedent("""\
    const test = require('node:test');
    const assert = require('node:assert');
    const { clamp } = require('./calc.js');

    test('interior', () => { assert.strictEqual(clamp(5, 0, 10), 5); });
    test('below', () => { assert.strictEqual(clamp(-3, 0, 10), 0); });
    test('above', () => { assert.strictEqual(clamp(15, 0, 10), 10); });
    """)

_WEAK_TESTS = textwrap.dedent("""\
    const test = require('node:test');
    const assert = require('node:assert');
    const { clamp } = require('./calc.js');

    // 약한 테스트(의도적): x < lo에서 조기 반환되는 입력만 확인 —
    // 새로 추가된 hi 경계 검사 줄은 아예 실행되지 않는다.
    test('below only', () => { assert.strictEqual(clamp(-3, 0, 10), 0); });
    """)


def _make_repo(tmp_path, tests_source):
    (tmp_path / "calc.js").write_text(_CANDIDATE_JS)
    (tmp_path / "calc.test.js").write_text(tests_source)
    return str(tmp_path)


@needs_ts
def test_sites_only_on_changed_lines():
    src = _CANDIDATE_JS.encode()
    all_sites = _collect_splice_sites(src, "javascript", set(range(1, 20)))
    assert {s.operator for s in all_sites} >= {"comparison_flip"}
    # 변경 줄을 5번(x > hi)으로 제한하면 그 줄의 사이트만 나와야 한다.
    only5 = _collect_splice_sites(src, "javascript", {5})
    assert only5 and all(s.lineno == 5 for s in only5)


@needs_ts
def test_apply_splice_produces_flipped_source():
    src = b"const ok = a < b;\n"
    [site] = _collect_splice_sites(src, "javascript", {1})
    assert _apply_splice(src, site) == b"const ok = a >= b;\n"


@needs_ts
@needs_node
def test_strong_tests_kill_boundary_mutant(tmp_path):
    """추가된 경계 검사(x > hi)를 뒤집으면 'above' 테스트가 잡아야 한다."""
    repo = _make_repo(tmp_path, _STRONG_TESTS)
    candidate = MergeCandidate(id="c1", content=_CANDIDATE_JS, file_path="calc.js")
    score = compute_mutation_score_ts(candidate, repo_path=repo, base_content=_BASE_JS)
    assert score.sites_total >= 1
    assert score.mutants_total >= 1
    assert score.mutants_killed == score.mutants_total  # 전부 kill
    assert score.score == 1.0


@needs_ts
@needs_node
def test_weak_tests_let_mutant_survive(tmp_path):
    """변경 줄을 실행하지 않는 테스트에서는 뮤턴트가 생존해야 한다.

    이 엔진은 줄 단위 커버리지 확인이 없어 미실행 뮤턴트가 '생존'으로
    집계된다 — 점수가 낮아져 에스컬레이션되는 보수적 편향(문서화된 동작)."""
    repo = _make_repo(tmp_path, _WEAK_TESTS)
    candidate = MergeCandidate(id="c1", content=_CANDIDATE_JS, file_path="calc.js")
    score = compute_mutation_score_ts(candidate, repo_path=repo, base_content=_BASE_JS)
    assert score.mutants_total >= 1
    assert score.mutants_killed < score.mutants_total
    assert score.survived_mutants


@needs_ts
@needs_node
def test_dispatcher_routes_non_python_to_ts_engine(tmp_path):
    """공개 API compute_mutation_score가 .js 후보를 TS 엔진으로 보낸다."""
    repo = _make_repo(tmp_path, _STRONG_TESTS)
    candidate = MergeCandidate(id="c1", content=_CANDIDATE_JS, file_path="calc.js")
    score = compute_mutation_score(
        candidate, relevant_tests=["무의미한-pytest-id"], repo_path=repo,
        base_content=_BASE_JS,
    )
    assert score.mutants_total >= 1  # pytest ID와 무관하게 node로 판정됨


def test_unknown_language_returns_no_signal(tmp_path):
    candidate = MergeCandidate(id="c1", content="whatever", file_path="notes.txt")
    score = compute_mutation_score_ts(candidate, repo_path=str(tmp_path))
    assert score.mutants_total == 0 and score.sites_total == 0


@needs_ts
@needs_node
def test_failing_baseline_returns_no_signal(tmp_path):
    """원본 후보부터 테스트가 빨간 상태면 kill 판정이 성립하지 않는다."""
    broken = _CANDIDATE_JS.replace("return x;", "return x + 1;")
    (tmp_path / "calc.js").write_text(broken)
    (tmp_path / "calc.test.js").write_text(_STRONG_TESTS)
    candidate = MergeCandidate(id="c1", content=broken, file_path="calc.js")
    score = compute_mutation_score_ts(
        candidate, repo_path=str(tmp_path), base_content=_BASE_JS
    )
    assert score.mutants_total == 0
    assert score.sites_total >= 1  # 사이트는 있었지만 판정 불가
