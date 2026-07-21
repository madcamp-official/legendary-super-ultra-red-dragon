"""Weld end-to-end 데모 하네스.

진짜 임시 git 저장소를 만들어 충돌 상황을 심고, 실제 `git merge`를 돌려서
Weld 머지 드라이버가 무엇을 하는지 눈으로 보여준다. 부품별 테스트와 달리
분류→후보생성→검증→판정→에스컬레이션 파이프라인 전체를 실제 git 위에서 이어
돌린다 — 통합이 됐는지 확인하는 용도이자, 발표 시연용.

실행 전 준비:
    pip install -e .        # weld 명령이 PATH에 있어야 함 (git이 이걸 호출)

실행:
    python demo/run_demo.py

"살아있는 데모": 이서영님 파트(candidates/policy/escalate)가 stub인 동안에는
'진짜 충돌' 시나리오가 표준 충돌 마커로 안전하게 폴백된다. 그 파트가 들어오면
같은 스크립트에서 실제 LLM 후보 생성 → 검증 → 자동병합/에스컬레이션까지 돈다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

# 한글 경로에서 editable install이 간헐적으로 깨지는 것에 대비해 PYTHONPATH를
# 심어, git이 부르는 `weld` 서브프로세스도 weld 패키지를 확실히 찾게 한다.
ENV = os.environ.copy()
ENV["PYTHONPATH"] = str(SRC) + (
    os.pathsep + ENV["PYTHONPATH"] if ENV.get("PYTHONPATH") else ""
)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=ENV, capture_output=True, text=True)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return _run(["git", *args], cwd)


def _init_repo(root: Path) -> None:
    _git(["init", "-q"], root)
    _git(["config", "user.email", "demo@weld.local"], root)
    _git(["config", "user.name", "Weld Demo"], root)


def _commit_all(root: Path, message: str) -> None:
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", message], root)


def _install_weld(root: Path) -> bool:
    result = _run(["weld", "install"], root)
    if result.returncode != 0:
        print("  ✗ `weld install` 실패 — `pip install -e .`로 weld를 먼저 설치하세요.")
        print("   ", (result.stderr or result.stdout).strip().splitlines()[-1:])
        return False
    return True


def _print_file(root: Path, rel: str) -> None:
    content = (root / rel).read_text()
    print(f"  ── {rel} 최종 내용 " + "─" * 30)
    for line in content.splitlines():
        print(f"    {line}")
    print("  " + "─" * 48)


def _has_conflict_markers(root: Path, rel: str) -> bool:
    text = (root / rel).read_text()
    return "<<<<<<<" in text and ">>>>>>>" in text


def scenario_spurious(root: Path) -> None:
    """가짜 충돌: A와 B가 서로 다른 함수를 건드림 → mergiraf가 자동 병합.

    테스트 파일을 반드시 같이 심어야 한다 — 저장소에 테스트가 하나도 없으면
    pytest가 "수집된 테스트 없음"(exit 5)을 내고, verify/sandbox.py가 이걸
    tests_passed=False로 기록해버려서 mergiraf가 완벽하게 병합한 결과조차
    재검증에 실패해 사람에게 폴백되는 사고가 난다(실측으로 확인됨).
    """
    print("\n" + "=" * 60)
    print("시나리오 ① 가짜 충돌 (구조적으로 안 겹침) → mergiraf 자동 병합")
    print("=" * 60)
    print("  base: add() 와 multiply() 두 함수")
    print("  A(feature): add() 안에 로그 한 줄 추가")
    print("  B(main):    새 함수 subtract() 추가")
    print("  → 서로 다른 곳을 고쳤으니 진짜 충돌이 아니다.")

    _init_repo(root)
    (root / "conftest.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n"
    )
    calc = "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n"
    (root / "calc.py").write_text(calc)
    (root / "test_calc.py").write_text(
        "from calc import add, multiply\n\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_multiply():\n    assert multiply(2, 3) == 6\n"
    )
    _commit_all(root, "base + test")
    _install_weld(root)
    _commit_all(root, "weld 설치")
    base_branch = _git(["branch", "--show-current"], root).stdout.strip()

    _git(["checkout", "-q", "-b", "feature"], root)
    (root / "calc.py").write_text(
        'def add(a, b):\n    print("adding")\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n'
    )
    _commit_all(root, "A: add에 로그 추가")

    _git(["checkout", "-q", base_branch], root)
    (root / "calc.py").write_text(calc + "\n\ndef subtract(a, b):\n    return a - b\n")
    _commit_all(root, "B: subtract 추가")

    result = _git(["merge", "feature", "-m", "merge feature"], root)
    print(f"\n  git merge 종료코드: {result.returncode}")
    if result.returncode == 0 and not _has_conflict_markers(root, "calc.py"):
        print("  ✅ Weld가 자동 병합했다 (사람 개입 없이 착지).")
    elif _has_conflict_markers(root, "calc.py"):
        print("  🟡 사람에게 폴백됨 — 아래 stderr를 보면 어느 단계에서 막혔는지 보인다.")
        if result.stderr.strip():
            print("  ── weld 진단 (stderr) " + "─" * 30)
            for line in result.stderr.strip().splitlines():
                print(f"    {line}")
    else:
        print("  ⚠️ 예상 밖 상태.")
    _print_file(root, "calc.py")


def scenario_llm_synthesis(root: Path) -> None:
    """구조적 충돌: A와 B가 같은 지점(함수 초입)에 서로 다른 로직을 삽입.

    git의 텍스트 3-way 병합(diff3)조차 같은 위치를 건드린 두 삽입을 자동으로
    합치지 못해 진짜 충돌로 남는다 — 값 충돌(같은 줄, 다른 값)과 달리 두 로직을
    "모두 살리는" 합성이 필요해서 candidates/generate.py의 LLM 훙크 생성이
    실제로 개입한다. 테스트가 두 로직을 각각 독립적으로 검증하므로, 올바르게
    합친 후보만 검증+뮤테이션 게이트를 통과해 자동 병합된다.

    GEMINI_API_KEY가 없거나 만료됐으면 이 시나리오는 안전하게 사람에게
    폴백된다(크래시하지 않음) — 그 자체로 fail-safe 설계가 맞다는 증거다.
    """
    print("\n" + "=" * 60)
    print("시나리오 ② 구조적 충돌 (두 기능이 같은 지점에 삽입) → LLM 합성 자동 병합")
    print("=" * 60)
    print("  base: calculate_discount(price, is_member) → 항상 원가 그대로 반환")
    print("  A(feature): 멤버면 10% 할인 로직을 함수 초입에 추가")
    print("  B(main):    가격 100 초과 시 5% 할인 로직을 같은 자리에 추가")
    print("  → 같은 위치에 서로 다른 조건을 삽입 → git이 자동 병합 못 하는 진짜 충돌")
    print("  테스트: 멤버 할인 / 대량구매 할인 / 일반가 세 케이스를 각각 검증")

    _init_repo(root)
    (root / "conftest.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n"
    )
    (root / "pricing2.py").write_text(
        "def calculate_discount(price, is_member):\n    return price\n"
    )
    (root / "test_pricing2.py").write_text(
        "from pricing2 import calculate_discount\n\n\n"
        "def test_member_discount():\n"
        "    assert calculate_discount(50, True) == 45.0\n\n\n"
        "def test_bulk_discount():\n"
        "    assert calculate_discount(150, False) == 142.5\n\n\n"
        "def test_regular_price():\n"
        "    assert calculate_discount(50, False) == 50\n"
    )
    _commit_all(root, "base + test")
    if not _install_weld(root):
        return
    _commit_all(root, "weld 설치")
    base_branch = _git(["branch", "--show-current"], root).stdout.strip()

    _git(["checkout", "-q", "-b", "feature"], root)
    (root / "pricing2.py").write_text(
        "def calculate_discount(price, is_member):\n"
        "    if is_member:\n"
        "        return price * 0.9\n"
        "    return price\n"
    )
    _commit_all(root, "A: 멤버 할인 추가")

    _git(["checkout", "-q", base_branch], root)
    (root / "pricing2.py").write_text(
        "def calculate_discount(price, is_member):\n"
        "    if price > 100:\n"
        "        return price * 0.95\n"
        "    return price\n"
    )
    _commit_all(root, "B: 대량구매 할인 추가")

    result = _git(["merge", "feature", "-m", "merge feature"], root)
    print(f"\n  git merge 종료코드: {result.returncode}")
    if result.returncode == 0 and not _has_conflict_markers(root, "pricing2.py"):
        print("  ✅ Weld가 LLM으로 합성한 후보를 검증 통과시켜 자동 병합했다.")
    elif _has_conflict_markers(root, "pricing2.py"):
        print("  🟡 검증 통과 후보가 없어 사람에게 폴백됨.")
        if result.stderr.strip():
            print("  ── weld 진단 (stderr) " + "─" * 30)
            for line in result.stderr.strip().splitlines():
                print(f"    {line}")
    else:
        print("  ⚠️ 예상 밖 상태.")
    _print_file(root, "pricing2.py")


def scenario_mutation_catch(root: Path) -> None:
    """구조적 충돌 + 부실한 테스트: 하한/상한 두 검증 로직을 합성해야 하는 진짜
    충돌이지만, 테스트가 경계값을 전혀 건드리지 않아 "통과는 하지만 사실
    아무것도 검증 안 한" 상태다. 평범한 CI라면 초록불이니 그냥 병합되겠지만,
    Weld는 변경 영역에 결함을 주입해(뮤테이션) 그 초록불이 진짜로 로직을
    검증한 것인지를 다시 잰다 — 이 경계값 미달 테스트로는 하한/상한 로직에
    결함을 넣어도 안 잡히므로(뮤테이션 점수가 임계값 0.8 미달) 자동 병합을
    거부하고 사람에게 넘겨야 한다.
    """
    print("\n" + "=" * 60)
    print("시나리오 ③ 통과하지만 부실한 테스트 → 뮤테이션 테스팅이 잡아내 에스컬레이션")
    print("=" * 60)
    print("  base: is_valid_range(x) → 항상 True")
    print("  A(feature): x < 0 이면 False (하한 체크)를 같은 자리에 추가")
    print("  B(main):    x > 100 이면 False (상한 체크)를 같은 자리에 추가")
    print("  테스트: assert is_valid_range(50) == True  (경계값 근처를 전혀 안 건드림)")
    print("  → 테스트는 통과하지만, 하한/상한 로직에 결함을 주입해도 안 잡힘")
    print("    (뮤테이션 점수 낮음) → Weld가 '초록불을 못 믿겠다'며 에스컬레이션해야 함")

    _init_repo(root)
    (root / "conftest.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n"
    )
    (root / "range_check.py").write_text("def is_valid_range(x):\n    return True\n")
    (root / "test_range_check.py").write_text(
        "from range_check import is_valid_range\n\n\n"
        "def test_middle_value():\n"
        "    assert is_valid_range(50) == True\n"
    )
    _commit_all(root, "base + test")
    if not _install_weld(root):
        return
    _commit_all(root, "weld 설치")
    base_branch = _git(["branch", "--show-current"], root).stdout.strip()

    _git(["checkout", "-q", "-b", "feature"], root)
    (root / "range_check.py").write_text(
        "def is_valid_range(x):\n"
        "    if x < 0:\n"
        "        return False\n"
        "    return True\n"
    )
    _commit_all(root, "A: 하한 체크 추가")

    _git(["checkout", "-q", base_branch], root)
    (root / "range_check.py").write_text(
        "def is_valid_range(x):\n"
        "    if x > 100:\n"
        "        return False\n"
        "    return True\n"
    )
    _commit_all(root, "B: 상한 체크 추가")

    result = _git(["merge", "feature", "-m", "merge feature"], root)
    print(f"\n  git merge 종료코드: {result.returncode}")
    if _has_conflict_markers(root, "range_check.py"):
        print("  🟡 사람에게 폴백됨 — 뮤테이션 게이트가 부실한 테스트를 잡아낸 것이 정상 동작.")
        if result.stderr.strip():
            print("  ── weld 진단 (stderr, 뮤테이션 점수 포함) " + "─" * 20)
            for line in result.stderr.strip().splitlines():
                print(f"    {line}")
    elif result.returncode == 0:
        print("  ⚠️ 자동 병합됨 — 뮤테이션 게이트가 이 부실한 테스트를 못 잡았다면 점검 필요.")
    _print_file(root, "range_check.py")


def _setup_value_conflict_repo(root: Path, test_body: str) -> subprocess.CompletedProcess:
    """값 충돌(같은 줄, 다른 값) + 그 값을 검증하는 테스트가 있는 저장소를 만들고
    git merge까지 실행한다. base=3000, A(feature)=4000, B(main)=5000.

    값 충돌이라 generate가 LLM을 안 부르고 ours/theirs를 그대로 후보로 낸다
    (GEMINI_API_KEY 불필요). 어느 값이 맞는지는 test_body가 결정한다.
    """
    _init_repo(root)
    (root / "conftest.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n"
    )
    (root / "pricing.py").write_text("def shipping_fee():\n    return 3000\n")
    (root / "test_pricing.py").write_text(
        "from pricing import shipping_fee\n\n\ndef test_shipping_fee():\n" + test_body
    )
    _commit_all(root, "base + test")
    _install_weld(root)
    _commit_all(root, "weld 설치")
    base_branch = _git(["branch", "--show-current"], root).stdout.strip()

    _git(["checkout", "-q", "-b", "feature"], root)
    (root / "pricing.py").write_text("def shipping_fee():\n    return 4000\n")
    _commit_all(root, "A: 배송비 4000")

    _git(["checkout", "-q", base_branch], root)
    (root / "pricing.py").write_text("def shipping_fee():\n    return 5000\n")
    _commit_all(root, "B: 배송비 5000")

    return _git(["merge", "feature", "-m", "merge feature"], root)


def scenario_value_automerge(root: Path) -> None:
    """값 충돌 + 테스트가 정답 값을 못 박음 → 검증이 옳은 후보를 골라 자동 병합."""
    print("\n" + "=" * 60)
    print("시나리오 ④ 값 충돌 + 엄격한 테스트 → 자동 병합 (해피패스)")
    print("=" * 60)
    print("  base: shipping_fee() = 3000,  A=4000,  B=5000")
    print("  테스트: assert shipping_fee() == 5000  (정답을 못 박음)")
    print("  → 5000은 통과, 4000은 실패 → Weld가 5000을 자동 병합해야 함")

    result = _setup_value_conflict_repo(root, "    assert shipping_fee() == 5000\n")
    print(f"\n  git merge 종료코드: {result.returncode}")
    merged = (root / "pricing.py").read_text()
    if result.returncode == 0 and "5000" in merged and not _has_conflict_markers(root, "pricing.py"):
        print("  ✅ Weld가 검증 통과한 5000을 자동 병합했다 (진짜 충돌 해피패스!).")
    elif _has_conflict_markers(root, "pricing.py"):
        print("  🟡 사람에게 폴백됨.")
        if result.stderr.strip():
            print("  ── weld 진단 (stderr) " + "─" * 30)
            for line in result.stderr.strip().splitlines():
                print(f"    {line}")
    else:
        print("  ⚠️ 예상 밖 상태.")
    _print_file(root, "pricing.py")


def scenario_value_escalate(root: Path) -> None:
    """값 충돌 + 테스트가 값을 전혀 제약 안 함 → 판정 불가 → 정직하게 사람에게."""
    print("\n" + "=" * 60)
    print("시나리오 ⑤ 값 충돌 + 느슨한 테스트 → 에스컬레이션 (정직한 폴백)")
    print("=" * 60)
    print("  base: shipping_fee() = 3000,  A=4000,  B=5000")
    print("  테스트: assert isinstance(shipping_fee(), int)  (값을 제약 안 함)")
    print("  → 4000도 5000도 통과, 뮤테이션도 못 잡음 → 사람에게 넘겨야 함")

    result = _setup_value_conflict_repo(root, "    assert isinstance(shipping_fee(), int)\n")
    print(f"\n  git merge 종료코드: {result.returncode}")
    if _has_conflict_markers(root, "pricing.py"):
        print("  🟡 사람에게 폴백 — 표준 충돌 마커를 남김.")
        print("     (테스트가 값을 못 박으니 자동 판정 불가 → 이게 올바른 동작)")
        if result.stderr.strip():
            print("  ── weld 진단 (stderr) " + "─" * 30)
            for line in result.stderr.strip().splitlines():
                print(f"    {line}")
    elif result.returncode == 0:
        print("  ⚠️ 자동 병합됨 — 느슨한 테스트인데 병합했다면 판정 로직 점검 필요.")
    _print_file(root, "pricing.py")


def main() -> int:
    if shutil.which("weld") is None:
        print("`weld` 명령을 PATH에서 못 찾았습니다. 먼저 `pip install -e .`를 실행하세요.")
        return 1

    print("Weld end-to-end 데모 — 진짜 git 저장소에서 실제 merge 실행")
    scenarios = (
        scenario_spurious,
        scenario_llm_synthesis,
        scenario_mutation_catch,
        scenario_value_automerge,
        scenario_value_escalate,
    )
    for scenario in scenarios:
        with tempfile.TemporaryDirectory(prefix="weld-demo-") as tmp:
            scenario(Path(tmp))
    print("\n데모 끝.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
