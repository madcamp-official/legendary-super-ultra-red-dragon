"""길고 복잡한 파일에서 LLM 구조합성 + 단계별 타이밍 측정.

~120줄 주문처리 모듈의 apply_discounts 함수에 두 브랜치가 서로 다른 할인
로직을 넣어 구조충돌을 만들고, weld 파이프라인을 단계별로 계측한다.
  classify → generate(LLM) → verify(sandbox) → mutation → decide
각 단계 벽시계 시간을 출력해 "30초가 어디서 나오는지" 보여준다.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

WELD = "/Users/kimminjae/Documents/몰입캠프/3주차/weld"
sys.path.insert(0, f"{WELD}/src")

D = textwrap.dedent

# ---- 공통 조상: 길고 여러 함수가 있는 현실적 모듈 (~120줄) ----
BASE = D('''\
    """주문 가격 계산 모듈."""

    TAX_RATE = 0.10
    FREE_SHIP_THRESHOLD = 50.0
    SHIP_FEE = 5.0
    CURRENCY = "KRW"


    class OrderError(Exception):
        """주문 검증 실패."""


    def _validate_item(item):
        if "price" not in item or "qty" not in item:
            raise OrderError("item에 price/qty가 필요하다")
        if item["price"] < 0:
            raise OrderError("price는 음수일 수 없다")
        if item["qty"] <= 0:
            raise OrderError("qty는 1 이상이어야 한다")
        return True


    def _line_total(item):
        _validate_item(item)
        return item["price"] * item["qty"]


    def subtotal(items):
        if not items:
            raise OrderError("빈 주문은 계산할 수 없다")
        return sum(_line_total(i) for i in items)


    def shipping_fee(amount):
        if amount >= FREE_SHIP_THRESHOLD:
            return 0.0
        return SHIP_FEE


    def apply_discounts(amount, customer):
        """할인 적용."""
        return amount


    def compute_tax(amount):
        return round(amount * TAX_RATE, 2)


    def calculate_total(items, customer):
        base_amount = subtotal(items)
        discounted = apply_discounts(base_amount, customer)
        ship = shipping_fee(discounted)
        tax = compute_tax(discounted)
        return round(discounted + ship + tax, 2)


    def format_receipt(items, customer):
        total = calculate_total(items, customer)
        lines = [f"주문 {len(items)}건", f"합계: {total} {CURRENCY}"]
        return "\\n".join(lines)
    ''')

# ours/theirs: docstring은 그대로 두고 return 앞에 서로 다른 if블록만 삽입
# (username 워크스루와 같은 구조 → 진짜 LLM 합성 경로).
_ANCHOR = (
    '    """할인 적용."""\n'
    '    return amount\n'
)
OURS = BASE.replace(_ANCHOR,
    '    """할인 적용."""\n'
    '    if amount >= 100.0:\n'
    '        amount = round(amount * 0.9, 2)\n'
    '    return amount\n',
)
THEIRS = BASE.replace(_ANCHOR,
    '    """할인 적용."""\n'
    '    if customer.get("years", 0) >= 3:\n'
    '        amount = round(amount * 0.95, 2)\n'
    '    return amount\n',
)
assert OURS != BASE and THEIRS != BASE and OURS != THEIRS, "replace 실패"

FILE = "src/orders.py"

# ours가 추가하는 테스트 (대량구매 할인 검증)
TEST_OURS = D('''\
    from src.orders import apply_discounts, calculate_total


    def test_bulk_applied():
        assert apply_discounts(200.0, {}) == 180.0

    def test_bulk_boundary_at():
        assert apply_discounts(100.0, {}) == 90.0

    def test_bulk_boundary_below():
        assert apply_discounts(99.0, {}) == 99.0
    ''')

# theirs가 추가하는 테스트 (멤버십 할인 검증)
# amount는 80(대량 임계값 100 미만)으로 골라 대량할인과 겹치지 않게 격리한다.
TEST_THEIRS = D('''\
    from src.orders import apply_discounts


    def test_member_applied():
        assert apply_discounts(80.0, {"years": 5}) == 76.0

    def test_member_boundary_at():
        assert apply_discounts(80.0, {"years": 3}) == 76.0

    def test_member_boundary_below():
        assert apply_discounts(80.0, {"years": 2}) == 80.0
    ''')


def main() -> None:
    from weld.candidates.generate import generate_candidates
    from weld.classify.mergiraf import classify_conflict
    from weld.policy.trust import decide_among
    from weld.verify.impact import select_relevant_tests
    from weld.verify.mutation import compute_mutation_scores_parallel
    from weld.verify.sandbox import run_candidates_parallel

    repo = Path(tempfile.mkdtemp(prefix="weld-timed-"))
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "tests" / "__init__.py").write_text("")
    # main = ours 상태(초록) + 양쪽 테스트 (병합 시 둘 다 존재하도록 미리 둠)
    (repo / FILE).write_text(OURS)
    (repo / "tests" / "test_bulk.py").write_text(TEST_OURS)
    (repo / "tests" / "test_member.py").write_text(TEST_THEIRS)
    os.chdir(repo)
    # sandbox 검증이 `git worktree add`로 격리하므로 진짜 git 저장소여야 한다.
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"], ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "base"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)

    print(f"파일 길이: base {len(BASE.splitlines())}줄, "
          f"충돌 함수 apply_discounts에서 ours(대량할인) vs theirs(멤버십할인)")
    print("=" * 60)

    timings = {}
    t0 = time.time()

    t = time.time()
    changed = {FILE: set(range(1, len(OURS.splitlines()) + 1))}
    relevant = select_relevant_tests([FILE], repo_path=".", changed_lines=changed)
    timings["1. 테스트 선별(impact)"] = time.time() - t

    t = time.time()
    cls = classify_conflict(BASE, OURS, THEIRS, file_path=FILE)
    timings["2. 분류(mergiraf)"] = time.time() - t

    t = time.time()
    candidates = [
        dataclasses.replace(c, file_path=FILE)
        for c in generate_candidates(BASE, OURS, THEIRS, file_path=FILE)
    ]
    timings["3. 후보생성(LLM)"] = time.time() - t

    t = time.time()
    verifs = run_candidates_parallel(candidates, repo_path=".", tests=relevant)
    timings["4. 검증(sandbox/test)"] = time.time() - t

    t = time.time()
    muts = compute_mutation_scores_parallel(candidates, relevant, repo_path=".",
                                            base_content=BASE)
    timings["5. 뮤테이션"] = time.time() - t

    t = time.time()
    decision = decide_among(candidates, verifs, muts)
    timings["6. 판정(policy)"] = time.time() - t

    total = time.time() - t0

    print("단계별 타이밍:")
    for k, v in timings.items():
        bar = "█" * max(1, int(v / total * 40))
        print(f"  {k:24s} {v:6.2f}s  {bar}")
    print(f"  {'─' * 24} {'─' * 6}")
    print(f"  {'합계':24s} {total:6.2f}s")
    print("=" * 60)

    print(f"\n분류: 구조충돌={not cls.is_spurious}")
    for c, v, m in zip(candidates, verifs, muts):
        print(f"후보 {c.id} [{c.strategy}]: 컴파일={v.compiled} 테스트={v.tests_passed}"
              f" | 뮤테이션 {m.mutants_killed}/{m.mutants_total} (점수 {m.score:.2f})")
    print(f"\n판정: {'✅ 자동병합' if decision.accepted else '🔶 에스컬레이션'} — {decision.reason}")
    if decision.accepted:
        content = next(c.content for c in candidates if c.id == decision.candidate_id)
        # 합성된 apply_discounts 부분만 발췌
        print("\n--- LLM이 합성한 apply_discounts ---")
        inside = False
        for ln in content.splitlines():
            if ln.strip().startswith("def apply_discounts"):
                inside = True
            elif inside and ln.strip().startswith("def "):
                break
            if inside:
                print("  " + ln)

    os.chdir("/")
    shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    main()
