"""union-green 필터 — "초록 main 가정" 제거의 안전성 검증.

핵심 두 가지:
1. 양쪽 부모 모두에서 빨간 테스트(이 병합과 무관한 잔재)는 게이트에서 빠진다.
2. theirs(MERGE_HEAD)에서만 초록인 테스트는 **남는다** — naive baseline-diff
   ("HEAD에서 빨간 건 전부 무시")였다면 theirs 기능을 빼먹은 후보가 통과해
   오탐이 됐을 지점.
"""

from __future__ import annotations

import subprocess
import textwrap

import pytest

from weld.verify.impact import select_relevant_tests

D = textwrap.dedent


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "tests" / "__init__.py").write_text("")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def test_both_red_noise_is_filtered_out(tmp_path):
    """다른 시나리오의 base-빨강 테스트가 전체-폴백에 딸려 와도 게이트에서 빠진다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "value.py").write_text("TIMEOUT = 60\n")
    (repo / "tests" / "test_value.py").write_text(D("""\
        from src.value import TIMEOUT
        def test_timeout():
            assert TIMEOUT == 60
        """))
    # 잔재 노이즈: import부터 깨져서 collection error → baseline 매핑 실패
    # → 전체-폴백 경로를 강제로 태운다 (실측 오염 사건과 같은 메커니즘).
    (repo / "tests" / "test_noise.py").write_text(D("""\
        from src.nonexistent import missing  # noqa: F401
        def test_never_runs():
            assert False
        """))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    selected = select_relevant_tests(["src/value.py"], repo_path=str(repo))

    assert any("test_value" in t for t in selected), "관련 초록 테스트는 남아야 한다"
    assert not any("test_noise" in t for t in selected), (
        "양쪽 어디서도 안 도는 잔재 테스트가 게이트에 남으면 후보 검증이 오염된다"
    )


def test_theirs_only_green_test_is_kept(tmp_path):
    """MERGE_HEAD에서만 초록인 테스트는 유지 — naive baseline-diff 함정 방어.

    main: feature()가 1을 반환, test_b는 2를 기대(빨강).
    branch: feature()를 2로 수정(test_b 초록).
    병합 중(MERGE_HEAD 존재) 선별하면 test_b가 게이트에 남아야 한다 —
    HEAD 기준 빨강이라고 버리면, theirs 변경을 빼먹은 후보를 못 잡는다.
    """
    repo = _init_repo(tmp_path)
    (repo / "src" / "f.py").write_text("def feature():\n    return 1\n")
    (repo / "tests" / "test_b.py").write_text(D("""\
        from src.f import feature
        def test_feature_v2():
            assert feature() == 2
        """))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "main: feature v1 + v2 기대 테스트(아직 빨강)")

    _git(repo, "checkout", "-q", "-b", "v2")
    (repo / "src" / "f.py").write_text("def feature():\n    return 2\n")
    _git(repo, "commit", "-q", "-am", "v2: feature 2 반환")
    _git(repo, "checkout", "-q", "main")

    # 병합 시작(--no-commit로 MERGE_HEAD를 살려둔 채 멈춤)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-commit", "--no-ff", "v2"],
        capture_output=True,
    )
    merge_head = (repo / ".git" / "MERGE_HEAD")
    if not merge_head.exists():
        pytest.skip("이 git 버전에서 --no-commit 병합이 MERGE_HEAD를 안 남김")

    selected = select_relevant_tests(["src/f.py"], repo_path=str(repo))

    assert any("test_feature_v2" in t for t in selected), (
        "theirs에서 초록인 테스트를 버리면 theirs 기능을 빼먹은 후보가 통과한다(오탐)"
    )
