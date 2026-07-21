"""C/C++ 뮤테이션 kill 판정 테스트 (실제 cc + make 빌드 사이클).

cc/make 또는 tree-sitter가 없는 환경에서는 skip. C/C++ 규약: 저장소 루트
Makefile의 기본 타깃이 테스트 바이너리를 빌드하고 `test` 타깃이 실행한다.
"""

import shutil
import textwrap

import pytest

from weld.types import MergeCandidate
from weld.verify.mutation_ts import _collect_splice_sites, compute_mutation_score_ts

try:
    import tree_sitter_language_pack  # noqa: F401

    _TS_OK = True
except ImportError:
    _TS_OK = False

needs_ts = pytest.mark.skipif(not _TS_OK, reason="tree-sitter-language-pack 미설치")
needs_cc = pytest.mark.skipif(
    shutil.which("cc") is None or shutil.which("make") is None,
    reason="cc/make 미설치",
)

_BASE_C = textwrap.dedent("""\
    #include "clamp.h"

    int clamp(int x, int lo, int hi) {
        if (x < lo) {
            return lo;
        }
        return x;
    }
    """)

_CANDIDATE_C = textwrap.dedent("""\
    #include "clamp.h"

    int clamp(int x, int lo, int hi) {
        if (x < lo) {
            return lo;
        }
        if (x > hi) {
            return hi;
        }
        return x;
    }
    """)

_HEADER_C = "int clamp(int x, int lo, int hi);\n"

_STRONG_TEST_C = textwrap.dedent("""\
    #include <assert.h>
    #include "clamp.h"

    int main(void) {
        assert(clamp(5, 0, 10) == 5);
        assert(clamp(-3, 0, 10) == 0);
        assert(clamp(15, 0, 10) == 10);
        return 0;
    }
    """)

_WEAK_TEST_C = textwrap.dedent("""\
    #include <assert.h>
    #include "clamp.h"

    /* 약한 테스트(의도적): x < lo 조기 반환 입력만 — 새 hi 경계 줄은 미실행 */
    int main(void) {
        assert(clamp(-3, 0, 10) == 0);
        return 0;
    }
    """)

_MAKEFILE = textwrap.dedent("""\
    CC ?= cc
    test_bin: clamp.c test_clamp.c clamp.h
\t$(CC) -o test_bin clamp.c test_clamp.c

    .PHONY: test
    test: test_bin
\t./test_bin
    """)


def _make_repo(tmp_path, test_source):
    (tmp_path / "clamp.c").write_text(_CANDIDATE_C)
    (tmp_path / "clamp.h").write_text(_HEADER_C)
    (tmp_path / "test_clamp.c").write_text(test_source)
    (tmp_path / "Makefile").write_text(_MAKEFILE)
    return str(tmp_path)


@needs_ts
@needs_cc
def test_strong_c_tests_kill_boundary_mutant(tmp_path):
    """추가된 경계 검사(x > hi)를 뒤집으면 assert가 잡아야 한다."""
    repo = _make_repo(tmp_path, _STRONG_TEST_C)
    candidate = MergeCandidate(id="c1", content=_CANDIDATE_C, file_path="clamp.c")
    score = compute_mutation_score_ts(candidate, repo_path=repo, base_content=_BASE_C)
    assert score.mutants_total >= 1
    assert score.mutants_killed == score.mutants_total
    assert score.score == 1.0


@needs_ts
@needs_cc
def test_weak_c_tests_let_mutant_survive(tmp_path):
    """변경 줄을 실행하지 않는 테스트에서는 뮤턴트가 생존해야 한다.

    이 시나리오가 잡아내는 함정: macOS GNU make 3.81의 초 단위 mtime 비교
    때문에 -B 없이 돌리면 낡은 바이너리가 실행돼 모든 뮤턴트가 가짜로
    생존한다 — build_command의 -B 규약이 지켜지는지도 함께 검증된다
    (강한 테스트 케이스에서 kill이 나오는 것이 그 증거).
    """
    repo = _make_repo(tmp_path, _WEAK_TEST_C)
    candidate = MergeCandidate(id="c1", content=_CANDIDATE_C, file_path="clamp.c")
    score = compute_mutation_score_ts(candidate, repo_path=repo, base_content=_BASE_C)
    assert score.mutants_total >= 1
    assert score.mutants_killed < score.mutants_total
    assert score.survived_mutants


@needs_ts
def test_cpp_template_angle_brackets_are_not_sites():
    """C++ 템플릿의 `<`/`>`는 비교 연산자가 아니다 — 사이트로 잡으면
    컴파일 불능 뮤턴트로 예산만 낭비된다."""
    src = textwrap.dedent("""\
        #include <vector>
        int total(std::vector<int> xs) {
            int s = 0;
            for (int x : xs) s = s + x;
            return s;
        }
        """).encode()
    sites = _collect_splice_sites(src, "cpp", set(range(1, 10)))
    # vector<int>의 꺾쇠(2행)는 제외되고, 실제 연산자(+ 등)만 잡혀야 한다.
    assert all(s.lineno != 2 for s in sites if s.operator == "comparison_flip")
    assert any(s.operator == "arithmetic_flip" for s in sites)
