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


def test_c_is_unaffected_by_resolver(tmp_path):
    """C는 이미 make(저장소 빌드시스템)에 위임하는 구조라 그대로."""
    spec = detect_language("src/foo.c")
    assert effective_test_command(spec, tmp_path) == ("make", "-s", "test")
