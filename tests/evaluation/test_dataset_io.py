"""save_cases / load_cases 라운드트립 테스트 — 채굴한 평가셋을 파일로 저장했다가
그대로 복원되는지 확인한다."""

from __future__ import annotations

from weld.evaluation.cases import EvalCase, load_cases, save_cases


def test_save_load_roundtrip(tmp_path):
    cases = [
        EvalCase(
            id="c1",
            base="x = 1\n",
            ours="x = 2\n",
            theirs="x = 3\n",
            file_path="m.py",
            relevant_tests=["t.py::test_x"],
            ground_truth_resolution="x = 2\n",
            repo_coverage=0.8,
            source_repo="/repos/foo",
            source_commit="abcdef123456",
        ),
        EvalCase(id="c2", base="", ours="a\n", theirs="b\n", file_path="n.py"),
    ]
    path = tmp_path / "dataset.json"
    save_cases(cases, str(path))

    assert path.exists()
    loaded = load_cases(str(path))
    assert loaded == cases  # frozen dataclass 동등성으로 완전 일치 확인
