"""공용 진입점 — 파이프라인을 각 모듈에서 가져와 연결만 한다.

이 파일은 오케스트레이션 전용이다. 실제 로직은 각 모듈(classify/candidates/
verify/policy/escalate)에 있으므로, 자기 파트를 채울 때는 보통 이 파일을
건드릴 필요가 없다. 새 파이프라인 단계를 추가할 때만 팀 전체와 상의 후 수정한다.
"""

from __future__ import annotations

import configparser
import dataclasses
import subprocess
import sys
from pathlib import Path

import click

from weld.candidates.generate import generate_candidates
from weld.candidates.summarize import summarize_intent
from weld.classify.mergiraf import classify_conflict
from weld.escalate.report import build_escalation_report
from weld.policy.trust import decide
from weld.types import EscalationReport, MergeCandidate
from weld.verify.impact import select_relevant_tests
from weld.verify.mutation import compute_mutation_score
from weld.verify.sandbox import run_candidates_parallel, run_in_sandbox

MERGE_DRIVER_NAME = "weld"


@click.group()
def main() -> None:
    """Weld — 검증되지 않은 병합은 자동으로 착지하지 못하게 막는 안전장치."""


@main.command()
@click.argument("base_file")
@click.argument("ours_file")
@click.argument("theirs_file")
@click.argument("path")
def merge(base_file: str, ours_file: str, theirs_file: str, path: str) -> None:
    """git merge driver 진입점: `weld merge %O %A %B %P`.

    %P는 저장소 내 실제 파일 경로 — %O/%A/%B는 그 파일의 세 리비전을 담은
    임시 파일이라, 후보를 검증/뮤테이션 테스트할 때 어디에 써야 할지는
    %P로만 알 수 있다.

    exit 0 → git이 자동 커밋. exit 1 → 표준 충돌 마커를 남기고 사람에게
    폴백(지금과 동일한 경험) — 검증할 후보가 없어서든, 파이프라인 자체가
    (아직 미완성인 다른 모듈 때문에) 예외로 죽어서든 마찬가지다. git이
    알아서 마커를 남겨주지 않으므로(커스텀 merge driver는 %A를 그대로
    working tree에 되돌려 쓸 뿐) 여기서 직접 쓴다 — 안 그러면 사람이
    아무 표시 없는 "ours" 버전만 보고 충돌을 못 알아챌 수 있다.
    """
    base = Path(base_file).read_text()
    ours = Path(ours_file).read_text()
    theirs = Path(theirs_file).read_text()

    try:
        changed_files = [path]
        relevant_tests = select_relevant_tests(changed_files, repo_path=".")

        classification = classify_conflict(base, ours, theirs)
        if classification.is_spurious:
            spurious_candidate = MergeCandidate(
                id="mergiraf-spurious",
                content=classification.resolved_content or "",
                strategy="mergiraf",
                file_path=path,
            )
            spurious_verification = run_in_sandbox(
                spurious_candidate, repo_path=".", tests=relevant_tests
            )
            if spurious_verification.compiled and spurious_verification.tests_passed:
                Path(ours_file).write_text(spurious_candidate.content)
                sys.exit(0)
            # mergiraf가 오판했을 수 있으니 테스트 실패 시 진짜 충돌 파이프라인으로 폴백.

        candidates = [
            dataclasses.replace(c, file_path=path)
            for c in generate_candidates(base, ours, theirs)
        ]
        verifications = run_candidates_parallel(candidates, repo_path=".", tests=relevant_tests)
        mutation_scores = [
            compute_mutation_score(c, relevant_tests, repo_path=".", base_content=base)
            for c in candidates
        ]

        for candidate, verification, mutation in zip(candidates, verifications, mutation_scores):
            decision = decide(verification, mutation)
            if decision.accepted:
                Path(ours_file).write_text(candidate.content)
                sys.exit(0)

        intent_summary = summarize_intent(base, ours, theirs)
        report = EscalationReport(
            intent_summary=intent_summary,
            candidates=candidates,
            verifications=verifications,
            mutation_scores=mutation_scores,
        )
        click.echo(build_escalation_report(report), err=True)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 사람에게 안전하게 폴백해야 함
        click.echo(f"weld: 검증 파이프라인이 실패해서 사람에게 폴백함: {exc}", err=True)

    _write_conflict_markers(ours_file, base, ours, theirs)
    sys.exit(1)


def _write_conflict_markers(ours_file: str, base: str, ours: str, theirs: str) -> None:
    """검증 통과한 후보가 없거나 파이프라인이 실패했을 때, git 기본 병합과
    똑같이 익숙한 충돌 마커를 파일에 남긴다."""
    marked = (
        "<<<<<<< ours\n"
        f"{ours}"
        "||||||| base\n"
        f"{base}"
        "=======\n"
        f"{theirs}"
        ">>>>>>> theirs\n"
    )
    Path(ours_file).write_text(marked)


@main.command()
def install() -> None:
    """현재 저장소에 weld를 git merge driver로 등록한다."""
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )

    # interpolation=None 필수: driver 값 안의 %O %A %B %P는 git이 해석할
    # 플레이스홀더라 configparser의 기본 %-보간 문법과 충돌해서 크래시한다.
    config = configparser.ConfigParser(interpolation=None)
    git_config_path = repo_root / ".git" / "config"
    config.read(git_config_path)
    section = f'merge "{MERGE_DRIVER_NAME}"'
    if section not in config:
        config[section] = {}
    config[section]["name"] = "Weld verified merge driver"
    config[section]["driver"] = f"{MERGE_DRIVER_NAME} merge %O %A %B %P"
    with git_config_path.open("w") as f:
        config.write(f)

    gitattributes_path = repo_root / ".gitattributes"
    line = f"* merge={MERGE_DRIVER_NAME}\n"
    existing = gitattributes_path.read_text() if gitattributes_path.exists() else ""
    if line not in existing:
        with gitattributes_path.open("a") as f:
            f.write(line)

    click.echo("weld merge driver installed.")


if __name__ == "__main__":
    main()
