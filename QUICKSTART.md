# Weld 빠른 시작

설치·API 키·사용 흐름은 **[README의 "사용 흐름 (Getting Started)"](README.md#사용-흐름-getting-started)** 에
정리돼 있다 — 그쪽을 보면 된다.

세 줄 요약:

```bash
# ① 컴퓨터당 1회
git clone <이 저장소> && cd weld && ./install.sh
open -e ~/.config/weld/env      # GEMINI_API_KEY / GEMINI_MODEL 입력

# ② 저장소당 1회
cd <프로젝트> && weld install

# ③ 이후
git merge some-branch            # 충돌 시 weld가 자동 검증
```

- 모델 선택·무료 한도·문제 해결: README 참고
- 실제 충돌을 직접 만들어보는 실습: 데모 저장소의 `TUTORIAL.md`
