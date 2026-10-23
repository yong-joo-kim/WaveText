# 하네스 운영 규칙

메인 세션이 `pm` 역할로 아래 단계를 순서대로 진행한다. 진행 상태는 `harness/state.json`에 기록한다.

| 단계 | 담당 에이전트 | 완료 기준 | 게이트 |
|---|---|---|---|
| P0 착수 | pm | 계획서, 하네스 골격, `git init` | 사용자 승인 |
| P1 요구분석 | requirements | `docs/02_requirements/requirements.md` (FR/NFR 번호) | 사용자 승인 |
| P2 UI 시안 | ui-designer | `docs/03_design/ui/mockup.html`, `화면설계서.md` | 사용자 승인 |
| P3 구현 | developer | 전 기능 구현, 단위 테스트 통과 | 자동 |
| P4 검증 | verifier | 치명/주요 결함 0건, 요구사항 추적 100% | 자동 (실패 시 P3) |
| P5 자동시험 | test-automation | 전체 시험 통과, 실서버 연동 1회 | 자동 (실패 시 P3) |
| P6 버전관리 | version-control | 커밋 정리, 태그, CHANGELOG | 자동 |
| P7 배포 | release | 휴대용 zip, 사용설명서 | 사용자 승인 |

- P3~P5 되돌림(`loop`)은 최대 3회. 넘으면 pm이 사용자에게 보고하고 멈춘다.
- 에이전트끼리는 문서 산출물로만 정보를 넘긴다. 에이전트 정의: `.claude/agents/*.md`
- 사용자 승인은 대화창에서 받은 것만 유효하다.
