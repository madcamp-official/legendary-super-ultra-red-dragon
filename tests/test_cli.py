import configparser
import subprocess
from unittest.mock import patch

from click.testing import CliRunner

from weld.cli import main
from weld.types import ClassificationResult


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("test\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def test_install_registers_merge_driver_with_percent_placeholders(tmp_path, monkeypatch):
    """configparser의 기본 %-보간과 git의 %O %A %B %P 플레이스홀더가 충돌하면 안 된다."""
    _init_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["install"])

    assert result.exit_code == 0, result.output

    config = configparser.ConfigParser(interpolation=None)
    config.read(tmp_path / ".git" / "config")
    driver = config['merge "weld"']["driver"]
    assert driver == "weld merge %O %A %B %P"

    gitattributes = (tmp_path / ".gitattributes").read_text()
    assert "* merge=weld\n" in gitattributes


def test_merge_falls_back_to_conflict_markers_when_pipeline_raises(tmp_path):
    """다른 모듈이 아직 스텁(NotImplementedError)이든, 뭐가 됐든 파이프라인이
    예외로 죽으면 조용히 "ours" 버전만 남기지 말고 표준 충돌 마커를 써야 한다."""
    base_file = tmp_path / "base.py"
    ours_file = tmp_path / "ours.py"
    theirs_file = tmp_path / "theirs.py"
    base_file.write_text("x = 1\n")
    ours_file.write_text("x = 2\n")
    theirs_file.write_text("x = 3\n")

    runner = CliRunner()
    with (
        patch(
            "weld.cli.classify_conflict",
            return_value=ClassificationResult(is_spurious=False),
        ),
        patch("weld.cli.generate_candidates", side_effect=RuntimeError("아직 미구현")),
    ):
        result = runner.invoke(
            main, ["merge", str(base_file), str(ours_file), str(theirs_file), "src/x.py"]
        )

    assert result.exit_code == 1
    merged = ours_file.read_text()
    assert "<<<<<<< ours" in merged
    assert "x = 2" in merged
    assert "||||||| base" in merged
    assert "x = 1" in merged
    assert "=======" in merged
    assert "x = 3" in merged
    assert ">>>>>>> theirs" in merged
