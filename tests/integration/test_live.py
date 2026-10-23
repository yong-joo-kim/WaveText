"""실서버 연동 시험 (-m live). WaveText 음성인식 서버 설정(API 키 포함)과 data/sample/*.wav가 있어야 한다.

실행: .venv/Scripts/python -m pytest -m live -s
"""
import shutil
from pathlib import Path

import pytest

from wavetext import config, paths
from wavetext.audio import convert, segment, wavio
from wavetext.stt import client

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = sorted((ROOT / "data" / "sample").glob("*.wav"))
pytestmark = [pytest.mark.live, pytest.mark.skipif(not SAMPLES, reason="data/sample/*.wav 없음")]


@pytest.fixture(scope="module")
def stt():
    try:
        s = config.load_stt(config.load())
    except config.SttConfigError as e:
        pytest.skip(str(e))
    ok, msg = client.health(s["server"])
    if not ok:
        pytest.skip(f"서버 응답 없음: {msg}")
    return s


def test_live_transcribe_segment(stt):
    a = wavio.read(SAMPLES[0])
    x = segment.cut(a.samples, a.rate, 0.0, min(8.0, a.duration))
    model = (stt.get("models") or ["model_v2"])[0]
    r = client.transcribe_with_retry(convert.to_stt_pcm(x, a.rate), stt, model, "wavetext-live")
    print("\n인식 결과:", r["transcript"])
    assert r["transcript"].strip()


def test_live_window_flow(qtbot, tmp_path, monkeypatch):
    """실제 화면·실서버로 생성 흐름 전체를 돌리고 화면을 tests/_out에 저장한다."""
    from wavetext.ui import theme
    from wavetext.ui.main_window import MainWindow
    from PySide6.QtWidgets import QApplication

    try:
        config.load_stt(config.load(ROOT / "config.local.json"))
    except config.SttConfigError as e:
        pytest.skip(f"음성인식 서버 설정 필요: {e}")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "wavetext.json")
    src = tmp_path / "녹취"
    src.mkdir()
    shutil.copy(SAMPLES[0], src / SAMPLES[0].name)
    base_cfg = config.load(ROOT / "config.local.json")
    cfg = config.load()
    cfg.update({"stt": base_cfg["stt"], "model": base_cfg["model"], "folders": {"src": str(src), "full": "", "wav": "", "part_txt": ""}})
    ui_font, mono = theme.load_fonts()
    QApplication.instance().setStyleSheet(theme.stylesheet(ui_font, mono))
    win = MainWindow(cfg, ui_font, mono)
    qtbot.addWidget(win)
    win.resize(1600, 960)
    win.show()
    qtbot.waitUntil(lambda: win.audio is not None, timeout=5000)
    win.run_src_stt(False)                                   # 1. 원본 음성인식 (단어 시간 포함)
    qtbot.waitUntil(lambda: win.src_worker is None, timeout=120000)
    assert win.src_text.toPlainText().strip(), win.st_msg.text()
    words = win.meta["words"]
    assert words and all(w["end"] >= w["start"] for w in words)
    assert paths.load_meta(win.folders, src.joinpath(SAMPLES[0].name).stem)["words"] == words
    win.wave.set_selection((1.0, min(7.0, win.audio.duration)))  # 2. 구간 선택 → 생성
    win.create_pair()
    qtbot.waitUntil(lambda: bool(win.pairs) and not win._creating, timeout=30000)
    pair = win.pairs[-1]
    expect = client.words_in(words, 1.0, min(7.0, win.audio.duration))
    assert pair.state == "ok" and pair.text == expect and expect   # 3. 선택 구간 문장 자동 출력·저장
    assert pair.txt.read_text(encoding="utf-8") == expect
    out = ROOT / "tests" / "_out"
    out.mkdir(exist_ok=True)
    qtbot.wait(300)
    win.grab().save(str(out / "live_window.png"))            # 3단계 화면
    win.run_out_stt()                                         # 4. 필요 시 분할 음성 다시 인식 → 덮어써 저장
    qtbot.waitUntil(lambda: pair.state != "recognizing", timeout=120000)
    assert pair.state == "ok" and pair.txt.read_text(encoding="utf-8").strip() == pair.text
    print("\n원본 인식:", win.src_text.toPlainText())
    print("선택 구간 문장(생성 시):", expect)
    print("분할 다시 인식:", pair.text)
    print("저장:", pair.wav, pair.txt)
