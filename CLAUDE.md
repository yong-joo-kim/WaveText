# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트
WaveText: 현장 녹취 WAV에서 음성이 명료한 구간(최대 30초)을 잘라 음성인식 학습용 `Wav + Text` 쌍을 만드는 윈도우 데스크톱 프로그램(PySide6). 사용자와의 모든 대화·문서는 한국어로 작성한다.

## 명령
```
WaveText.bat                                         # 실행(더블클릭). PYTHONPATH=src 를 설정하고 .venv\Scripts\pythonw 로 띄운다
.venv/Scripts/python -m wavetext                     # 실행. src 레이아웃이라 .venv의 site-packages에 wavetext-src.pth(src 경로)가 있어야 한다
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q      # 전체 시험 (실서버 시험 제외)
.venv/Scripts/python -m pytest tests/unit/test_paths.py::test_next_number_uses_both_folders   # 시험 1건
.venv/Scripts/python -m pytest -m live -s            # 실서버 연동 (WaveText 음성인식 서버 설정·API 키 + data/sample/*.wav 필요, 화면을 tests/_out/live_window.png로 저장)
```
가상환경 생성: `py -3.14 -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt` 후 src 경로 등록(`.venv/Lib/site-packages/wavetext-src.pth`에 `<프로젝트>\src` 한 줄) 또는 `pip install -e .`
`.bat` 파일은 CP949·CRLF로 저장한다(UTF-8이면 cmd가 한글 줄을 명령으로 잘못 읽음, `.gitattributes` 참고).

## 구조
- `src/wavetext/audio/` — Qt 비의존 순수 로직. `wavio`(PCM 8/16/24/32비트 읽기·쓰기, 상태줄 형식 문자열, 덮어쓰기 거부), `segment`(자르기·10ms 페이드·30초 제한), `denoise`(원본 전체의 저에너지 프레임으로 잡음 문턱을 추정하는 스펙트럴 게이팅), `convert`(음성인식용 8kHz·16비트·모노 변환).
- `src/wavetext/stt/client.py` — ZIEUM 스트리밍 음성인식 웹소켓 프로토콜(ready → PCM 640바이트 → finalize → finished). `extract_words`/`words_in`으로 단어 시간·구간 문장. `connect` 인자로 시험용 가짜 소켓 주입.
- `src/wavetext/config.py` — 모든 설정(음성인식 서버 주소·포트·접속 방식·API 키·모델 포함)은 실행 폴더의 `config.local.json` 하나에만 저장한다. 기본값은 ws / 8081 / model_v2이며 서버 주소와 API 키는 기본값이 없다.
- `src/wavetext/paths.py` — 저장 폴더·파일 이름 규칙의 단일 출처. `{원본}.stt.json`(원본 음성인식 Text 폴더)에 원본 인식 단어 시간(`words`)과 분할 구간(`parts`)을 기록하며, 학습용 파일 생성 시 이 단어 시간으로 선택 구간 문장을 가져온다(분할 음성은 다시 인식하지 않음). 원본 음성 폴더 필수, 비운 저장 폴더는 각각 `원본\full-stt`(`{원본}.stt.txt`), `원본\part-wav`(`{원본}.part-N.wav`), `원본\part-stt`(`{원본}.stt-N.txt`). N은 두 분할 폴더의 기존 최댓값 + 1.
- `src/wavetext/ui/` — `main_window`(4분할 ①원본 파형 ②원본 인식 ③분할 Wave ④분할 Text, 파일 목록 서랍, 2줄 상태줄), `waveform`(직접 그리는 파형 위젯), `player`(QAudioSink, 장치 샘플레이트로 변환 후 재생), `workers`(음성인식 QThread: 신호에 작업자 자신을 실어 메인 창 바운드 메서드로 받는다 — 람다 연결 금지), `theme`(KRDS 색 토큰·스타일시트), `dialogs`.
- 시험 `tests/`: 음성은 `conftest.synth_speech`로 합성. 화면 시험은 `MainWindow(stt_factory=FakeWorker)`로 서버 없이 실행하고 `config.CONFIG_PATH`를 임시 경로로 바꾼다.

## 하네스
`harness/README.md`(단계 P0~P7, 게이트), `harness/state.json`(현재 단계), 에이전트 정의 `.claude/agents/*.md`. 사용자 승인 게이트(P0·P1·P2·P7)는 대화창 승인만 유효하다. 문서: `docs/01_pm` 계획, `docs/02_requirements` 요구(FR/NFR), `docs/03_design` 화면설계서·시안(KRDS 가이드라인 기준), `docs/04_test` 검증·시험 결과.

## 주의
- WaveText는 다른 제품과 완전히 분리된 독립 제품이다. 다른 프로그램의 파일·설정을 읽거나 화면·문서에 다른 제품 이름을 쓰지 않는다. 사내 서버 주소·API 키는 저장소에 넣지 않는다(PC별 config.local.json).
- `config.local.json`, `data/`, 가이드라인 PDF는 저장소에 넣지 않는다(.gitignore).
- 사용자가 확정한 폴더·파일 이름 문구(저장 폴더 창: "원본 음성인식 Text", "분할 Wav", "분할 음성인식 Text")는 바꾸지 않는다.
