# [재준형 전달] sandbox.py — 실제 JS/TS 저장소 지원을 위한 2가지 수정

## 배경 (실측으로 발견)

실제 axios 저장소(vitest 사용)에 weld 전체 파이프라인을 태워봤더니, 분류→LLM→검증→뮤테이션→판정이 **끝까지 정상 동작**했다 (자동병합, 뮤테이션 8/9 kill). 다만 그렇게 되게 하려고 두 가지를 수동 우회해야 했는데, 이게 곧 sandbox.py가 실사용 전에 고쳐야 할 지점이다.

## 문제 1 — 격리본에 node_modules가 없다

`run_in_sandbox`는 git worktree로 격리하는데, **node_modules는 .gitignore돼서 worktree에 안 들어온다.** 그러면 vitest/jest가 실행 자체를 못 한다 (모듈 못 찾음). C/C++의 빌드 산출물, Python venv도 같은 계열 문제.

**해결**: worktree를 만든 뒤, 원본 저장소의 node_modules를 worktree에 **심링크**로 붙인다. 복사(186MB)는 뮤턴트마다 하면 너무 느리니 심링크가 맞다.

```python
# _add_worktree 직후, 후보 파일 쓰기 전에:
src_nm = Path(repo_path) / "node_modules"
if src_nm.exists() and not (worktree / "node_modules").exists():
    os.symlink(src_nm.resolve(), worktree / "node_modules")
```

- `repo_path`는 원본 저장소(절대경로로 resolve해서 심링크할 것 — worktree 안에서 상대경로면 깨진다).
- node_modules가 없으면(설치 안 된 저장소) 그냥 건너뛰면 된다 — 그 경우 어차피 테스트가 안 도니 검증 실패로 정상 처리됨.
- 참고: 나(김민재)는 verify/mutation_ts.py에 같은 심링크를 이미 넣었으니 그 구현을 참고해도 된다.

## 문제 2 — test_command가 `node --test`로 고정이다

`langs.py`의 JS/TS `test_command=("node","--test")`는 데모 편의용이라, 실제 저장소(vitest/jest/mocha)에선 테스트를 못 돌린다. 그래서 **langs.py에 러너 위임 리졸버를 추가**했다 (내가 이미 커밋):

```python
from weld.langs import effective_test_command  # 신규

# _check_compiles_lang / _run_tests_lang 에서 spec.test_command 대신:
cmd = effective_test_command(spec, worktree)   # worktree 루트를 넘긴다
```

`effective_test_command(spec, repo_root)`의 동작:
- 저장소 루트에 `package.json`이 있고 `scripts.test`가 정의돼 있으면 → `("npm","test")` (저장소 자신의 러너로 위임)
- 없으면 → `spec.test_command` (데모/픽스처의 `node --test` 그대로)

즉 sandbox.py는 `spec.test_command`를 직접 쓰지 말고 **`effective_test_command(spec, worktree)`를 쓰면** 실제 저장소와 데모 둘 다 동작한다. `spec.build_command`(C/C++)는 그대로 둬도 된다.

## 검증 방법

수정 후, 실제 저장소로 확인:
```bash
cd <axios 클론>  # npm install 되어 있어야 함
# combineURLs 같은 소스에 충돌 만들고 weld merge 실행 → vitest로 검증되는지
```
데모/유닛은 기존대로 통과해야 한다 (`pytest tests/`).

## 왜 이게 중요한가

이 둘을 고치면 "다국어 실사용"이 우회 없이 열린다. 실측에서 이미 (심링크+위임을 수동으로 걸었을 때) 실제 axios+vitest에서 전체 파이프라인이 자동병합까지 완주하는 걸 확인했으니, 코드에 반영만 하면 된다.
