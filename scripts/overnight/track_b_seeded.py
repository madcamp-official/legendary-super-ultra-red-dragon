"""Track B-1 — 4개 언어 × 6유형 씨딩 충돌 통제 실험 (풀 파이프라인).

언어(Python/JS/C/C++)마다 같은 6가지 충돌 유형을 심고
classify → generate(qwen) → verify → mutation → decide 풀 사이클을 돌린다:

  m1 spurious    — 서로 다른 함수 수정 → mergiraf 자동 병합 기대
  m2 value       — 같은 상수 양쪽 변경 → 테스트가 판정 (ours 채택 기대)
  m3 struct-strong — 양쪽 가드 합성 필요 + 강한 테스트 → LLM 합성 자동병합 기대
  m4 struct-weak — 같은 합성이지만 약한 테스트 → 뮤테이션 저점수 → 에스컬레이션 기대
  m5 bug-side    — theirs가 ours의 안전장치를 지우는 재작성 → 올바른 합성만 통과
  m6 break       — theirs가 미정의 함수 호출 → 컴파일/테스트 게이트 → 에스컬레이션 기대

핵심 지표: 오탐(잘못된 자동병합) 0건.
결과는 케이스별 JSONL 증분 저장 (재실행 시 완료분 스킵).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

WELD = "/Users/kimminjae/Documents/몰입캠프/3주차/weld"
sys.path.insert(0, f"{WELD}/src")
os.chdir(WELD)

from weld.evaluation.cases import EvalCase  # noqa: E402
from weld.evaluation.multilang import run_case  # noqa: E402

OUT = f"{WELD}/results/overnight-0721/track_b_seeded.jsonl"
REPO = os.environ.get(
    "TRACK_B_REPO",
    "/private/tmp/claude-501/-Users-kimminjae-Documents------1--/10bf48a6-ad74-4827-acbb-873ca1494c72/scratchpad/track_b_repo",
)

D = textwrap.dedent


@dataclass
class Seed:
    id: str
    file_path: str
    base: str
    ours: str
    theirs: str
    gt: str | None            # 사람이라면 채택했을 내용 (None = 정답은 에스컬레이션)
    tests: dict[str, str]     # {테스트파일경로: 내용}
    baseline: str             # 저장소에 체크인해 둘 초록 상태 (전체 스위트 통과용)
    expected: str             # "auto" | "escalate" | "any"
    must_contain: list[str] = field(default_factory=list)      # 자동병합 시 필수 포함
    must_not_contain: list[str] = field(default_factory=list)  # 자동병합 시 금지


# ---------------------------------------------------------------- Python 6종
def py_seeds() -> list[Seed]:
    s: list[Seed] = []
    # m1 spurious
    base = D("""\
        def add(a, b):
            return a + b


        def mul(a, b):
            return a * b
        """)
    ours = base.replace("return a + b", "return int(a) + int(b)")
    theirs = base.replace("return a * b", "return a * b * 1")
    gt = ours.replace("return a * b", "return a * b * 1")
    s.append(Seed(
        id="py-m1-spurious", file_path="mathx.py", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="auto",
        tests={"tests/test_mathx.py": D("""\
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from mathx import add, mul

            def test_add():
                assert add("2", "3") == 5

            def test_mul():
                assert mul(2, 3) == 6
            """)},
    ))
    # m2 value
    base = D("""\
        MAX_RETRIES = 3


        def retries_left(used):
            return MAX_RETRIES - used
        """)
    ours = base.replace("MAX_RETRIES = 3", "MAX_RETRIES = 5")
    theirs = base.replace("MAX_RETRIES = 3", "MAX_RETRIES = 8")
    s.append(Seed(
        id="py-m2-value", file_path="retry.py", base=base, ours=ours,
        theirs=theirs, gt=ours, baseline=ours, expected="any",
        must_contain=["MAX_RETRIES = 5"],
        tests={"tests/test_retry.py": D("""\
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from retry import retries_left

            def test_left():
                assert retries_left(0) == 5

            def test_used():
                assert retries_left(2) == 3
            """)},
    ))
    # m3 struct-strong
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
    gt = D("""\
        def parse_port(s):
            n = int(s)
            if n < 0:
                raise ValueError("negative port")
            if n > 65535:
                raise ValueError("port too large")
            return n
        """)
    s.append(Seed(
        id="py-m3-struct-strong", file_path="ports.py", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="auto",
        must_contain=["n < 0", "n > 65535"],
        tests={"tests/test_ports.py": D("""\
            import sys, os
            import pytest
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from ports import parse_port

            def test_ok():
                assert parse_port("8080") == 8080

            def test_boundaries():
                assert parse_port("0") == 0
                assert parse_port("65535") == 65535

            def test_negative():
                with pytest.raises(ValueError, match="negative"):
                    parse_port("-1")

            def test_too_big():
                with pytest.raises(ValueError, match="large"):
                    parse_port("70000")
            """)},
    ))
    # m4 struct-weak (같은 합성, 약한 테스트)
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
    gt = D("""\
        def validate_age(age):
            if age < 0:
                raise ValueError("negative age")
            if age > 150:
                raise ValueError("age too large")
            return age
        """)
    s.append(Seed(
        id="py-m4-struct-weak", file_path="ages.py", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="escalate",
        tests={"tests/test_ages.py": D("""\
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from ages import validate_age

            def test_ok():
                assert validate_age(30) == 30
            """)},
    ))
    # m5 bug-side (theirs 재작성이 ours의 안전장치를 지움)
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
    gt = D("""\
        def clamp(x, lo, hi):
            if lo > hi:
                raise ValueError("bad range")
            return min(max(x, lo), hi)
        """)
    s.append(Seed(
        id="py-m5-bug-side", file_path="clampx.py", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="any",
        must_contain=["lo > hi"],
        tests={"tests/test_clampx.py": D("""\
            import sys, os
            import pytest
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from clampx import clamp

            def test_mid():
                assert clamp(5, 0, 10) == 5

            def test_low():
                assert clamp(-1, 0, 10) == 0

            def test_high():
                assert clamp(11, 0, 10) == 10

            def test_edges():
                assert clamp(0, 0, 10) == 0
                assert clamp(10, 0, 10) == 10

            def test_point_range():
                assert clamp(5, 5, 5) == 5

            def test_bad_range():
                with pytest.raises(ValueError, match="bad range"):
                    clamp(1, 5, 2)
            """)},
    ))
    # m6 break (theirs가 미정의 audit_log 호출)
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
    s.append(Seed(
        id="py-m6-break", file_path="normx.py", base=base, ours=ours,
        theirs=theirs, gt=ours, baseline=ours, expected="any",
        must_not_contain=["audit_log"],
        tests={"tests/test_normx.py": D("""\
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from normx import normalize

            def test_lower():
                assert normalize("  A ") == "a"

            def test_plain():
                assert normalize("x") == "x"
            """)},
    ))
    return s


# ---------------------------------------------------------------- JS 6종
def js_seeds() -> list[Seed]:
    s: list[Seed] = []
    base = D("""\
        function add(a, b) {
          return a + b;
        }

        function mul(a, b) {
          return a * b;
        }

        module.exports = { add, mul };
        """)
    ours = base.replace("return a + b;", "return Number(a) + Number(b);")
    theirs = base.replace("return a * b;", "return a * b * 1;")
    gt = ours.replace("return a * b;", "return a * b * 1;")
    s.append(Seed(
        id="js-m1-spurious", file_path="src/mathx.js", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="auto",
        tests={"tests/mathx.test.js": D("""\
            const test = require('node:test');
            const assert = require('node:assert');
            const { add, mul } = require('../src/mathx.js');

            test('add coerces', () => { assert.strictEqual(add('2', '3'), 5); });
            test('mul', () => { assert.strictEqual(mul(2, 3), 6); });
            """)},
    ))
    base = D("""\
        const MAX_RETRIES = 3;

        function retriesLeft(used) {
          return MAX_RETRIES - used;
        }

        module.exports = { retriesLeft };
        """)
    ours = base.replace("MAX_RETRIES = 3", "MAX_RETRIES = 5")
    theirs = base.replace("MAX_RETRIES = 3", "MAX_RETRIES = 8")
    s.append(Seed(
        id="js-m2-value", file_path="src/retry.js", base=base, ours=ours,
        theirs=theirs, gt=ours, baseline=ours, expected="any",
        must_contain=["MAX_RETRIES = 5"],
        tests={"tests/retry.test.js": D("""\
            const test = require('node:test');
            const assert = require('node:assert');
            const { retriesLeft } = require('../src/retry.js');

            test('left', () => { assert.strictEqual(retriesLeft(0), 5); });
            test('used', () => { assert.strictEqual(retriesLeft(2), 3); });
            """)},
    ))
    base = D("""\
        function parsePort(s) {
          const n = parseInt(s, 10);
          return n;
        }

        module.exports = { parsePort };
        """)
    ours = base.replace(
        "  return n;",
        "  if (n < 0) {\n    throw new Error('negative port');\n  }\n  return n;",
    )
    theirs = base.replace(
        "  return n;",
        "  if (n > 65535) {\n    throw new Error('port too large');\n  }\n  return n;",
    )
    gt = base.replace(
        "  return n;",
        "  if (n < 0) {\n    throw new Error('negative port');\n  }\n"
        "  if (n > 65535) {\n    throw new Error('port too large');\n  }\n  return n;",
    )
    s.append(Seed(
        id="js-m3-struct-strong", file_path="src/ports.js", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="auto",
        must_contain=["n < 0", "n > 65535"],
        tests={"tests/ports.test.js": D("""\
            const test = require('node:test');
            const assert = require('node:assert');
            const { parsePort } = require('../src/ports.js');

            test('ok', () => { assert.strictEqual(parsePort('8080'), 8080); });
            test('boundaries', () => {
              assert.strictEqual(parsePort('0'), 0);
              assert.strictEqual(parsePort('65535'), 65535);
            });
            test('negative', () => { assert.throws(() => parsePort('-1'), /negative/); });
            test('too big', () => { assert.throws(() => parsePort('70000'), /large/); });
            """)},
    ))
    base = D("""\
        function validateAge(age) {
          return age;
        }

        module.exports = { validateAge };
        """)
    ours = base.replace(
        "  return age;",
        "  if (age < 0) {\n    throw new Error('negative age');\n  }\n  return age;",
    )
    theirs = base.replace(
        "  return age;",
        "  if (age > 150) {\n    throw new Error('age too large');\n  }\n  return age;",
    )
    gt = base.replace(
        "  return age;",
        "  if (age < 0) {\n    throw new Error('negative age');\n  }\n"
        "  if (age > 150) {\n    throw new Error('age too large');\n  }\n  return age;",
    )
    s.append(Seed(
        id="js-m4-struct-weak", file_path="src/ages.js", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="escalate",
        tests={"tests/ages.test.js": D("""\
            const test = require('node:test');
            const assert = require('node:assert');
            const { validateAge } = require('../src/ages.js');

            test('ok', () => { assert.strictEqual(validateAge(30), 30); });
            """)},
    ))
    base = D("""\
        function clamp(x, lo, hi) {
          if (x < lo) {
            return lo;
          }
          if (x > hi) {
            return hi;
          }
          return x;
        }

        module.exports = { clamp };
        """)
    ours = base.replace(
        "  if (x < lo) {",
        "  if (lo > hi) {\n    throw new Error('bad range');\n  }\n  if (x < lo) {",
    )
    theirs = D("""\
        function clamp(x, lo, hi) {
          return Math.min(Math.max(x, lo), hi);
        }

        module.exports = { clamp };
        """)
    gt = D("""\
        function clamp(x, lo, hi) {
          if (lo > hi) {
            throw new Error('bad range');
          }
          return Math.min(Math.max(x, lo), hi);
        }

        module.exports = { clamp };
        """)
    s.append(Seed(
        id="js-m5-bug-side", file_path="src/clampx.js", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="any",
        must_contain=["lo > hi"],
        tests={"tests/clampx.test.js": D("""\
            const test = require('node:test');
            const assert = require('node:assert');
            const { clamp } = require('../src/clampx.js');

            test('mid', () => { assert.strictEqual(clamp(5, 0, 10), 5); });
            test('low', () => { assert.strictEqual(clamp(-1, 0, 10), 0); });
            test('high', () => { assert.strictEqual(clamp(11, 0, 10), 10); });
            test('edges', () => {
              assert.strictEqual(clamp(0, 0, 10), 0);
              assert.strictEqual(clamp(10, 0, 10), 10);
            });
            test('point range', () => { assert.strictEqual(clamp(5, 5, 5), 5); });
            test('bad range', () => { assert.throws(() => clamp(1, 5, 2), /bad range/); });
            """)},
    ))
    base = D("""\
        function normalize(s) {
          return s.trim();
        }

        module.exports = { normalize };
        """)
    ours = base.replace("return s.trim();", "return s.trim().toLowerCase();")
    theirs = base.replace("return s.trim();", "auditLog(s);\n  return s.trim();")
    s.append(Seed(
        id="js-m6-break", file_path="src/normx.js", base=base, ours=ours,
        theirs=theirs, gt=ours, baseline=ours, expected="any",
        must_not_contain=["auditLog"],
        tests={"tests/normx.test.js": D("""\
            const test = require('node:test');
            const assert = require('node:assert');
            const { normalize } = require('../src/normx.js');

            test('lower', () => { assert.strictEqual(normalize('  A '), 'a'); });
            test('plain', () => { assert.strictEqual(normalize('x'), 'x'); });
            """)},
    ))
    return s


# ---------------------------------------------------------------- C 6종
def c_seeds() -> list[Seed]:
    s: list[Seed] = []
    base = D("""\
        int add_c(int a, int b) {
            return a + b;
        }

        int mul_c(int a, int b) {
            return a * b;
        }
        """)
    ours = base.replace("return a + b;", "return a + b + 0;")
    theirs = base.replace("return a * b;", "return a * b * 1;")
    gt = ours.replace("return a * b;", "return a * b * 1;")
    s.append(Seed(
        id="c-m1-spurious", file_path="src/mathx_c.c", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="auto",
        tests={"tests/test_mathx_c.c": D("""\
            #include <assert.h>
            int add_c(int a, int b);
            int mul_c(int a, int b);
            int main(void) {
                assert(add_c(2, 3) == 5);
                assert(mul_c(2, 3) == 6);
                return 0;
            }
            """)},
    ))
    base = D("""\
        static const int MAX_RETRIES = 3;

        int retries_left_c(int used) {
            return MAX_RETRIES - used;
        }
        """)
    ours = base.replace("MAX_RETRIES = 3", "MAX_RETRIES = 5")
    theirs = base.replace("MAX_RETRIES = 3", "MAX_RETRIES = 8")
    s.append(Seed(
        id="c-m2-value", file_path="src/retry_c.c", base=base, ours=ours,
        theirs=theirs, gt=ours, baseline=ours, expected="any",
        must_contain=["MAX_RETRIES = 5"],
        tests={"tests/test_retry_c.c": D("""\
            #include <assert.h>
            int retries_left_c(int used);
            int main(void) {
                assert(retries_left_c(0) == 5);
                assert(retries_left_c(2) == 3);
                return 0;
            }
            """)},
    ))
    base = D("""\
        int parse_port_c(int n) {
            return n;
        }
        """)
    ours = D("""\
        int parse_port_c(int n) {
            if (n < 0) {
                return -1;
            }
            return n;
        }
        """)
    theirs = D("""\
        int parse_port_c(int n) {
            if (n > 65535) {
                return -1;
            }
            return n;
        }
        """)
    gt = D("""\
        int parse_port_c(int n) {
            if (n < 0) {
                return -1;
            }
            if (n > 65535) {
                return -1;
            }
            return n;
        }
        """)
    s.append(Seed(
        id="c-m3-struct-strong", file_path="src/ports_c.c", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="auto",
        must_contain=["n < 0", "n > 65535"],
        tests={"tests/test_ports_c.c": D("""\
            #include <assert.h>
            int parse_port_c(int n);
            int main(void) {
                assert(parse_port_c(8080) == 8080);
                assert(parse_port_c(0) == 0);
                assert(parse_port_c(65535) == 65535);
                assert(parse_port_c(-5) == -1);
                assert(parse_port_c(70000) == -1);
                return 0;
            }
            """)},
    ))
    base = D("""\
        int validate_age_c(int age) {
            return age;
        }
        """)
    ours = D("""\
        int validate_age_c(int age) {
            if (age < 0) {
                return -1;
            }
            return age;
        }
        """)
    theirs = D("""\
        int validate_age_c(int age) {
            if (age > 150) {
                return -1;
            }
            return age;
        }
        """)
    gt = D("""\
        int validate_age_c(int age) {
            if (age < 0) {
                return -1;
            }
            if (age > 150) {
                return -1;
            }
            return age;
        }
        """)
    s.append(Seed(
        id="c-m4-struct-weak", file_path="src/ages_c.c", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="escalate",
        tests={"tests/test_ages_c.c": D("""\
            #include <assert.h>
            int validate_age_c(int age);
            int main(void) {
                assert(validate_age_c(30) == 30);
                return 0;
            }
            """)},
    ))
    base = D("""\
        int clamp_c(int x, int lo, int hi) {
            if (x < lo) {
                return lo;
            }
            if (x > hi) {
                return hi;
            }
            return x;
        }
        """)
    ours = D("""\
        int clamp_c(int x, int lo, int hi) {
            if (lo > hi) {
                return -1;
            }
            if (x < lo) {
                return lo;
            }
            if (x > hi) {
                return hi;
            }
            return x;
        }
        """)
    theirs = D("""\
        int clamp_c(int x, int lo, int hi) {
            int r = x < lo ? lo : x;
            return r > hi ? hi : r;
        }
        """)
    gt = D("""\
        int clamp_c(int x, int lo, int hi) {
            if (lo > hi) {
                return -1;
            }
            int r = x < lo ? lo : x;
            return r > hi ? hi : r;
        }
        """)
    s.append(Seed(
        id="c-m5-bug-side", file_path="src/clampx_c.c", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="any",
        must_contain=["lo > hi"],
        tests={"tests/test_clampx_c.c": D("""\
            #include <assert.h>
            int clamp_c(int x, int lo, int hi);
            int main(void) {
                assert(clamp_c(5, 0, 10) == 5);
                assert(clamp_c(-1, 0, 10) == 0);
                assert(clamp_c(11, 0, 10) == 10);
                assert(clamp_c(0, 0, 10) == 0);
                assert(clamp_c(10, 0, 10) == 10);
                assert(clamp_c(5, 5, 5) == 5);
                assert(clamp_c(1, 5, 2) == -1);
                return 0;
            }
            """)},
    ))
    base = D("""\
        int normalize_c(int x) {
            return x < 0 ? -x : x;
        }
        """)
    ours = base.replace("return x < 0 ? -x : x;", "return (x < 0 ? -x : x) % 1000;")
    theirs = base.replace(
        "return x < 0 ? -x : x;", "audit_log(x);\n    return x < 0 ? -x : x;"
    )
    s.append(Seed(
        id="c-m6-break", file_path="src/normx_c.c", base=base, ours=ours,
        theirs=theirs, gt=ours, baseline=ours, expected="any",
        must_not_contain=["audit_log"],
        tests={"tests/test_normx_c.c": D("""\
            #include <assert.h>
            int normalize_c(int x);
            int main(void) {
                assert(normalize_c(-1500) == 500);
                assert(normalize_c(7) == 7);
                return 0;
            }
            """)},
    ))
    return s


# ---------------------------------------------------------------- C++ 6종
def cpp_seeds() -> list[Seed]:
    s: list[Seed] = []
    base = D("""\
        int add_xx(int a, int b) {
            return a + b;
        }

        int mul_xx(int a, int b) {
            return a * b;
        }
        """)
    ours = base.replace("return a + b;", "return a + b + 0;")
    theirs = base.replace("return a * b;", "return a * b * 1;")
    gt = ours.replace("return a * b;", "return a * b * 1;")
    s.append(Seed(
        id="cpp-m1-spurious", file_path="src/mathx_xx.cpp", base=base, ours=ours,
        theirs=theirs, gt=gt, baseline=gt, expected="auto",
        tests={"tests/test_mathx_xx.cpp": D("""\
            #include <cassert>
            int add_xx(int a, int b);
            int mul_xx(int a, int b);
            int main() {
                assert(add_xx(2, 3) == 5);
                assert(mul_xx(2, 3) == 6);
                return 0;
            }
            """)},
    ))
    base = D("""\
        static const int kMaxRetries = 3;

        int retries_left_xx(int used) {
            return kMaxRetries - used;
        }
        """)
    ours = base.replace("kMaxRetries = 3", "kMaxRetries = 5")
    theirs = base.replace("kMaxRetries = 3", "kMaxRetries = 8")
    s.append(Seed(
        id="cpp-m2-value", file_path="src/retry_xx.cpp", base=base, ours=ours,
        theirs=theirs, gt=ours, baseline=ours, expected="any",
        must_contain=["kMaxRetries = 5"],
        tests={"tests/test_retry_xx.cpp": D("""\
            #include <cassert>
            int retries_left_xx(int used);
            int main() {
                assert(retries_left_xx(0) == 5);
                assert(retries_left_xx(2) == 3);
                return 0;
            }
            """)},
    ))
    base = D("""\
        int parse_port_xx(int n) {
            return n;
        }
        """)
    ours = D("""\
        int parse_port_xx(int n) {
            if (n < 0) {
                return -1;
            }
            return n;
        }
        """)
    theirs = D("""\
        int parse_port_xx(int n) {
            if (n > 65535) {
                return -1;
            }
            return n;
        }
        """)
    gt = D("""\
        int parse_port_xx(int n) {
            if (n < 0) {
                return -1;
            }
            if (n > 65535) {
                return -1;
            }
            return n;
        }
        """)
    s.append(Seed(
        id="cpp-m3-struct-strong", file_path="src/ports_xx.cpp", base=base,
        ours=ours, theirs=theirs, gt=gt, baseline=gt, expected="auto",
        must_contain=["n < 0", "n > 65535"],
        tests={"tests/test_ports_xx.cpp": D("""\
            #include <cassert>
            int parse_port_xx(int n);
            int main() {
                assert(parse_port_xx(8080) == 8080);
                assert(parse_port_xx(0) == 0);
                assert(parse_port_xx(65535) == 65535);
                assert(parse_port_xx(-5) == -1);
                assert(parse_port_xx(70000) == -1);
                return 0;
            }
            """)},
    ))
    base = D("""\
        int validate_age_xx(int age) {
            return age;
        }
        """)
    ours = D("""\
        int validate_age_xx(int age) {
            if (age < 0) {
                return -1;
            }
            return age;
        }
        """)
    theirs = D("""\
        int validate_age_xx(int age) {
            if (age > 150) {
                return -1;
            }
            return age;
        }
        """)
    gt = D("""\
        int validate_age_xx(int age) {
            if (age < 0) {
                return -1;
            }
            if (age > 150) {
                return -1;
            }
            return age;
        }
        """)
    s.append(Seed(
        id="cpp-m4-struct-weak", file_path="src/ages_xx.cpp", base=base,
        ours=ours, theirs=theirs, gt=gt, baseline=gt, expected="escalate",
        tests={"tests/test_ages_xx.cpp": D("""\
            #include <cassert>
            int validate_age_xx(int age);
            int main() {
                assert(validate_age_xx(30) == 30);
                return 0;
            }
            """)},
    ))
    base = D("""\
        #include <algorithm>

        int clamp_xx(int x, int lo, int hi) {
            if (x < lo) {
                return lo;
            }
            if (x > hi) {
                return hi;
            }
            return x;
        }
        """)
    ours = base.replace(
        "    if (x < lo) {",
        "    if (lo > hi) {\n        return -1;\n    }\n    if (x < lo) {",
    )
    theirs = D("""\
        #include <algorithm>

        int clamp_xx(int x, int lo, int hi) {
            return std::min(std::max(x, lo), hi);
        }
        """)
    gt = D("""\
        #include <algorithm>

        int clamp_xx(int x, int lo, int hi) {
            if (lo > hi) {
                return -1;
            }
            return std::min(std::max(x, lo), hi);
        }
        """)
    s.append(Seed(
        id="cpp-m5-bug-side", file_path="src/clampx_xx.cpp", base=base,
        ours=ours, theirs=theirs, gt=gt, baseline=gt, expected="any",
        must_contain=["lo > hi"],
        tests={"tests/test_clampx_xx.cpp": D("""\
            #include <cassert>
            int clamp_xx(int x, int lo, int hi);
            int main() {
                assert(clamp_xx(5, 0, 10) == 5);
                assert(clamp_xx(-1, 0, 10) == 0);
                assert(clamp_xx(11, 0, 10) == 10);
                assert(clamp_xx(0, 0, 10) == 0);
                assert(clamp_xx(10, 0, 10) == 10);
                assert(clamp_xx(5, 5, 5) == 5);
                assert(clamp_xx(1, 5, 2) == -1);
                return 0;
            }
            """)},
    ))
    base = D("""\
        int normalize_xx(int x) {
            return x < 0 ? -x : x;
        }
        """)
    ours = base.replace("return x < 0 ? -x : x;", "return (x < 0 ? -x : x) % 1000;")
    theirs = base.replace(
        "return x < 0 ? -x : x;", "audit_log(x);\n    return x < 0 ? -x : x;"
    )
    s.append(Seed(
        id="cpp-m6-break", file_path="src/normx_xx.cpp", base=base, ours=ours,
        theirs=theirs, gt=ours, baseline=ours, expected="any",
        must_not_contain=["audit_log"],
        tests={"tests/test_normx_xx.cpp": D("""\
            #include <cassert>
            int normalize_xx(int x);
            int main() {
                assert(normalize_xx(-1500) == 500);
                assert(normalize_xx(7) == 7);
                return 0;
            }
            """)},
    ))
    return s


_MAKEFILE = D("""\
    CC ?= cc
    CXX ?= c++
    ALL_TESTS ?= $(wildcard tests/test_*.c) $(wildcard tests/test_*.cpp)
    TESTS ?= $(ALL_TESTS)
    .PHONY: all test
    all:
    \t@for t in $(TESTS); do \\
    \t  b=.build_$$(basename $$t | tr . _); \\
    \t  case $$t in \\
    \t    *.cpp) $(CXX) -std=c++17 -o $$b src/*.cpp $$t || exit 1 ;; \\
    \t    *.c)   $(CC) -o $$b src/*.c $$t || exit 1 ;; \\
    \t  esac; \\
    \tdone
    test:
    \t@for t in $(TESTS); do \\
    \t  b=.build_$$(basename $$t | tr . _); \\
    \t  case $$t in \\
    \t    *.cpp) $(CXX) -std=c++17 -o $$b src/*.cpp $$t && ./$$b || exit 1 ;; \\
    \t    *.c)   $(CC) -o $$b src/*.c $$t && ./$$b || exit 1 ;; \\
    \t  esac; \\
    \tdone
    """)


def build_repo(seeds: list[Seed], root: str) -> None:
    """모든 시드의 baseline(초록 상태) + 테스트로 규약 저장소를 구성한다."""
    rootp = Path(root)
    if rootp.exists():
        shutil.rmtree(rootp)
    (rootp / "src").mkdir(parents=True)
    (rootp / "tests").mkdir()
    (rootp / "Makefile").write_text(_MAKEFILE)
    for sd in seeds:
        f = rootp / sd.file_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(sd.baseline)
        for tp, tc in sd.tests.items():
            t = rootp / tp
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(tc)


def verdict(rec: dict, sd: Seed) -> str:
    """케이스 결과를 안전성 관점으로 판정한다."""
    if rec.get("error"):
        return "error"
    d = rec.get("decision", {})
    accepted = d.get("accepted", False)
    content = rec.get("accepted_content", "")
    if accepted:
        semantic_ok = all(m in content for m in sd.must_contain) and not any(
            m in content for m in sd.must_not_contain
        )
        gt_ok = sd.gt is not None and content.strip() == sd.gt.strip()
        if not semantic_ok:
            return "auto_WRONG"          # 오탐! 잘못된 내용을 자동 병합
        if sd.expected == "escalate":
            return "auto_but_expected_escalate"  # 약테스트인데 자동 — 검토 필요
        return "auto_correct_exact" if gt_ok else "auto_correct_semantic"
    if sd.expected == "auto":
        return "escalated_missed"        # 자동됐어야 하는데 놓침 (안전하지만 아쉬움)
    return "escalated_ok"                # 설계대로 사람에게 (안전)


def main() -> None:
    seeds = py_seeds() + js_seeds() + c_seeds() + cpp_seeds()
    only = os.environ.get("TRACK_B_ONLY", "")
    if only:
        seeds = [sd for sd in seeds if only in sd.id]
    build_repo(py_seeds() + js_seeds() + c_seeds() + cpp_seeds(), REPO)
    print(f"규약 저장소 구성: {REPO} ({len(seeds)}케이스 실행 예정)", flush=True)

    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if os.path.exists(OUT) and not os.environ.get("TRACK_B_FRESH"):
        for l in open(OUT):
            try:
                done.add(json.loads(l)["id"])
            except Exception:  # noqa: BLE001
                pass

    out_f = open(OUT, "a")
    for i, sd in enumerate(seeds):
        if sd.id in done:
            continue
        case = EvalCase(
            id=sd.id, base=sd.base, ours=sd.ours, theirs=sd.theirs,
            file_path=sd.file_path, ground_truth_resolution=sd.gt,
        )
        t0 = time.time()
        try:
            rec = run_case(case, REPO)
        except Exception as e:  # noqa: BLE001
            rec = {"id": sd.id, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        rec["expected"] = sd.expected
        rec["verdict"] = verdict(rec, sd)
        rec["lang_group"] = sd.id.split("-")[0]
        rec["wall_seconds"] = round(time.time() - t0, 1)
        rec.pop("accepted_content", None)  # 로그 슬림화 (verdict 계산 후 제거)
        out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out_f.flush()
        print(f"[{i + 1}/{len(seeds)}] {sd.id}: {rec['verdict']} "
              f"({rec['wall_seconds']}s)", flush=True)

    out_f.close()
    # 요약
    res = [json.loads(l) for l in open(OUT)]
    from collections import Counter, defaultdict
    print("\n===== Track B-1 요약 =====", flush=True)
    by_lang: dict[str, Counter] = defaultdict(Counter)
    for r in res:
        by_lang[r.get("lang_group", "?")][r.get("verdict", "?")] += 1
    for lang, cnt in sorted(by_lang.items()):
        print(f"  {lang}: {dict(cnt)}")
    wrong = sum(1 for r in res if r.get("verdict") == "auto_WRONG")
    print(f"  ★ 오탐(auto_WRONG): {wrong}건 (목표 0)")


if __name__ == "__main__":
    main()
