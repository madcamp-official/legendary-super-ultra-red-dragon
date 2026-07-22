# Weld 빠른 시작 (처음 쓰는 사람용)

weld는 **검증되지 않은 병합이 자동으로 착지하지 못하게** 막는 git 머지 드라이버다.
충돌이 나면 후보를 만들고 테스트·뮤테이션으로 검증해서, 안전하면 자동병합하고
못 믿으면 사람에게 넘긴다. 원칙: *놓친 자동화는 있어도, 잘못된 자동화는 없다.*

---

## 1. 설치 — 컴퓨터당 딱 한 번

```bash
git clone <weld 저장소 URL>
cd weld
./install.sh
```
이게 알아서 다 한다: 파이썬 venv(iCloud 밖) + 의존성 + mergiraf 확인 +
`weld` 명령 등록 + 전역 LLM 설정 템플릿. (1~3분)

설치 후 새 터미널을 열거나 `source ~/.zshrc` 하면 `weld` 명령을 쓸 수 있다:
```bash
weld --help
```

## 2. LLM 키 넣기 — 구조합성 병합을 쓰려면

```bash
open ~/.config/weld/env      # 또는 편집기로 열기
```
`GEMINI_API_KEY=` 에 본인 키를 넣는다(Gemini). 사내 모델(OpenAI 호환)을 쓰면
`WELD_LLM_BASE_URL/MODEL/API_KEY` 를 대신 채운다. 이 설정은 **모든 저장소 공통**이다.

> 값충돌·가짜충돌 같은 **LLM 없이 되는 병합**은 키 없이도 동작한다. LLM이
> 없거나 실패하면 그냥 사람에게 에스컬레이션된다(오탐은 절대 안 남).

## 3. 저장소에 적용 — 저장소당 한 번

weld를 쓰고 싶은 프로젝트로 가서:
```bash
cd my-project
weld install
```
→ 그 저장소에 머지 드라이버가 등록되고 `.gitattributes`(`* merge=weld`)가 생긴다.

## 4. 끝 — 이제 그냥 평소처럼

```bash
git merge some-branch
```
충돌이 나면 weld가 자동으로 끼어들어 검증한다.
- **자동병합**: 검증 통과 → 그대로 커밋 (마커 없음)
- **에스컬레이션**: 못 믿음 → 익숙한 `<<<<<<< ours` 충돌 마커를 남김(사람이 해결)

---

## 꼭 알아야 할 것 2가지

1. **weld는 로컬 `git merge`에서만 돈다.** GitHub의 "Merge" 버튼(서버 병합)은
   weld를 안 부른다. 자동 검증을 받으려면 **로컬에서 병합**해야 한다.
2. **`weld install`은 저장소마다** 한 번씩 필요하다(머지 드라이버 설정은
   `.git/config`에 있어 clone에 안 따라온다). `.gitattributes`만 커밋되어 공유된다.

## 팀원에게 공유할 때 (clone 받는 사람)

```bash
# 컴퓨터에 weld가 처음이면
git clone <weld 저장소> && cd weld && ./install.sh
open ~/.config/weld/env    # API 키 입력

# weld 쓰는 프로젝트를 clone 받은 뒤
cd <그 프로젝트>
weld install               # 이 저장소에 드라이버 등록
```

## 문제 해결

| 증상 | 해결 |
|---|---|
| `command not found: weld` | 새 터미널을 열거나 `source ~/.zshrc` (PATH 반영) |
| 병합이 매번 에스컬레이션 | `~/.config/weld/env` 의 LLM 키 확인(크레딧/네트워크). 값충돌 등 LLM-무관 케이스는 되는지 먼저 확인 |
| `mergiraf` 없음 | `brew install mergiraf` |
| venv 읽기가 멈춤 | venv를 iCloud(Documents) 밖에 둔다 — install.sh는 `~/.weld-venv`(홈)에 만든다 |
