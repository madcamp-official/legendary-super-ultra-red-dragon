"""Weld 다국어 "손으로 체험하는" 데모.

run_demo.py(임시 디렉터리, 스크립트가 알아서 merge까지 실행)와 달리, 이 스크립트는
`demo/playground/`에 **영구** git 저장소를 만들어두기만 하고 실제 `git merge`는
사용자가 직접 친다 — 진짜 팀원 둘(나/팀원)이 같은 파일을 건드리고 병합하는
경험을 그대로 재현하기 위해서다.

지원 5개 언어(python, javascript, typescript, c, cpp — langs.py에서 test_command가
실제로 채워진, 즉 실행 검증까지 되는 언어) 각각에 파일 2개씩:
  - 가짜 충돌 파일: 팀원과 내가 같은 파일의 서로 다른 함수를 고침
    → mergiraf가 구조적으로 자동 병합 (LLM 불필요)
  - 진짜 충돌 파일: 팀원과 내가 같은 코드 블록을 서로 다른 의도로 고침
    → mergiraf가 못 풀어 candidates/generate.py가 LLM으로 후보를 합성하고
      verify+mutation 게이트를 통과해야 자동 병합됨

사용법:
    python demo/playground_setup.py          # demo/playground/ 새로 생성(있으면 초기화)
    cd demo/playground
    git log --oneline --all --graph          # base → teammate/me 분기 확인
    git diff main teammate -- src/           # 팀원이 뭘 고쳤는지 확인
    git merge teammate                       # ← 진짜 충돌 상황, Weld가 처리

재실행하면 demo/playground/를 통째로 지우고 처음(병합 전) 상태로 되돌린다 —
merge 결과를 구경한 뒤 다시 체험하고 싶으면 그냥 다시 이 스크립트를 돌리면 된다.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEMO_DIR.parent
PLAYGROUND = DEMO_DIR / "playground"
SHIM_BIN = DEMO_DIR / ".bin"
SHIM_SRC = DEMO_DIR / ".shim-src" / "make_shim.c"


# --------------------------------------------------------------------------
# 파일 하나당 (base, teammate 버전, main 버전) 3종 콘텐츠 + 안 변하는 테스트.
# teammate 브랜치엔 teammate 버전을, 이 스크립트를 돌리는 "나"의 브랜치(main)엔
# main 버전을 커밋한다 — 테스트는 base 커밋에 한 번만 들어가고 양쪽 다 안 건드린다.
# --------------------------------------------------------------------------


class Fixture:
    def __init__(self, src_path: str, base: str, teammate: str, main: str, spurious: bool):
        self.src_path = src_path
        self.base = base
        self.teammate = teammate
        self.main = main
        self.spurious = spurious  # 설명용 — 실제 분류는 mergiraf가 실행 시점에 결정


# ---------------------------------------------------------------- python ---

_PY_DISCOUNT_BASE = textwrap.dedent("""\
    def apply_discount(price, percent):
        return price - (price * percent / 100)


    def format_price(amount):
        return f"{amount}원"
    """)

_PY_DISCOUNT_TEAMMATE = textwrap.dedent("""\
    def apply_discount(price, percent):
        discounted = price - (price * percent / 100)
        return round(discounted)


    def format_price(amount):
        return f"{amount}원"
    """)

_PY_DISCOUNT_MAIN = textwrap.dedent("""\
    def apply_discount(price, percent):
        return price - (price * percent / 100)


    def format_price(amount):
        return f"{amount:,}원"
    """)

_PY_DISCOUNT_TEST = textwrap.dedent("""\
    from discount import apply_discount, format_price


    def test_apply_discount_rounds_to_int():
        assert apply_discount(1000, 10) == 900


    def test_format_price_has_thousands_comma():
        assert format_price(12000) == "12,000원"
    """)

_PY_INVENTORY_BASE = textwrap.dedent("""\
    def restock(current, incoming):
        if incoming < 0:
            return current
        return current + incoming
    """)

_PY_INVENTORY_TEAMMATE = textwrap.dedent("""\
    def restock(current, incoming):
        if incoming < 0:
            raise ValueError("incoming must be non-negative")
        return current + incoming
    """)

_PY_INVENTORY_MAIN = textwrap.dedent("""\
    def restock(current, incoming):
        if current < 0:
            raise ValueError("current must be non-negative")
        if incoming < 0:
            incoming = 0
        return current + incoming
    """)

_PY_INVENTORY_TEST = textwrap.dedent("""\
    import pytest

    from inventory import restock


    def test_restock_normal():
        assert restock(10, 5) == 15


    def test_restock_rejects_negative_incoming():
        with pytest.raises(ValueError):
            restock(10, -5)


    def test_restock_rejects_negative_current():
        with pytest.raises(ValueError):
            restock(-10, 5)
    """)

_PY_CONFTEST = textwrap.dedent("""\
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src", "python"))
    """)


# -------------------------------------------------------------- javascript -

_JS_CART_BASE = textwrap.dedent("""\
    function addItem(cart, item) {
      cart.push(item);
      return cart;
    }

    function totalPrice(cart) {
      return cart.reduce((sum, item) => sum + item.price, 0);
    }

    module.exports = { addItem, totalPrice };
    """)

_JS_CART_TEAMMATE = textwrap.dedent("""\
    function addItem(cart, item) {
      if (cart.some((i) => i.id === item.id)) {
        return cart;
      }
      cart.push(item);
      return cart;
    }

    function totalPrice(cart) {
      return cart.reduce((sum, item) => sum + item.price, 0);
    }

    module.exports = { addItem, totalPrice };
    """)

_JS_CART_MAIN = textwrap.dedent("""\
    function addItem(cart, item) {
      cart.push(item);
      return cart;
    }

    function totalPrice(cart) {
      const sum = cart.reduce((s, item) => s + item.price, 0);
      return Math.round(sum * 100) / 100;
    }

    module.exports = { addItem, totalPrice };
    """)

_JS_CART_TEST = textwrap.dedent("""\
    const test = require('node:test');
    const assert = require('node:assert');
    const { addItem, totalPrice } = require('../../src/javascript/cart.js');

    test('addItem prevents duplicate ids', () => {
      const cart = addItem(addItem([], { id: 1, price: 10 }), { id: 1, price: 10 });
      assert.strictEqual(cart.length, 1);
    });

    test('totalPrice rounds to 2 decimals', () => {
      const cart = [{ id: 1, price: 0.1 }, { id: 2, price: 0.2 }];
      assert.strictEqual(totalPrice(cart), 0.3);
    });
    """)

_JS_COUPON_BASE = textwrap.dedent("""\
    function applyCoupon(price, code) {
      if (code === "SAVE10") {
        return price * 0.9;
      }
      return price;
    }

    module.exports = { applyCoupon };
    """)

_JS_COUPON_TEAMMATE = textwrap.dedent("""\
    function applyCoupon(price, code) {
      const normalized = code.toUpperCase();
      if (normalized === "SAVE10") {
        return price * 0.9;
      }
      return price;
    }

    module.exports = { applyCoupon };
    """)

_JS_COUPON_MAIN = textwrap.dedent("""\
    function applyCoupon(price, code) {
      if (code === "SAVE10") {
        const discounted = price * 0.9;
        return discounted < 0 ? 0 : discounted;
      }
      return price;
    }

    module.exports = { applyCoupon };
    """)

_JS_COUPON_TEST = textwrap.dedent("""\
    const test = require('node:test');
    const assert = require('node:assert');
    const { applyCoupon } = require('../../src/javascript/coupon.js');

    test('coupon code is case-insensitive', () => {
      assert.strictEqual(applyCoupon(100, 'save10'), 90);
    });

    test('discount never goes below zero', () => {
      assert.strictEqual(applyCoupon(-100, 'SAVE10'), 0);
    });

    test('unknown code returns original price', () => {
      assert.strictEqual(applyCoupon(100, 'NONE'), 100);
    });
    """)


# -------------------------------------------------------------- typescript -

_TS_MEMBERSHIP_BASE = textwrap.dedent("""\
    export function tierOf(points: number): string {
      if (points >= 1000) return "gold";
      return "basic";
    }

    export function pointsToNextTier(points: number): number {
      return Math.max(0, 1000 - points);
    }
    """)

_TS_MEMBERSHIP_TEAMMATE = textwrap.dedent("""\
    export function tierOf(points: number): string {
      if (points >= 1000) return "gold";
      if (points >= 500) return "silver";
      return "basic";
    }

    export function pointsToNextTier(points: number): number {
      return Math.max(0, 1000 - points);
    }
    """)

_TS_MEMBERSHIP_MAIN = textwrap.dedent("""\
    export function tierOf(points: number): string {
      if (points >= 1000) return "gold";
      return "basic";
    }

    export function pointsToNextTier(points: number): number {
      return Math.max(0, Math.ceil((1000 - points) / 10) * 10);
    }
    """)

_TS_MEMBERSHIP_TEST = textwrap.dedent("""\
    import test from 'node:test';
    import assert from 'node:assert';
    import { tierOf, pointsToNextTier } from '../../src/typescript/membership.mts';

    test('silver tier from 500 points', () => {
      assert.strictEqual(tierOf(600), 'silver');
    });

    test('gold tier from 1000 points', () => {
      assert.strictEqual(tierOf(1200), 'gold');
    });

    test('pointsToNextTier rounds up to nearest 10', () => {
      assert.strictEqual(pointsToNextTier(985), 20);
    });
    """)

_TS_REFUND_BASE = textwrap.dedent("""\
    export function refundAmount(price: number, daysSincePurchase: number): number {
      if (daysSincePurchase <= 7) {
        return price;
      }
      return 0;
    }
    """)

_TS_REFUND_TEAMMATE = textwrap.dedent("""\
    export function refundAmount(price: number, daysSincePurchase: number): number {
      if (daysSincePurchase <= 7) {
        return price;
      } else if (daysSincePurchase <= 14) {
        return price * 0.5;
      }
      return 0;
    }
    """)

_TS_REFUND_MAIN = textwrap.dedent("""\
    export function refundAmount(price: number, daysSincePurchase: number): number {
      if (daysSincePurchase < 0) {
        throw new Error("daysSincePurchase must be non-negative");
      }
      if (daysSincePurchase <= 7) {
        return price;
      }
      return 0;
    }
    """)

_TS_REFUND_TEST = textwrap.dedent("""\
    import test from 'node:test';
    import assert from 'node:assert';
    import { refundAmount } from '../../src/typescript/refund.mts';

    test('full refund within 7 days', () => {
      assert.strictEqual(refundAmount(100, 3), 100);
    });

    test('half refund between 8 and 14 days', () => {
      assert.strictEqual(refundAmount(100, 10), 50);
    });

    test('no refund after 14 days', () => {
      assert.strictEqual(refundAmount(100, 20), 0);
    });

    test('throws on negative days', () => {
      assert.throws(() => refundAmount(100, -1));
    });
    """)


# ------------------------------------------------------------------- c -----

_C_STATS_BASE = textwrap.dedent("""\
    int sum_array(const int *xs, int n) {
        int total = 0;
        for (int i = 0; i < n; i++) {
            total += xs[i];
        }
        return total;
    }

    double average(const int *xs, int n) {
        if (n == 0) {
            return 0.0;
        }
        return (double) sum_array(xs, n) / n;
    }
    """)

_C_STATS_TEAMMATE = textwrap.dedent("""\
    int sum_array(const int *xs, int n) {
        if (n <= 0) {
            return 0;
        }
        int total = 0;
        for (int i = 0; i < n; i++) {
            total += xs[i];
        }
        return total;
    }

    double average(const int *xs, int n) {
        if (n == 0) {
            return 0.0;
        }
        return (double) sum_array(xs, n) / n;
    }
    """)

_C_STATS_MAIN = textwrap.dedent("""\
    int sum_array(const int *xs, int n) {
        int total = 0;
        for (int i = 0; i < n; i++) {
            total += xs[i];
        }
        return total;
    }

    double average(const int *xs, int n) {
        if (n <= 0) {
            return 0.0;
        }
        return (double) sum_array(xs, n) / n;
    }
    """)

_C_STATS_TEST = textwrap.dedent("""\
    #include <assert.h>

    int sum_array(const int *xs, int n);
    double average(const int *xs, int n);

    int main(void) {
        int a[] = {1, 2, 3, 4};
        assert(sum_array(a, 4) == 10);
        assert(sum_array(a, 0) == 0);
        assert(sum_array(a, -1) == 0);
        assert(average(a, 4) == 2.5);
        assert(average(a, -1) == 0.0);
        return 0;
    }
    """)

_C_TEMPERATURE_BASE = textwrap.dedent("""\
    double to_fahrenheit(double celsius) {
        return celsius * 9.0 / 5.0 + 32.0;
    }
    """)

_C_TEMPERATURE_TEAMMATE = textwrap.dedent("""\
    double to_fahrenheit(double celsius) {
        double c = celsius < -273.15 ? -273.15 : celsius;
        return c * 9.0 / 5.0 + 32.0;
    }
    """)

_C_TEMPERATURE_MAIN = textwrap.dedent("""\
    double to_fahrenheit(double celsius) {
        double f = celsius * 9.0 / 5.0 + 32.0;
        return ((int)(f * 10 + (f >= 0 ? 0.5 : -0.5))) / 10.0;
    }
    """)

_C_TEMPERATURE_TEST = textwrap.dedent("""\
    #include <assert.h>
    #include <math.h>

    double to_fahrenheit(double celsius);

    int main(void) {
        assert(fabs(to_fahrenheit(0) - 32.0) < 0.5);

        /* 절대영도 클램프: 안 하면 -508.0, 하면 -459.67 근처 — 48도 넘게
         * 차이나서 클램프 유무를 확실히 구분한다(반올림 유무와는 무관). */
        assert(fabs(to_fahrenheit(-300) - (-459.67)) < 0.5);

        /* 소수 첫째자리 반올림: 반올림을 안 하면 소수 둘째자리 이하가 남는다 —
         * 36.6도는 9/5 변환 시 딱 떨어지지 않는 값이라 반올림 유무가 갈린다. */
        double f = to_fahrenheit(36.6);
        double rounded = ((int)(f * 10 + (f >= 0 ? 0.5 : -0.5))) / 10.0;
        assert(fabs(f - rounded) < 1e-6);

        return 0;
    }
    """)


# ----------------------------------------------------------------- cpp -----

_CPP_GRADE_BASE = textwrap.dedent("""\
    char letter_grade(int score) {
        if (score >= 90) {
            return 'A';
        }
        return 'F';
    }

    bool is_passing(int score) {
        return score >= 60;
    }
    """)

_CPP_GRADE_TEAMMATE = textwrap.dedent("""\
    char letter_grade(int score) {
        if (score >= 90) {
            return 'A';
        }
        if (score >= 80) {
            return 'B';
        }
        return 'F';
    }

    bool is_passing(int score) {
        return score >= 60;
    }
    """)

_CPP_GRADE_MAIN = textwrap.dedent("""\
    char letter_grade(int score) {
        if (score >= 90) {
            return 'A';
        }
        return 'F';
    }

    bool is_passing(int score) {
        if (score < 0) {
            return false;
        }
        return score >= 60;
    }
    """)

_CPP_GRADE_TEST = textwrap.dedent("""\
    #include <cassert>

    char letter_grade(int score);
    bool is_passing(int score);

    int main() {
        assert(letter_grade(95) == 'A');
        assert(letter_grade(85) == 'B');
        assert(letter_grade(50) == 'F');
        assert(is_passing(60) == true);
        assert(is_passing(-5) == false);
        return 0;
    }
    """)

_CPP_BONUS_BASE = textwrap.dedent("""\
    int bonus_points(int amount) {
        if (amount < 0) {
            return 0;
        }
        return amount / 100;
    }
    """)

_CPP_BONUS_TEAMMATE = textwrap.dedent("""\
    int bonus_points(int amount) {
        if (amount < 0) {
            return 0;
        }
        int points = amount / 100;
        return points > 500 ? 500 : points;
    }
    """)

_CPP_BONUS_MAIN = textwrap.dedent("""\
    int bonus_points(int amount) {
        if (amount < 0) {
            return 0;
        }
        if (amount >= 10000) {
            return (amount / 100) * 3 / 2;
        }
        return amount / 100;
    }
    """)

_CPP_BONUS_TEST = textwrap.dedent("""\
    #include <cassert>

    int bonus_points(int amount);

    int main() {
        assert(bonus_points(-50) == 0);
        assert(bonus_points(250) == 2);
        assert(bonus_points(60000) == 500);
        assert(bonus_points(20000) == 300);
        return 0;
    }
    """)


FIXTURES: list[Fixture] = [
    Fixture("src/python/discount.py", _PY_DISCOUNT_BASE, _PY_DISCOUNT_TEAMMATE, _PY_DISCOUNT_MAIN, True),
    Fixture("src/python/inventory.py", _PY_INVENTORY_BASE, _PY_INVENTORY_TEAMMATE, _PY_INVENTORY_MAIN, False),
    Fixture("src/javascript/cart.js", _JS_CART_BASE, _JS_CART_TEAMMATE, _JS_CART_MAIN, True),
    Fixture("src/javascript/coupon.js", _JS_COUPON_BASE, _JS_COUPON_TEAMMATE, _JS_COUPON_MAIN, False),
    Fixture("src/typescript/membership.mts", _TS_MEMBERSHIP_BASE, _TS_MEMBERSHIP_TEAMMATE, _TS_MEMBERSHIP_MAIN, True),
    Fixture("src/typescript/refund.mts", _TS_REFUND_BASE, _TS_REFUND_TEAMMATE, _TS_REFUND_MAIN, False),
    Fixture("src/c/stats.c", _C_STATS_BASE, _C_STATS_TEAMMATE, _C_STATS_MAIN, True),
    Fixture("src/c/temperature.c", _C_TEMPERATURE_BASE, _C_TEMPERATURE_TEAMMATE, _C_TEMPERATURE_MAIN, False),
    Fixture("src/cpp/grade.cpp", _CPP_GRADE_BASE, _CPP_GRADE_TEAMMATE, _CPP_GRADE_MAIN, True),
    Fixture("src/cpp/bonus.cpp", _CPP_BONUS_BASE, _CPP_BONUS_TEAMMATE, _CPP_BONUS_MAIN, False),
]

TEST_FILES: dict[str, str] = {
    "tests/python/test_discount.py": _PY_DISCOUNT_TEST,
    "tests/python/test_inventory.py": _PY_INVENTORY_TEST,
    "tests/javascript/cart.test.js": _JS_CART_TEST,
    "tests/javascript/coupon.test.js": _JS_COUPON_TEST,
    "tests/typescript/membership.test.mts": _TS_MEMBERSHIP_TEST,
    "tests/typescript/refund.test.mts": _TS_REFUND_TEST,
    "tests/c/test_stats.c": _C_STATS_TEST,
    "tests/c/test_temperature.c": _C_TEMPERATURE_TEST,
    "tests/cpp/test_grade.cpp": _CPP_GRADE_TEST,
    "tests/cpp/test_bonus.cpp": _CPP_BONUS_TEST,
}

MAKEFILE = textwrap.dedent("""\
    # demo/playground용 C/C++ 빌드+테스트. langs.py 규약(build_command=
    # "make -s -B", test_command="make -s test")을 그대로 따른다.
    # CC/CXX는 ?=가 아니라 :=로 강제한다 — GNU Make는 CC/CXX를 내장 변수로 이미
    # "cc"/"g++" 아닌 "cc"로 미리 정의해두므로 `?=`가 안 먹는다(실측 확인).
    CC := gcc
    CXX := g++
    CFLAGS := -std=c11 -Wall
    CXXFLAGS := -std=c++17 -Wall

    BIN := bin

    .PHONY: all test clean
    all: $(BIN)/test_stats $(BIN)/test_temperature $(BIN)/test_grade $(BIN)/test_bonus

    $(BIN):
    \tmkdir -p $(BIN)

    $(BIN)/test_stats: src/c/stats.c tests/c/test_stats.c | $(BIN)
    \t$(CC) $(CFLAGS) -o $@ tests/c/test_stats.c src/c/stats.c

    $(BIN)/test_temperature: src/c/temperature.c tests/c/test_temperature.c | $(BIN)
    \t$(CC) $(CFLAGS) -o $@ tests/c/test_temperature.c src/c/temperature.c -lm

    $(BIN)/test_grade: src/cpp/grade.cpp tests/cpp/test_grade.cpp | $(BIN)
    \t$(CXX) $(CXXFLAGS) -o $@ tests/cpp/test_grade.cpp src/cpp/grade.cpp

    $(BIN)/test_bonus: src/cpp/bonus.cpp tests/cpp/test_bonus.cpp | $(BIN)
    \t$(CXX) $(CXXFLAGS) -o $@ tests/cpp/test_bonus.cpp src/cpp/bonus.cpp

    # TESTS=<테스트 파일...>이 오면(verify/impact.py 선별 결과 — langs.py의
    # effective_test_command가 "make -s test TESTS=tests/c/test_x.c" 형태로
    # 붙여준다) 그 파일에 대응하는 바이너리만 돌린다. 이게 중요한 이유: 이
    # 플레이그라운드엔 언어당 충돌 파일이 2개씩 있어서, 한쪽(예: temperature.c)
    # 후보를 검증하는 동안 항상 전체 스위트를 돌면, 아직 안 풀린 다른 쪽
    # (bonus.cpp의 main 브랜치 상태)이 자기 테스트에 실패해 엉뚱하게
    # temperature.c 후보까지 "테스트 실패"로 오염시킨다 — TESTS를 무시하는
    # 폴백은 충돌 파일이 하나뿐인 저장소에서나 안전하다. TESTS가 없으면
    # (선별 불가 등) 안전하게 전체 스위트로 폴백한다.
    test: all
    ifdef TESTS
    \t@for f in $(TESTS); do \
    \t\tstem=$$(basename "$$f"); \
    \t\tstem=$${stem%.c}; stem=$${stem%.cpp}; stem=$${stem%.cc}; stem=$${stem%.cxx}; \
    \t\techo "-- $(BIN)/$$stem (targeted) --"; \
    \t\t"$(BIN)/$$stem" || exit 1; \
    \t done
    \t@echo "targeted C/C++ tests passed"
    else
    \t@echo "-- test_stats --"
    \t@$(BIN)/test_stats
    \t@echo "-- test_temperature --"
    \t@$(BIN)/test_temperature
    \t@echo "-- test_grade --"
    \t@$(BIN)/test_grade
    \t@echo "-- test_bonus --"
    \t@$(BIN)/test_bonus
    \t@echo "all C/C++ tests passed"
    endif

    clean:
    \trm -rf $(BIN)
    """)

PLAYGROUND_GITIGNORE = textwrap.dedent("""\
    bin/
    .weld_cache/
    """)

PLAYGROUND_README = textwrap.dedent("""\
    # Weld 다국어 플레이그라운드

    `python demo/playground_setup.py`로 생성된 영구 저장소. 5개 언어(python,
    javascript, typescript, c, cpp) 각각 파일 2개 — 하나는 팀원과 내가 서로
    다른 함수를 고친 가짜 충돌, 하나는 같은 코드 블록을 다른 의도로 고친
    진짜 충돌이다. `git log --oneline --all --graph`로 base → teammate/main
    분기를 확인한 뒤, 그냥 실제로 병합해보면 된다:

    ```
    git merge teammate
    ```

    Weld가 설치돼 있어서(`.gitattributes`의 `* merge=weld`) git이 각 파일마다
    `weld merge`를 부른다 — 가짜 충돌 파일은 mergiraf가 조용히 구조적으로
    합치고, 진짜 충돌 파일은 candidates/generate.py가 LLM으로 후보를 만들어
    검증+뮤테이션 게이트를 통과해야 자동 병합된다. 게이트를 통과 못 하면
    표준 충돌 마커(`<<<<<<<` 등)를 남기고 사람에게 넘어간다 — 그것도 정상
    동작이다.

    다시 처음(병합 전) 상태로 되돌리려면 `python demo/playground_setup.py`를
    다시 실행한다 — 이 디렉터리를 통째로 지우고 새로 만든다.
    """)


def _onerror_force_remove(func, path, exc_info):
    """Windows에서 .git 내부의 읽기전용 파일 때문에 rmtree가 실패하는 걸 막는다."""
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def _rmtree_if_exists(path: Path) -> None:
    """직전에 죽은 make/git 서브프로세스가 파일 핸들을 아직 놓지 않은 경우를
    대비해 잠깐 텀을 두고 몇 번 재시도한다 — Windows는 프로세스 종료와 핸들
    해제 사이에 지연이 있을 수 있다."""
    import time

    if not path.exists():
        return
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            shutil.rmtree(path, onerror=_onerror_force_remove)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(1)
    raise last_error


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ 실패: {' '.join(cmd)}")
        print(f"    stdout: {result.stdout.strip()[-2000:]}")
        print(f"    stderr: {result.stderr.strip()[-2000:]}")
    return result


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return _run(["git", *args], cwd)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _ensure_make_shim() -> Path | None:
    """`make`가 이미 PATH에 있으면 아무것도 안 한다(mac/리눅스 정상 케이스).

    없으면(이 Windows 환경처럼 mingw32-make만 있는 경우) demo/.bin/make.exe
    셔틀을 gcc로 컴파일해둔다 — mingw32-make로 그대로 위임한다. 시스템 PATH나
    mingw64 설치는 건드리지 않고, 이 스크립트가 git 하위 프로세스에 넘기는
    PATH에만 demo/.bin을 얹는다(_env_with_shim 참고).
    """
    if shutil.which("make") is not None:
        return None

    SHIM_BIN.mkdir(parents=True, exist_ok=True)
    shim_exe = SHIM_BIN / "make.exe"
    if shim_exe.exists():
        return SHIM_BIN

    if shutil.which("gcc") is None or shutil.which("mingw32-make") is None:
        print("  ⚠️ make도 gcc+mingw32-make 조합도 없어서 C/C++ 데모는 빌드 게이트에서 항상 실패합니다.")
        return None

    result = subprocess.run(
        ["gcc", "-O2", "-o", str(shim_exe), str(SHIM_SRC)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ✗ make 셔틀 컴파일 실패: {result.stderr.strip()}")
        return None
    print(f"  make 셔틀 준비 완료: {shim_exe} (mingw32-make로 위임)")
    return SHIM_BIN


def _env_with_shim(shim_dir: Path | None) -> dict[str, str] | None:
    import os

    if shim_dir is None:
        return None
    env = os.environ.copy()
    env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
    return env


def build_playground() -> dict[str, str] | None:
    print(f"플레이그라운드 위치: {PLAYGROUND}")
    _rmtree_if_exists(PLAYGROUND)
    PLAYGROUND.mkdir(parents=True)

    shim_dir = _ensure_make_shim()
    env = _env_with_shim(shim_dir)

    # base 버전 + 테스트 + 부속 파일을 써넣는다.
    for fx in FIXTURES:
        _write(PLAYGROUND / fx.src_path, fx.base)
    for rel, content in TEST_FILES.items():
        _write(PLAYGROUND / rel, content)
    _write(PLAYGROUND / "Makefile", MAKEFILE)
    _write(PLAYGROUND / "conftest.py", _PY_CONFTEST)
    _write(PLAYGROUND / ".gitignore", PLAYGROUND_GITIGNORE)
    _write(PLAYGROUND / "README.md", PLAYGROUND_README)

    print("git 저장소 초기화 중...")
    _git(["init", "-q", "-b", "main"], PLAYGROUND)
    _git(["config", "user.email", "demo@weld.local"], PLAYGROUND)
    _git(["config", "user.name", "Weld Demo"], PLAYGROUND)
    _git(["add", "-A"], PLAYGROUND)
    _git(["commit", "-q", "-m", "base: 5개 언어 초기 버전 + 테스트"], PLAYGROUND)

    print("weld merge driver 설치 중...")
    install = _run(["weld", "install"], PLAYGROUND, env=env)
    if install.returncode != 0:
        print("  ✗ `weld install` 실패 — `pip install -e \".[dev]\"`로 weld를 먼저 설치했는지 확인하세요.")
        return None
    _git(["add", "-A"], PLAYGROUND)
    _git(["commit", "-q", "-m", "weld 설치"], PLAYGROUND)

    print("팀원(teammate) 브랜치 생성 중...")
    _git(["checkout", "-q", "-b", "teammate"], PLAYGROUND)
    for fx in FIXTURES:
        _write(PLAYGROUND / fx.src_path, fx.teammate)
    _git(["add", "-A"], PLAYGROUND)
    _git(["commit", "-q", "-m", "teammate: 5개 언어 기능 각각 수정"], PLAYGROUND)

    print("main 브랜치로 돌아가서 내 변경 커밋 중...")
    _git(["checkout", "-q", "main"], PLAYGROUND)
    for fx in FIXTURES:
        _write(PLAYGROUND / fx.src_path, fx.main)
    _git(["add", "-A"], PLAYGROUND)
    _git(["commit", "-q", "-m", "me: 같은 5개 언어 파일을 다른 방향으로 수정"], PLAYGROUND)

    print("\n준비 완료. 병합 전 상태로 main 브랜치에 있습니다.")
    print(f"  cd {PLAYGROUND}")
    print("  git log --oneline --all --graph   # base → teammate/main 분기 확인")
    print("  git diff main teammate -- src/    # 팀원이 뭘 고쳤는지 확인")
    print("  git merge teammate                # ← 실제 충돌 발생, Weld가 처리")
    return env


if __name__ == "__main__":
    build_playground()
    sys.exit(0)
