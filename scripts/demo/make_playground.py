"""실사용 테스트 놀이터 생성기 — 진짜 git merge로 weld를 돌려본다.

상황별(가짜충돌/값충돌/구조합성-강/구조합성-약/버그측/깨짐) 충돌을 심은
git 저장소를 만들고 weld를 머지 드라이버로 설치한다. 사용자는 그냥
`git merge <시나리오>` 하면 weld가 자동병합(exit 0, 커밋)하거나
에스컬레이션(충돌 마커)하는 걸 눈으로 본다.

구조:
  main 브랜치  = 각 시나리오 파일의 'ours' + ours가 추가한 테스트 (초록 상태)
  sc-<n>-<name> = base에서 갈라져 'theirs'를 담은 브랜치
  git merge sc-<n> → 해당 파일에서만 3-way 충돌 → weld 드라이버 발동

사용:
  python scripts/demo/make_playground.py --dest /tmp/weld-playground
  (--python, --weld-src 는 기본값이 이 저장소 기준으로 잡힘)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

D = textwrap.dedent
WELD_ROOT = Path(__file__).resolve().parents[2]


class Scn:
    def __init__(self, key, name, desc, expect, base, ours, theirs,
                 ours_add=None, theirs_add=None):
        self.key = key            # sc-1 ...
        self.name = name          # spurious ...
        self.desc = desc          # 한 줄 설명
        self.expect = expect      # 기대 동작 (자동/에스컬레이션)
        self.src = f"src/{name.replace('-', '_')}.py"
        self.base = base          # 공통 조상 소스
        self.ours = ours          # main 소스
        self.theirs = theirs      # 시나리오 브랜치 소스
        self.ours_add = ours_add or {}     # main에만 추가할 파일(주로 테스트)
        self.theirs_add = theirs_add or {}  # 브랜치에만 추가할 파일


def scenarios() -> list[Scn]:
    s = []
    # 1) 독립적 동시 추가 — 양쪽이 같은 위치에 서로 다른 헬퍼를 추가(텍스트 충돌).
    #    겹치는 편집은 아니라 LLM이 둘 다 보존하면 통과. [LLM 필요]
    base = D("""\
        def add(a, b):
            return a + b
        """)
    ours = D("""\
        def add(a, b):
            return a + b


        def double(x):
            return x * 2
        """)
    theirs = D("""\
        def add(a, b):
            return a + b


        def triple(x):
            return x * 3
        """)
    s.append(Scn(
        "sc-1", "concurrent-add", "양쪽이 같은 위치에 독립적인 함수를 추가",
        "자동병합 기대 [LLM 필요] — 둘 다 보존하는 합성",
        base, ours, theirs,
        ours_add={"tests/test_concurrent_ours.py": D("""\
            from src.concurrent_add import add, double
            def test_add(): assert add(2, 3) == 5
            def test_double(): assert double(4) == 8
            """)},
        theirs_add={"tests/test_concurrent_theirs.py": D("""\
            from src.concurrent_add import triple
            def test_triple(): assert triple(4) == 12
            """)},
    ))
    # 2) 값충돌 — 같은 상수 양쪽 변경. 테스트가 옳은 쪽(ours)을 고름. LLM 0%.
    base = D("""\
        MAX_RETRIES = 3


        def retries_left(used):
            return MAX_RETRIES - used
        """)
    s.append(Scn(
        "sc-2", "value", "같은 상수를 양쪽이 다른 값으로 변경",
        "자동병합 [LLM 불필요] — 테스트 통과하는 쪽(verbatim) 채택",
        base,
        base.replace("MAX_RETRIES = 3", "MAX_RETRIES = 5"),
        base.replace("MAX_RETRIES = 3", "MAX_RETRIES = 8"),
        ours_add={"tests/test_value.py": D("""\
            from src.value import retries_left
            def test_left(): assert retries_left(0) == 5
            def test_used(): assert retries_left(2) == 3
            """)},
    ))
    # 3) 구조합성-강 — 양쪽이 서로 다른 가드 추가. 강한 테스트가 둘 다 요구.
    #    LLM이 두 가드를 합쳐야만 통과 → 자동병합.
    base = D("""\
        def parse_port(s):
            n = int(s)
            return n
        """)
    ours = D("""\
        def parse_port(s):
            n = int(s)
            if n < 0:
                raise ValueError("negative port")
            return n
        """)
    theirs = D("""\
        def parse_port(s):
            n = int(s)
            if n > 65535:
                raise ValueError("port too large")
            return n
        """)
    s.append(Scn(
        "sc-3", "struct-strong", "양쪽이 서로 다른 검증 가드를 추가 (강한 테스트)",
        "자동병합 [LLM 필요] — 두 가드를 합성, 강한 테스트가 검증",
        base, ours, theirs,
        ours_add={"tests/test_struct_strong_ours.py": D("""\
            import pytest
            from src.struct_strong import parse_port
            def test_ok(): assert parse_port("8080") == 8080
            def test_low_boundary(): assert parse_port("0") == 0
            def test_negative():
                with pytest.raises(ValueError, match="negative"):
                    parse_port("-1")
            """)},
        theirs_add={"tests/test_struct_strong_theirs.py": D("""\
            import pytest
            from src.struct_strong import parse_port
            def test_high_boundary(): assert parse_port("65535") == 65535
            def test_too_big():
                with pytest.raises(ValueError, match="large"):
                    parse_port("70000")
            """)},
    ))
    # 4) 구조합성-약 — 같은 합성이 필요하지만 theirs 쪽 가드엔 테스트가 없음.
    #    뮤테이션이 그 가드를 kill 못함 → 저점수 → 에스컬레이션.
    base = D("""\
        def validate_age(age):
            return age
        """)
    ours = D("""\
        def validate_age(age):
            if age < 0:
                raise ValueError("negative age")
            return age
        """)
    theirs = D("""\
        def validate_age(age):
            if age > 150:
                raise ValueError("age too large")
            return age
        """)
    s.append(Scn(
        "sc-4", "struct-weak", "구조합성 필요하지만 한쪽 가드에 테스트가 없음(약한 테스트)",
        "에스컬레이션 [LLM 시도] — 약한 테스트라 뮤테이션 저점수",
        base, ours, theirs,
        ours_add={"tests/test_struct_weak.py": D("""\
            from src.struct_weak import validate_age
            def test_ok(): assert validate_age(30) == 30
            """)},
    ))
    # 5) 버그측 — theirs가 ours의 안전장치를 지우는 재작성. 올바른 합성만 통과.
    base = D("""\
        def clamp(x, lo, hi):
            if x < lo:
                return lo
            if x > hi:
                return hi
            return x
        """)
    ours = D("""\
        def clamp(x, lo, hi):
            if lo > hi:
                raise ValueError("bad range")
            if x < lo:
                return lo
            if x > hi:
                return hi
            return x
        """)
    theirs = D("""\
        def clamp(x, lo, hi):
            return min(max(x, lo), hi)
        """)
    s.append(Scn(
        "sc-5", "bug-side", "theirs 재작성이 ours의 안전장치(범위검사)를 삭제",
        "자동병합 [LLM 필요] — 안전장치 보존한 합성만 통과",
        base, ours, theirs,
        ours_add={"tests/test_bug_side.py": D("""\
            import pytest
            from src.bug_side import clamp
            def test_mid(): assert clamp(5, 0, 10) == 5
            def test_low(): assert clamp(-1, 0, 10) == 0
            def test_high(): assert clamp(11, 0, 10) == 10
            def test_edges():
                assert clamp(0, 0, 10) == 0
                assert clamp(10, 0, 10) == 10
            def test_point_range(): assert clamp(5, 5, 5) == 5
            def test_bad_range():
                with pytest.raises(ValueError, match="bad range"):
                    clamp(1, 5, 2)
            """)},
    ))
    # 6) 깨짐 — theirs가 미정의 함수 호출. 컴파일/테스트 게이트가 잡음.
    base = D("""\
        def normalize(s):
            return s.strip()
        """)
    ours = D("""\
        def normalize(s):
            return s.strip().lower()
        """)
    theirs = D("""\
        def normalize(s):
            audit_log(s)
            return s.strip()
        """)
    s.append(Scn(
        "sc-6", "break", "theirs가 미정의 함수(audit_log)를 호출 — 병합하면 깨짐",
        "에스컬레이션 [LLM 무관] — 컴파일/테스트 게이트가 차단",
        base, ours, theirs,
        ours_add={"tests/test_break.py": D("""\
            from src.break_case import normalize
            def test_lower(): assert normalize("  A ") == "a"
            def test_plain(): assert normalize("x") == "x"
            """)},
    ))
    # break 시나리오는 파일명이 예약어라 별도 경로
    s[-1].src = "src/break_case.py"
    return s


def _run(cmd, cwd, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


def build(dest: str, py: str, weld_src: str, env_file: str) -> None:
    root = Path(dest)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    scns = scenarios()

    # git init
    _run(["git", "init", "-q", "-b", "main"], root)
    _run(["git", "config", "user.email", "demo@weld"], root)
    _run(["git", "config", "user.name", "weld demo"], root)

    # weld 머지 드라이버 래퍼 (PYTHONPATH/인터프리터 고정)
    wrapper = root / ".weld-driver.sh"
    wrapper.write_text(D(f"""\
        #!/bin/sh
        # weld 머지 드라이버 래퍼 — git이 %O %A %B %P 를 넘겨 호출.
        exec env PYTHONPATH="{weld_src}" "{py}" -m weld.cli merge "$@"
        """))
    wrapper.chmod(0o755)
    # .git/config 에 드라이버 등록 + 모든 파일에 적용
    _run(["git", "config", "merge.weld.name", "Weld verified merge driver"], root)
    _run(["git", "config", "merge.weld.driver",
          f"{wrapper} %O %A %B %P"], root)
    (root / ".gitattributes").write_text("* merge=weld\n")

    # .env 복사 (LLM provider 설정 — 구조합성 시나리오에 필요)
    if env_file and Path(env_file).exists():
        shutil.copy(env_file, root / ".env")

    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "__init__.py").write_text("")
    (root / "tests" / "__init__.py").write_text("")

    # 1) base 커밋 — 모든 시나리오의 base 소스 + __init__
    for sc in scns:
        (root / sc.src).write_text(sc.base)
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-q", "-m", "base: 모든 시나리오 공통 조상"], root)
    base_sha = _run(["git", "rev-parse", "HEAD"], root).stdout.strip()

    # 2) 각 시나리오 브랜치(theirs) 생성
    for sc in scns:
        _run(["git", "checkout", "-q", "-b", f"{sc.key}-{sc.name}", base_sha], root)
        (root / sc.src).write_text(sc.theirs)
        for p, c in sc.theirs_add.items():
            fp = root / p
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(c)
        _run(["git", "add", "-A"], root)
        _run(["git", "commit", "-q", "-m", f"{sc.name}: theirs"], root)

    # 3) main 으로 돌아와 ours + ours 테스트 커밋
    _run(["git", "checkout", "-q", "main"], root)
    for sc in scns:
        (root / sc.src).write_text(sc.ours)
        for p, c in sc.ours_add.items():
            fp = root / p
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(c)
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-q", "-m", "ours: main 쪽 변경 + 테스트"], root)
    # 리셋 지점 고정 — 성공 머지 후 main이 전진하므로 되돌릴 앵커가 필요.
    _run(["git", "tag", "start"], root)

    # 안내문
    _write_readme(root, scns, py)
    print(f"✅ 놀이터 생성: {dest}")
    print(f"   cd {dest} && cat README_TEST.md")


def _write_readme(root: Path, scns: list[Scn], py: str) -> None:
    lines = [
        "# Weld 실사용 테스트 놀이터",
        "",
        "`git merge`를 직접 실행해 weld 머지 드라이버가 자동병합(exit 0)하는지",
        "에스컬레이션(충돌 마커)하는지 눈으로 확인한다.",
        "",
        "## 준비 — 먼저 LLM(.env) 상태 확인",
        "",
        "`[LLM 필요]` 시나리오는 `.env`의 LLM이 실제로 응답해야 자동병합까지 간다.",
        "이 놀이터의 `.env`는 weld 저장소 것을 복사한 것.",
        "",
        "```bash",
        "# 지금 어떤 LLM을 쓰는지 + 실제 응답하는지 확인",
        "python - <<'PY'",
        "import os; from dotenv import load_dotenv; load_dotenv()",
        "print('provider =', 'custom:'+os.environ['WELD_LLM_BASE_URL']"
        " if os.environ.get('WELD_LLM_BASE_URL') else 'gemini:'+os.environ.get('GEMINI_MODEL','?'))",
        "PY",
        "```",
        "",
        "> ⚠️ **알려진 상태**: 현재 `.env`는 **Gemini**로 설정돼 있는데 그 키가"
        " `RESOURCE_EXHAUSTED`(크레딧 고갈, 429)를 낸다. 이 경우 `[LLM 필요]`"
        " 시나리오는 **API 실패로 안전하게 에스컬레이션**된다(오탐은 없음).",
        "> 자동병합까지 보려면 **크레딧 있는 Gemini 키**로 `.env`의 `GEMINI_API_KEY`를"
        " 교체하거나, 친구 qwen을 쓰려면 `WELD_LLM_BASE_URL/MODEL/API_KEY`를 채우고"
        " 캠프 VPN을 켠다. weld는 429/5xx를 자동 백오프 재시도한다.",
        "",
        "- **`[LLM 불필요]`(sc-2)와 `[LLM 무관]`(sc-6)** 은 위 상태와 무관하게 동작한다.",
        "",
        "## 시나리오",
        "",
        "| 명령 | 상황 | 기대 결과 |",
        "|---|---|---|",
    ]
    for sc in scns:
        lines.append(f"| `git merge {sc.key}-{sc.name}` | {sc.desc} | {sc.expect} |")
    lines += [
        "",
        "## 실행 예시",
        "```bash",
        "git merge sc-1-spurious      # → weld: 자동병합, git이 커밋",
        "git log -1 --oneline         # 병합 커밋 확인",
        "git reset --hard start       # 초기화 (성공 머지는 main을 전진시키므로)",
        "",
        "git merge sc-4-struct-weak   # → weld: 에스컬레이션",
        "git status                   # 충돌 파일 확인",
        "cat src/struct_weak.py       # <<<<<<< ours 마커 확인 (weld가 남긴 것)",
        "git merge --abort            # 되돌리기",
        "```",
        "",
        "## 한 번에 전부 (스크립트)",
        "```bash",
        "python scripts/demo/run_all_scenarios.py --dest <이 놀이터 경로>",
        "```",
        "",
        "## 판별법",
        "- **자동병합**: `git merge` 성공 + 병합 커밋 생성 (충돌 마커 없음)",
        "- **에스컬레이션**: 병합 멈춤 + 파일에 `<<<<<<< ours` / `||||||| base` 마커"
        " (이 라벨이 weld가 남긴 증거 — git 기본은 `<<<<<<< HEAD`)",
        "",
        "**초기화**: 성공 머지 후엔 `git reset --hard start`,"
        " 에스컬레이션(충돌) 상태에선 `git merge --abort`.",
    ]
    (root / "README_TEST.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="/tmp/weld-playground")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--weld-src", default=str(WELD_ROOT / "src"))
    ap.add_argument("--env", default=str(WELD_ROOT / ".env"))
    args = ap.parse_args()
    build(args.dest, args.python, args.weld_src, args.env)
