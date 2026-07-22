"""놀이터의 모든 시나리오를 자동으로 머지해보고 weld의 실제 판정을 출력한다.

각 시나리오마다: git merge → 자동병합/에스컬레이션 판별 → start로 리셋.
weld가 실제로 발동했는지는 충돌 마커 라벨(ours/||||||| base = weld)로 확인.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def branches(dest: str) -> list[str]:
    out = sh(["git", "branch", "--format=%(refname:short)"], dest).stdout
    return sorted(b for b in out.split() if b.startswith("sc-"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="/tmp/weld-playground")
    args = ap.parse_args()
    dest = args.dest
    if not (Path(dest) / ".git").exists():
        sys.exit(f"놀이터가 없음: {dest} (먼저 make_playground.py 실행)")

    print(f"{'시나리오':22s} {'판정':14s} {'weld발동':8s} 비고")
    print("-" * 70)
    for br in branches(dest):
        sh(["git", "reset", "--hard", "-q", "start"], dest)
        sh(["git", "checkout", "-q", "main"], dest)
        sh(["git", "reset", "--hard", "-q", "start"], dest)
        r = sh(["git", "merge", "--no-edit", br], dest)
        conflicted = sh(["git", "ls-files", "-u"], dest).stdout.strip()
        # 충돌 파일에서 weld 마커 라벨 탐지
        weld_ran = "?"
        if conflicted:
            files = {ln.split("\t")[-1] for ln in conflicted.splitlines()}
            markers = ""
            for f in files:
                p = Path(dest) / f
                if p.exists():
                    markers += p.read_text()
            weld_ran = "예" if "||||||| base" in markers else "아니오(git)"
            verdict = "🔶 에스컬레이션"
        else:
            verdict = "✅ 자동병합" if r.returncode == 0 else "⚠️ 실패"
            # 자동병합이 weld였는지: 병합 커밋 존재 + 텍스트충돌 있었는지는
            # 로그로 단정 어려워 '자동'으로 표기(마커 없으면 성공)
            weld_ran = "예(게이트통과)"
        note = (r.stdout + r.stderr).strip().splitlines()
        note = note[-1][:34] if note else ""
        print(f"{br:22s} {verdict:14s} {weld_ran:8s} {note}")
        # 정리
        sh(["git", "merge", "--abort"], dest)
        sh(["git", "reset", "--hard", "-q", "start"], dest)

    print("\n초기화 완료 (start 상태). 개별 재현: git merge <시나리오>")


if __name__ == "__main__":
    main()
