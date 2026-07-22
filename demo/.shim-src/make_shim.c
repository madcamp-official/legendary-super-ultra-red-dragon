/* demo 전용 `make` -> `mingw32-make` 포워더.
 *
 * 이 Windows 환경에는 mingw32-make만 있고 `make`라는 이름의 실행 파일이
 * PATH에 없다(langs.py의 C/C++ build_command/test_command는 "make"를
 * 하드코딩). 언어 레지스트리(langs.py)는 담당자 파일이라 건드리지 않고,
 * 대신 demo 전용으로 이 작은 포워더를 만들어 PATH에 얹는다 — 시스템
 * PATH나 mingw64 설치는 건드리지 않는다.
 */
#include <process.h>
#include <stdio.h>

/* _execvp의 Windows(MSVCRT) 구현은 실제 exec가 아니라 spawn+wait를 흉내내는
 * 방식이라 종료 코드 전달이 간헐적으로 깨진다(실측: 자식이 Error 3으로
 * 죽어도 셔틀 프로세스는 0을 반환). _spawnvp로 명시적으로 기다렸다가 그
 * 종료 코드를 그대로 exit()하는 편이 신뢰할 수 있다. */
int main(int argc, char **argv) {
    argv[0] = "mingw32-make";
    intptr_t rc = _spawnvp(_P_WAIT, "mingw32-make", (const char *const *)argv);
    if (rc == -1) {
        perror("make-shim: mingw32-make 실행 실패");
        return 127;
    }
    return (int)rc;
}
