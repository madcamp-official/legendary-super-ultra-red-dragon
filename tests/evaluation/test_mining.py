"""mining.py 테스트 — 손으로 만든 git 저장소에 진짜 충돌 병합을 심고, 캐낸
EvalCase의 base/ours/theirs/resolution이 정확한지 확인한다. 네트워크 불필요.
"""

from __future__ import annotations

import subprocess

from weld.evaluation.mining import mine_conflicts


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_conflicting_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.local")
    _git(repo, "config", "user.name", "t")

    (repo / "mod.py").write_text("def rate():\n    return 100\n")
    _git(repo, "add", "."), _git(repo, "commit", "-qm", "base")

    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "mod.py").write_text("def rate():\n    return 200\n")
    _git(repo, "commit", "-qam", "feature: 200")

    _git(repo, "checkout", "-q", "-")  # 원래 브랜치로
    (repo / "mod.py").write_text("def rate():\n    return 300\n")
    _git(repo, "commit", "-qam", "main: 300")

    # 충돌 병합을 만들고, 사람이 250으로 해결했다고 기록
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-commit", "feature"],
        capture_output=True, text=True,
    )
    (repo / "mod.py").write_text("def rate():\n    return 250\n")  # 사람의 해결(정답)
    _git(repo, "add", "mod.py")
    _git(repo, "commit", "-qm", "merge: 사람이 250으로 해결")
    return repo


def test_mine_conflicts_extracts_base_ours_theirs_resolution(tmp_path):
    repo = _make_conflicting_repo(tmp_path)
    cases = mine_conflicts(str(repo))

    assert len(cases) == 1
    case = cases[0]
    assert case.file_path == "mod.py"
    assert "return 100" in case.base           # merge-base
    assert "return 300" in case.ours           # P1 = main
    assert "return 200" in case.theirs         # P2 = feature
    assert case.ground_truth_resolution is not None
    assert "return 250" in case.ground_truth_resolution  # 사람이 실제로 고른 값


def test_mine_conflicts_respects_max_cases(tmp_path):
    repo = _make_conflicting_repo(tmp_path)
    assert mine_conflicts(str(repo), max_cases=0) == []
