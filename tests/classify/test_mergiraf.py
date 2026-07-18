import subprocess
from unittest.mock import patch

from weld.classify.mergiraf import classify_conflict


def _write_output(cmd, **kwargs):
    """subprocess.run 목(mock) — 명령의 `-o <path>`를 찾아 그 경로에 내용을 써준다."""
    output_path = cmd[cmd.index("-o") + 1]
    with open(output_path, "w") as f:
        f.write(_write_output.content)
    return subprocess.CompletedProcess(cmd, returncode=_write_output.returncode, stdout="", stderr="")


def test_classify_conflict_spurious_when_mergiraf_resolves_cleanly():
    _write_output.content = "def f():\n    return 1\n"
    _write_output.returncode = 0
    with patch("weld.classify.mergiraf.subprocess.run", side_effect=_write_output):
        result = classify_conflict(base="", ours="", theirs="")
    assert result.is_spurious is True
    assert result.resolved_content == "def f():\n    return 1\n"


def test_classify_conflict_real_when_mergiraf_exits_nonzero():
    _write_output.content = "<<<<<<< ours\n1\n=======\n2\n>>>>>>> theirs\n"
    _write_output.returncode = 1
    with patch("weld.classify.mergiraf.subprocess.run", side_effect=_write_output):
        result = classify_conflict(base="", ours="", theirs="")
    assert result.is_spurious is False


def test_classify_conflict_real_when_conflict_markers_remain_despite_exit_zero():
    _write_output.content = "<<<<<<< ours\n1\n=======\n2\n>>>>>>> theirs\n"
    _write_output.returncode = 0
    with patch("weld.classify.mergiraf.subprocess.run", side_effect=_write_output):
        result = classify_conflict(base="", ours="", theirs="")
    assert result.is_spurious is False


def test_classify_conflict_fails_safe_when_binary_missing():
    with patch("weld.classify.mergiraf.subprocess.run", side_effect=FileNotFoundError):
        result = classify_conflict(base="", ours="", theirs="")
    assert result.is_spurious is False


def test_classify_conflict_fails_safe_on_timeout():
    with patch(
        "weld.classify.mergiraf.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="mergiraf", timeout=30),
    ):
        result = classify_conflict(base="", ours="", theirs="")
    assert result.is_spurious is False
