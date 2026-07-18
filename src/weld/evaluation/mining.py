"""실제 git 저장소의 병합 히스토리에서 진짜 충돌을 캐내 EvalCase로 만든다.

합성 테스트(우리가 지어낸 충돌)를 넘어, 실제 오픈소스 저장소의 과거 병합에서
실제로 충돌했던 파일을 꺼낸다. 각 충돌에 대해:
  - base       = 병합 기준(merge-base)에서의 파일
  - ours       = 부모1(P1)에서의 파일
  - theirs     = 부모2(P2)에서의 파일
  - resolution = 병합 커밋 자체에서의 파일  ← 과거에 실제로 채택된 정답(ground truth)

이 resolution이 있어서, Weld가 자동 병합한 결과가 "사람이 실제로 고른 것"과
일치하는지 대조해 오탐률을 잴 수 있다.

relevant_tests / repo_coverage는 여기서 안 채운다 — 평가 실행 시점에
impact.py(테스트 선별)와 커버리지 측정으로 붙인다.

요구사항: git 2.38+ (merge-tree --write-tree). 이 모듈은 머지 드라이버 본체가
아니라 평가용 도구다.
"""

from __future__ import annotations

import re
import subprocess

from weld.evaluation.cases import EvalCase

_STAGE_LINE = re.compile(r"^\d{6} [0-9a-f]+ [123]\t(.+)$")


def _git(repo_path: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_path, *args], capture_output=True, text=True
    )


def _file_at(repo_path: str, ref: str, path: str) -> str | None:
    """ref 시점의 path 파일 내용. 그 시점에 파일이 없으면 None."""
    result = _git(repo_path, ["show", f"{ref}:{path}"])
    return result.stdout if result.returncode == 0 else None


def _conflicted_paths(repo_path: str, p1: str, p2: str) -> list[str]:
    """P1과 P2를 병합했을 때 충돌하는 파일 경로들. 충돌이 없으면 빈 리스트.

    merge-tree 출력의 stage 라인(mode oid stage\\tpath)만 파싱한다 —
    아래 '자동 병합/충돌' 안내 메시지는 로케일에 따라 번역되므로 안 쓴다.
    """
    result = _git(repo_path, ["merge-tree", "--write-tree", p1, p2])
    if result.returncode == 0:
        return []  # 깔끔하게 병합됨 = 충돌 없음
    paths: list[str] = []
    for line in result.stdout.splitlines():
        match = _STAGE_LINE.match(line)
        if match and match.group(1) not in paths:
            paths.append(match.group(1))
    return paths


def _is_two_sided_conflict(base: str | None, ours: str, theirs: str) -> bool:
    """양쪽(ours/theirs)이 모두 base와 다른 진짜 충돌인지.

    한쪽이라도 base와 같으면 그쪽은 안 바뀐 것 → 진짜 두 갈래 충돌이 아니다
    (git이라면 바뀐 쪽으로 그냥 병합됨). merge-tree가 rename 등 다른 이유로
    충돌 표시한 경우를 걸러 평가셋 오염을 막는다. base가 None이면 양쪽에서 새로
    추가된 파일(add/add)이라 진짜 충돌로 본다.
    """
    if base is None:
        return True
    return base != ours and base != theirs


def mine_conflicts(
    repo_path: str, *, max_cases: int | None = None, python_only: bool = True
) -> list[EvalCase]:
    """저장소의 병합 커밋들을 훑어 실제 충돌을 EvalCase로 반환한다.

    max_cases: 이 개수만큼 모으면 멈춘다(작게 시작하기 좋게). None이면 전부.
    python_only: .py 충돌만 수집(언어 스코프 Python 고정 MVP에 맞춤).
    """
    merges = _git(repo_path, ["rev-list", "--merges", "HEAD"]).stdout.split()
    cases: list[EvalCase] = []

    for merge in merges:
        parents = _git(repo_path, ["rev-parse", f"{merge}^@"]).stdout.split()
        if len(parents) != 2:
            continue  # octopus 병합 등은 건너뜀
        p1, p2 = parents
        base_ref = _git(repo_path, ["merge-base", p1, p2]).stdout.strip()

        for path in _conflicted_paths(repo_path, p1, p2):
            if max_cases is not None and len(cases) >= max_cases:
                return cases
            if python_only and not path.endswith(".py"):
                continue

            ours = _file_at(repo_path, p1, path)
            theirs = _file_at(repo_path, p2, path)
            if ours is None or theirs is None:
                continue  # 한쪽에서 삭제/이동된 경우 — 값 대조가 애매하니 제외

            base = _file_at(repo_path, base_ref, path) if base_ref else None
            if not _is_two_sided_conflict(base, ours, theirs):
                continue  # 한쪽만 바뀐 가짜 충돌 — 평가셋 오염 방지

            resolution = _file_at(repo_path, merge, path)

            cases.append(
                EvalCase(
                    id=f"{merge[:8]}-{path}",
                    base=base or "",
                    ours=ours,
                    theirs=theirs,
                    file_path=path,
                    ground_truth_resolution=resolution,
                    source_repo=repo_path,
                    source_commit=merge,
                )
            )

    return cases
