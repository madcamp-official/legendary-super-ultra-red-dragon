#!/bin/bash
# Weld 원클릭 설치 — weld를 처음 쓰는 사람용.
# 한 번 실행하면: durable venv + 의존성 + mergiraf 확인 + `weld` 명령 등록 +
# 전역 LLM 설정 템플릿까지 세팅한다. 이후 아무 저장소에서 `weld install` 만 하면 됨.
set -e

WELD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${WELD_VENV:-$HOME/.weld-venv}"          # iCloud 밖(홈)에 둬 스톨 회피
BIN="${WELD_BIN:-$HOME/.local/bin}"
CFG="$HOME/.config/weld"

echo "▶ Weld 설치 시작 (weld 저장소: $WELD_ROOT)"

echo "[1/6] 파이썬 venv 생성: $VENV"
[ -d "$VENV" ] || python3 -m venv "$VENV"

echo "[2/6] 의존성 설치 (1~3분)"
"$VENV/bin/pip" -q install --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/pip" -q install \
  click python-dotenv google-genai openai coverage pytest \
  tree-sitter tree-sitter-language-pack

echo "[3/6] mergiraf(구조 병합 도구) 확인"
if command -v mergiraf >/dev/null 2>&1; then
  echo "    ✓ 이미 설치됨: $(command -v mergiraf)"
elif command -v brew >/dev/null 2>&1; then
  echo "    설치 중: brew install mergiraf"
  brew install mergiraf
else
  echo "    ⚠ mergiraf 없음 + homebrew 없음. https://mergiraf.org 에서 수동 설치하세요."
fi

echo "[4/6] 'weld' 명령 등록: $BIN/weld"
mkdir -p "$BIN"
cat > "$BIN/weld" <<EOF
#!/bin/sh
# Weld 런처 (install.sh가 생성). 전역 LLM 설정을 읽고 weld.cli 실행.
# PYTHONUTF8=1: git 머지 드라이버는 로케일이 ASCII인 환경에서 호출될 수 있어,
# weld의 한글 리포트 출력이 UnicodeEncodeError로 죽는 것을 막는다.
if [ -f "$CFG/env" ]; then set -a; . "$CFG/env"; set +a; fi
exec env PYTHONPATH="$WELD_ROOT/src" PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$VENV/bin/python" -m weld.cli "\$@"
EOF
chmod +x "$BIN/weld"

echo "[5/6] 전역 LLM 설정: $CFG/env"
mkdir -p "$CFG"
if [ ! -f "$CFG/env" ]; then
  cat > "$CFG/env" <<'EOF'
# Weld가 쓰는 LLM 설정 (모든 저장소 공통). 하나만 채우면 됨.
# 아래 주석(#)을 풀고 실제 키를 넣으세요. 안 넣으면 LLM 병합은 에스컬레이션됩니다.
# --- 옵션 A: Google Gemini ---
# GEMINI_API_KEY=paste-your-real-key-here
GEMINI_MODEL=gemini-3.5-flash
# --- 옵션 B: 커스텀 OpenAI 호환 모델(예: 사내 qwen). 쓰면 위 Gemini 대신 이걸 사용 ---
# WELD_LLM_BASE_URL=http://<host>:<port>/v1
# WELD_LLM_MODEL=<model>
# WELD_LLM_API_KEY=<key>
# WELD_LLM_TIMEOUT=600
EOF
  echo "    → 템플릿 생성됨. $CFG/env 를 열어 GEMINI_API_KEY 주석을 풀고 키를 넣으세요."
else
  echo "    ✓ 기존 설정 유지"
fi

echo "[6/6] PATH 확인"
case ":$PATH:" in
  *":$BIN:"*) echo "    ✓ $BIN 이 PATH에 있음" ;;
  *)
    SHELL_RC="$HOME/.zshrc"
    LINE="export PATH=\"\$PATH:$BIN\""
    grep -qF "$LINE" "$SHELL_RC" 2>/dev/null || echo "$LINE" >> "$SHELL_RC"
    echo "    → $SHELL_RC 에 PATH 추가함. 새 터미널을 열거나 'source $SHELL_RC' 실행."
    export PATH="$PATH:$BIN"
    ;;
esac

echo ""
if "$BIN/weld" --help >/dev/null 2>&1; then
  echo "✅ 설치 완료!"
  echo ""
  echo "다음 단계:"
  echo "  1) $CFG/env 에 API 키 입력 (LLM 병합을 쓰려면)"
  echo "  2) weld를 쓸 저장소로 이동 후:  weld install"
  echo "     → 그 저장소에 머지 드라이버가 등록되고, 이후 git merge가 자동 검증됨"
else
  echo "⚠ 설치는 됐지만 'weld --help' 확인 실패. 위 로그를 확인하세요."
fi
