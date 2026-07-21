import json

from weld.langs import detect_language, effective_test_command


def test_js_delegates_to_npm_test_when_package_json_has_test_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    spec = detect_language("src/foo.js")
    assert effective_test_command(spec, tmp_path) == ("npm", "test")


def test_js_falls_back_to_static_when_no_package_json(tmp_path):
    """데모/픽스처처럼 package.json이 없으면 정적 기본값(node --test)."""
    spec = detect_language("src/foo.js")
    assert effective_test_command(spec, tmp_path) == ("node", "--test")


def test_js_falls_back_when_package_json_has_no_test_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "rollup"}}))
    spec = detect_language("src/foo.js")
    assert effective_test_command(spec, tmp_path) == ("node", "--test")


def test_js_falls_back_when_package_json_is_malformed(tmp_path):
    (tmp_path / "package.json").write_text("{not valid json")
    spec = detect_language("src/foo.js")
    assert effective_test_command(spec, tmp_path) == ("node", "--test")


def test_c_full_suite_when_no_selection(tmp_path):
    """선별 없으면 C는 전체 make test 그대로."""
    spec = detect_language("src/foo.c")
    assert effective_test_command(spec, tmp_path) == ("make", "-s", "test")


def test_c_targets_via_tests_variable(tmp_path):
    """선별이 있으면 make test에 TESTS=<파일> 변수를 붙여 타깃팅(규약).
    저장소 Makefile이 $(TESTS)를 존중하면 그 파일만 돈다."""
    spec = detect_language("src/foo.c")
    cmd = effective_test_command(spec, tmp_path, ["tests/test_foo.c"])
    assert cmd == ("make", "-s", "test", "TESTS=tests/test_foo.c")


def test_cpp_targets_via_tests_variable(tmp_path):
    spec = detect_language("src/foo.cpp")
    cmd = effective_test_command(spec, tmp_path, ["tests/test_foo.cpp", "tests/test_bar.cpp"])
    assert cmd == ("make", "-s", "test", "TESTS=tests/test_foo.cpp tests/test_bar.cpp")


# --- selected_tests: targeted 러너 명령 ---


def _pkg(tmp_path, test_script="vitest run", dev=("vitest",)):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"test": test_script},
        "devDependencies": {d: "*" for d in dev},
    }))


def test_targets_vitest_with_selected_files(tmp_path):
    _pkg(tmp_path, "vitest run", ("vitest",))
    spec = detect_language("lib/foo.js")
    cmd = effective_test_command(spec, tmp_path, ["tests/foo.test.js"])
    assert cmd == ("npx", "vitest", "run", "tests/foo.test.js")


def test_targets_jest_with_selected_files(tmp_path):
    _pkg(tmp_path, "jest", ("jest",))
    spec = detect_language("lib/foo.js")
    cmd = effective_test_command(spec, tmp_path, ["tests/foo.test.js", "tests/bar.test.js"])
    assert cmd == ("npx", "jest", "tests/foo.test.js", "tests/bar.test.js")


def test_selected_test_node_ids_reduce_to_files(tmp_path):
    """'파일::케이스' 노드 ID여도 파일 경로만 뽑아 중복 제거."""
    _pkg(tmp_path, "vitest run", ("vitest",))
    spec = detect_language("lib/foo.js")
    cmd = effective_test_command(
        spec, tmp_path, ["tests/foo.test.js::a", "tests/foo.test.js::b"]
    )
    assert cmd == ("npx", "vitest", "run", "tests/foo.test.js")


def test_selected_files_without_known_runner_use_node_test(tmp_path):
    """러너를 못 알아내면 node --test로 그 파일만 지정."""
    spec = detect_language("lib/foo.js")  # package.json 없음
    cmd = effective_test_command(spec, tmp_path, ["tests/foo.test.js"])
    assert cmd == ("node", "--test", "tests/foo.test.js")


def test_empty_selection_falls_back_to_full_suite(tmp_path):
    """빈 선별은 '관련 테스트 못 찾음' — 0개 실행(공허한 통과)보다 전체가 안전."""
    _pkg(tmp_path, "vitest run", ("vitest",))
    spec = detect_language("lib/foo.js")
    assert effective_test_command(spec, tmp_path, []) == ("npm", "test")
