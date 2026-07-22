"""배포 실전용 데모 저장소 빌더 — 실제 GitHub 원격에 올려 팀이 clone/merge.

make_playground.py와 같은 6개 상황별 충돌을 담되, 배포 현실에 맞춘다:
- 머지 드라이버 설정(.git/config)은 clone에 안 따라오므로, 각자 clone 후
  실행할 **포터블 setup-weld.sh**를 커밋한다(내 로컬 경로를 굽지 않음).
- `.gitattributes`(* merge=weld)는 커밋되어 따라온다.
- `.env`는 비밀이라 커밋 안 함(.gitignore) — 각자 로컬에 둔다.

흐름(팀원 관점):
  git clone <repo> && cd repo
  ./setup-weld.sh /path/to/weld        # 드라이버 등록 (1회)
  cp /path/to/weld/.env .              # LLM 설정
  git merge origin/sc-2-value          # weld 발동 관찰
  git reset --hard origin/main         # 초기화
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_playground import D, scenarios  # noqa: E402


def run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


SETUP = D("""\
    #!/bin/sh
    # weld 머지 드라이버를 이 클론에 등록한다 (clone엔 .git/config가 안 따라오므로 1회 실행).
    # 사용법: ./setup-weld.sh /path/to/weld   (weld/src 가 있는 저장소 루트)
    set -e
    WELD_ROOT="${1:?사용법: ./setup-weld.sh /path/to/weld (weld 저장소 루트)}"
    WELD_SRC="$WELD_ROOT/src"
    PY="${WELD_PY:-python3}"
    if [ ! -d "$WELD_SRC/weld" ]; then
      echo "오류: $WELD_SRC 에 weld 패키지가 없음. weld 저장소 루트를 넘기세요." >&2
      exit 1
    fi
    git config merge.weld.name "Weld verified merge driver"
    git config merge.weld.driver "env PYTHONPATH=$WELD_SRC $PY -m weld.cli merge %O %A %B %P"
    echo "✅ weld 머지 드라이버 등록 완료 (python=$PY)"
    if [ ! -f .env ]; then
      echo "⚠️  .env 가 없습니다. LLM 시나리오를 보려면 weld의 .env 를 여기로 복사하세요:"
      echo "    cp $WELD_ROOT/.env ."
    fi
    """)


def readme(scns) -> str:
    lines = [
        "# Weld 배포 데모 저장소",
        "",
        "실제 배포처럼 `weld`를 git 머지 드라이버로 써서, 충돌 병합을 자동 검증한다.",
        "원칙: **놓친 자동화(에스컬레이션)는 있어도, 잘못된 자동화(오탐)는 없다.**",
        "",
        "## 셋업",
        "**① 컴퓨터에 weld가 처음이면 (1회)** — weld 저장소에서:",
        "```bash",
        "git clone <weld 저장소 URL> && cd weld && ./install.sh",
        "open ~/.config/weld/env      # LLM 키 입력 (선택)",
        "```",
        "**② 이 데모 저장소에 적용 (clone 후 1회)**:",
        "```bash",
        "git clone <이 저장소> && cd weld-merge-demo",
        "weld install                 # 이 저장소에 머지 드라이버 등록",
        "```",
        "> 저장소마다 `weld install` 한 번 필요(`.gitattributes` 는 자동 적용)."
        " install.sh 안 썼으면 `./setup-weld.sh /path/to/weld` 대안도 됨.",
        "",
        "## 충돌 시나리오 (각 브랜치를 main에 병합)",
        "",
        "| 명령 | 상황 | 기대 | LLM |",
        "|---|---|---|---|",
    ]
    for sc in scns:
        llm = ("불필요" if "불필요" in sc.expect else
               "무관" if "무관" in sc.expect else "필요")
        exp = "✅ 자동병합" if "자동병합" in sc.expect else "🔶 에스컬레이션"
        lines.append(f"| `git merge origin/{sc.key}-{sc.name}` | {sc.desc} | {exp} | {llm} |")
    lines += [
        "",
        "## 실행 예시",
        "```bash",
        "git merge origin/sc-2-value      # → 자동병합 (LLM 불필요)",
        "git log -1 --oneline",
        "git reset --hard origin/main     # 초기화",
        "",
        "git merge origin/sc-6-break      # → 에스컬레이션 (충돌 마커)",
        "cat src/break_case.py            # <<<<<<< ours / ||||||| base 마커 = weld가 남긴 것",
        "git merge --abort",
        "```",
        "",
        "## LLM(.env) 상태 주의",
        "`[LLM 필요]` 시나리오는 `.env`의 LLM이 응답해야 자동병합까지 간다.",
        "키가 크레딧 고갈/장애면 **안전하게 에스컬레이션**된다(오탐 없음).",
        "weld는 429/5xx를 지수 백오프로 자동 재시도한다.",
        "",
        "## 판별법",
        "- **자동병합**: `git merge` 성공 + 병합 커밋 (마커 없음)",
        "- **에스컬레이션**: 병합 멈춤 + `<<<<<<< ours` / `||||||| base` 마커"
        " (이 라벨이 weld가 남긴 증거 — git 기본은 `<<<<<<< HEAD`)",
    ]
    return "\n".join(lines) + "\n"


def build(dest: str) -> list:
    root = Path(dest)
    if root.exists():
        import shutil
        shutil.rmtree(root)
    root.mkdir(parents=True)
    scns = scenarios()

    run(["git", "init", "-q", "-b", "main"], root)
    run(["git", "config", "user.email", "demo@weld"], root)
    run(["git", "config", "user.name", "weld demo"], root)

    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "__init__.py").write_text("")
    (root / "tests" / "__init__.py").write_text("")
    (root / ".gitattributes").write_text("* merge=weld\n")
    (root / ".gitignore").write_text(".env\n__pycache__/\n*.pyc\n.weld_cache/\n")
    (root / "setup-weld.sh").write_text(SETUP)
    (root / "setup-weld.sh").chmod(0o755)

    # base 커밋
    for sc in scns:
        (root / sc.src).write_text(sc.base)
    run(["git", "add", "-A"], root)
    run(["git", "commit", "-q", "-m", "base: 시나리오 공통 조상 + weld 설정"], root)
    base_sha = run(["git", "rev-parse", "HEAD"], root).stdout.strip()

    # theirs 브랜치들
    for sc in scns:
        run(["git", "checkout", "-q", "-b", f"{sc.key}-{sc.name}", base_sha], root)
        (root / sc.src).write_text(sc.theirs)
        for p, c in sc.theirs_add.items():
            fp = root / p
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(c)
        run(["git", "add", "-A"], root)
        run(["git", "commit", "-q", "-m", f"{sc.name}: theirs 브랜치"], root)

    # main = ours + README
    run(["git", "checkout", "-q", "main"], root)
    for sc in scns:
        (root / sc.src).write_text(sc.ours)
        for p, c in sc.ours_add.items():
            fp = root / p
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(c)
    (root / "README.md").write_text(readme(scns))
    run(["git", "add", "-A"], root)
    run(["git", "commit", "-q", "-m", "main: ours 변경 + 테스트 + README"], root)
    return scns


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    args = ap.parse_args()
    scns = build(args.dest)
    print(f"✅ 로컬 저장소 빌드: {args.dest}")
    print("브랜치:", ", ".join(f"{s.key}-{s.name}" for s in scns))
