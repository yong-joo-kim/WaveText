"""검증보고서(P4 1차) 주요·경미 결함 회귀 시험과, 시험이 없던 요구(FR-05·06·17·21, NFR-04·06·07) 시험."""
import threading

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtMultimedia import QAudioFormat

from wavetext import paths
from wavetext.ui import theme
from wavetext.ui.dialogs import SHORTCUTS, HelpDialog
from wavetext.ui.main_window import set_badge
from wavetext.ui.player import _Stream
from wavetext.ui.workers import SttWorker
from tests.gui.test_main_window import FakeWorker, make_pair, src_stt, win  # noqa: F401


class ManualWorker(FakeWorker):
    """결과를 시험이 원하는 시점에 보내는 인식기 (늦게 온 결과 재현용)."""
    def start(self):
        pass


# ---------- M-1: Ctrl+S ----------
def test_ctrl_s_saves_focused_text(win, qtbot):
    win.src_text.setFocus()
    qtbot.waitUntil(win.src_text.hasFocus, timeout=2000)
    win.src_text.setPlainText("원본 문장")
    qtbot.keyClick(win.src_text, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert paths.read_text(win.folders.full_dir / "a_0001.stt.txt") == "원본 문장"
    win.wave.set_selection((1.0, 2.0))
    p = make_pair(win, qtbot)
    win.out_text.setFocus()
    qtbot.waitUntil(win.out_text.hasFocus, timeout=2000)
    win.out_text.setPlainText("고친 문장")
    qtbot.keyClick(win.out_text, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert paths.read_text(p.txt) == "고친 문장"


# ---------- M-2: 늦게 온 인식 결과 ----------
def test_late_rerun_result_after_navigation(win, qtbot):
    """④ 다시 인식 중 다른 파일로 갔다 와도 같은 쌍(인식 중)이 유지되고, 늦게 온 결과가 저장된다."""
    win.wave.set_selection((1.0, 2.0))
    p = make_pair(win, qtbot)
    win.stt_factory = ManualWorker
    win.run_out_stt()
    w = FakeWorker.calls[-1]
    win.load_index(1, ask=False)
    win.load_index(0, ask=False)
    assert win.pairs[-1] is p and p.state == "recognizing" and not win.out_text.isEnabled()
    w._finish()
    assert paths.read_text(p.txt) == "인식된 문장입니다" and win.out_text.toPlainText() == "인식된 문장입니다"


def test_late_result_after_delete_creates_nothing(win, qtbot):
    win.wave.set_selection((1.0, 2.0))
    p = make_pair(win, qtbot)
    win.stt_factory = ManualWorker
    win.run_out_stt()
    w = FakeWorker.calls[-1]
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes
    win.delete_pair()
    w._finish()
    assert not p.txt.exists() and not p.wav.exists()
    assert "1" not in paths.load_meta(win.folders, "a_0001")["parts"]   # 구간 기록도 삭제


def test_cannot_save_out_while_recognizing(win, qtbot):
    win.wave.set_selection((1.0, 2.0))
    p = make_pair(win, qtbot)
    win.stt_factory = ManualWorker
    win.run_out_stt()
    assert not win.save_text("out")
    assert paths.read_text(p.txt) == ""


# ---------- M-3 / NFR-04: 작업 스레드 ----------
def test_create_runs_in_worker_thread(win, qtbot):
    win.wave.set_selection((1.0, 3.0))
    win.create_pair()
    assert win._creating and not win.btn_create.isEnabled()   # 바로 돌아오고 버튼은 잠김
    qtbot.waitUntil(lambda: not win._creating, timeout=5000)
    assert win.btn_create.isEnabled() and len(win.pairs) == 1


def test_stt_worker_runs_off_gui_thread(qtbot):
    seen = {}

    def fake(pcm, cfg, model, tr_id, on_progress=None, stop=None):
        seen["thread"] = threading.get_ident()
        return {"transcript": "됨", "words": [{"word": "됨", "start": 0.1, "end": 0.2}]}
    w = SttWorker(b"", {}, "m", transcribe=fake)
    got = []
    w.result.connect(lambda wk, r, e: got.append(r))
    w.start()
    qtbot.waitUntil(lambda: bool(got), timeout=3000)
    w.wait()
    assert got[0]["transcript"] == "됨" and got[0]["words"][0]["word"] == "됨"
    assert seen["thread"] != threading.get_ident()


# ---------- M-4 / NFR-07: 1440×900 ----------
def test_fits_1440x900(win, qtbot):
    assert win.minimumWidth() <= 1440 and win.minimumHeight() <= 900 - 48 - 40  # 작업 표시줄·제목줄 제외
    win.resize(1440, 812)
    qtbot.wait(50)
    assert win.width() <= 1440 and win.height() <= 812


# ---------- 경미 결함 ----------
def test_unreadable_file_clears_panels(win, qtbot):
    win.src_text.setPlainText("이전 파일 문장")
    win.src_dirty = False
    bad = win.folders.src + "\\c_bad.wav"
    open(bad, "wb").write(b"not wav")
    win.open_folders(win.folders)
    i = [p.name for p in win.sources].index("c_bad.wav")
    win.load_index(i, ask=False)
    assert win.audio is None and win.src_text.toPlainText() == "" and not win.src_dirty
    assert win.file_list.item(i).text().startswith("✕")
    win.load_index(0, ask=False)
    win.step_file(-1)            # 앞으로 가면 b(40초) → c(읽기 불가)는 건너뜀
    assert win.sources[win.cur].name != "c_bad.wav"


def test_mark_start_at_end_keeps_selection(win):
    win.wave.set_playhead(win.wave.duration)
    win.mark_start()
    assert win.wave.sel and abs(win.wave.sel[1] - win.wave.duration) < 1e-6 and win.wave.sel[1] - win.wave.sel[0] > 0.5


def test_invalid_spin_reverts(win):
    win.wave.set_selection((1.0, 2.0))
    win.sp_start.setValue(3.0)
    win._spin_changed()
    assert win.wave.sel == (1.0, 2.0) and abs(win.sp_start.value() - 1.0) < 1e-6
    assert "끝은 시작보다" in win.st_msg.text()


def test_src_text_readonly_while_recognizing(win, qtbot):
    win.stt_factory = ManualWorker
    win.run_src_stt(False)
    assert win.src_text.isReadOnly()
    win.stop_src_stt()
    assert not win.src_text.isReadOnly()


# ---------- FR-05: 파형 보기 ----------
def test_waveform_zoom_and_overview(win, qtbot):
    v = win.wave
    v.zoom_at(3.0, 0.5)
    a, b = v.view
    assert abs((b - a) - 3.0) < 1e-6 and a <= 3.0 <= b
    v.zoom_all()
    assert v.view == (0.0, 6.0)
    v.set_view(0, 1)
    ov = [w for w in win.findChildren(type(v).__mro__[0].__base__) if type(w).__name__ == "OverviewBar"][0]
    qtbot.mouseClick(ov, Qt.MouseButton.LeftButton, pos=ov.rect().center())
    assert v.view[0] > 1.0                                       # 띠 가운데를 누르면 보기가 가운데로
    qtbot.mouseClick(v, Qt.MouseButton.LeftButton, pos=v.rect().center())
    assert v.playhead is not None and win.lcd.text() != "00:00:00.0"


# ---------- FR-06: 재생 ----------
def test_stream_resamples_on_demand():
    x = np.linspace(-0.5, 0.5, 8000, dtype=np.float32)[:, None]      # 1초 · 8kHz · 모노
    s = _Stream(x, 8000, 48000, 2, QAudioFormat.SampleFormat.Int16)
    assert s.total_out == 48000
    chunk = s.render(4800)
    assert len(chunk) == 4800 * 2 * 2                                 # 0.1초 · 스테레오 · 16비트
    y = np.frombuffer(chunk, "<i2").reshape(-1, 2)
    assert np.all(y[:, 0] == y[:, 1]) and y[0, 0] < y[-1, 0]
    s.render(10 ** 6)
    assert s.render(10) == b""                                         # 끝까지 내보내면 빈 값


def test_play_toggle_uses_playhead(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win.player, "play", lambda tag, x, rate, start=0.0, end=None: calls.append((tag, start, end)) or True)
    monkeypatch.setattr(win.player, "is_playing", lambda tag=None: False)
    win.wave.set_playhead(2.5)
    win.toggle_src_play()
    assert calls[-1][:2] == ("src", 2.5) and "일시정지" in win.btn_src_play.text()
    win._on_play_stop("src", 3.1)                                      # 일시정지: 위치 유지
    assert abs(win.wave.playhead - 3.1) < 1e-6 and win.btn_src_play.text() == "▶ 원본 재생"
    win.wave.set_selection((1.0, 2.0))
    win.play_selection()
    assert calls[-1] == ("sel", 1.0, 2.0)


# ---------- FR-17 · FR-21 · NFR-06 ----------
def test_copy_from_src(win, qtbot):
    win.wave.set_selection((1.0, 2.0))
    make_pair(win, qtbot)
    win.src_text.setPlainText("원본 전체 문장")
    win.copy_from_src()
    assert win.out_text.toPlainText() == "원본 전체 문장" and win.out_dirty


def test_help_dialog_lists_shortcuts(qtbot):
    d = HelpDialog()
    qtbot.addWidget(d)
    keys = [k for k, _ in SHORTCUTS]
    assert "F1" in keys and "Ctrl+Enter" in keys and "Space" in keys and len(SHORTCUTS) >= 15


def test_krds_style_and_badge_symbols(qtbot):
    css = theme.stylesheet("Malgun Gothic", "Consolas")
    assert "#256ef4" in css and "border-radius: 6px" in css and "border-radius: 10px" in css
    from PySide6.QtWidgets import QLabel
    lab = QLabel()
    qtbot.addWidget(lab)
    for kind, sym in (("ok", "✓"), ("warn", "●"), ("edit", "✎"), ("err", "✕")):
        set_badge(lab, kind, "상태")
        assert lab.text().startswith(sym)                              # 색만으로 구분하지 않음


# ---------- 2026-10-23 사용자 수정 요청 ----------
def test_out_save_button_enabled_after_copy_or_edit(win, qtbot):
    src_stt(win, qtbot)
    win.wave.set_selection((1.0, 2.0))
    p = make_pair(win, qtbot)
    assert not win.btn_out_save.isEnabled()                        # 선택 구간 문장은 이미 자동 저장됨
    win.copy_from_src()                                            # 문장 전체 가져오기
    assert win.btn_out_save.isEnabled()
    win.btn_out_save.click()
    assert paths.read_text(p.txt) == "인식된 문장입니다" and not win.btn_out_save.isEnabled()
    win.copy_part_from_src()                                       # 선택 구간 문장 가져오기
    assert win.out_text.toPlainText() == "w2 w3" and win.btn_out_save.isEnabled()
    win.btn_out_save.click()
    assert paths.read_text(p.txt) == "w2 w3"
    win.out_text.setPlainText("다시 고침")
    assert win.btn_out_save.isEnabled()
    win.btn_out_save.click()
    assert paths.read_text(p.txt) == "다시 고침"


def test_selection_only_stt_not_auto_saved(win, qtbot):
    full = win.folders.full_dir / "a_0001.stt.txt"
    win.wave.set_selection((1.0, 2.0))
    win.run_src_stt(True)
    qtbot.waitUntil(lambda: win.src_worker is None, timeout=3000)
    assert not full.exists() and win.btn_src_save.isEnabled()


def test_spin_boxes_show_full_number(win, qtbot):
    """선택 구간 숫자 칸에 0.000 형식 숫자가 가려지지 않고 모두 보인다."""
    win.load_index(1, ask=False)                     # 40초 파일
    win.wave.set_selection((12.345, 23.456))
    qtbot.wait(20)
    for s, txt in ((win.sp_start, "12.345"), (win.sp_end, "23.456")):
        assert s.text() == txt
        need = s.fontMetrics().horizontalAdvance(txt)
        m = s.textMargins()
        assert s.contentsRect().width() - m.left() - m.right() > need + 8


def test_settings_dialog_standalone(win, qtbot):
    """음성인식 서버 설정은 WaveText 설정만 읽고 쓴다. 다른 프로그램 값 불러오기 기능은 없다 (FR-20)."""
    from PySide6.QtWidgets import QPushButton
    from wavetext.ui.dialogs import SettingsDialog
    d = SettingsDialog(win.cfg, win)
    qtbot.addWidget(d)
    assert not [b for b in d.findChildren(QPushButton) if "불러오기" in b.text()]
    assert d.host.text() == "127.0.0.1" and d.port.text() == "1" and d.scheme.currentText() == "ws"
    assert "저장됨" in d.keyinfo.text() and "KEY12345" not in d.keyinfo.text()
    assert d.key.echoMode() == d.key.EchoMode.Password and d.key.text() == ""
    d.host.setText("10.1.2.3"); d.port.setText("9090"); d.scheme.setCurrentText("wss")
    v = d.values()
    assert v["stt"]["server"] == {"scheme": "wss", "host": "10.1.2.3", "port": 9090}
    assert v["stt"]["api_key"] == "KEY12345"                        # 입력하지 않으면 기존 키 유지
    d.key.setText("NEWKEY999999")
    assert d.values()["stt"]["api_key"] == "NEWKEY999999"
    win.cfg.update(d.values())
    win._load_stt_label()
    assert "wss://10.1.2.3:9090" in win.lbl_server.text()
    d.host.clear()
    d.accept()
    assert d.result() == 0 and "서버 주소" in d.status.text()      # 서버 주소 없으면 저장 안 됨


def test_no_api_key_shows_guidance(win):
    win.cfg["stt"]["api_key"] = ""
    win._load_stt_label()
    assert "API 키 미설정" in win.lbl_server.text()
    win.run_src_stt(False)
    assert win.src_worker is None and "API 키" in win.st_msg.text()


def test_create_takes_selected_part_text_without_restt(win, qtbot):
    """생성 시 분할 음성을 다시 인식하지 않고 ② 단어 시간으로 선택 구간 문장만 가져와 자동 저장한다."""
    src_stt(win, qtbot)
    n = len(FakeWorker.calls)
    win.wave.set_selection((2.4, 4.6))                             # w5(2.5~2.8) ~ w8(4.0~4.3)
    p = make_pair(win, qtbot)
    assert len(FakeWorker.calls) == n
    assert win.out_text.toPlainText() == "w5 w6 w7 w8" and paths.read_text(p.txt) == "w5 w6 w7 w8"
    assert not win.btn_out_save.isEnabled()


def test_part_button_without_source_stt_explains(win, qtbot):
    win.wave.set_selection((1.0, 2.0))
    make_pair(win, qtbot)
    win.copy_part_from_src()
    assert "원본 음성인식을 먼저" in win.st_msg.text() and win.out_text.toPlainText() == ""


def test_out_rerun_overwrites_or_keeps_on_failure(win, qtbot):
    src_stt(win, qtbot)
    win.wave.set_selection((1.0, 2.0))
    p = make_pair(win, qtbot)
    assert paths.read_text(p.txt) == "w2 w3"
    FakeWorker.reply = ("다시 인식한 문장", "")
    win.btn_out_recog.click()                                      # 분할 음성 다시 인식
    assert p.state == "recognizing" and not win.out_text.isEnabled()
    assert len(FakeWorker.calls[-1].pcm) == 8000 * 2               # 1초 분할 음성 전송
    qtbot.waitUntil(lambda: p.state == "ok", timeout=3000)
    assert paths.read_text(p.txt) == "다시 인식한 문장" and win.out_text.toPlainText() == "다시 인식한 문장"
    FakeWorker.reply = (None, "연결 실패")
    win.run_out_stt()
    qtbot.waitUntil(lambda: p.state != "recognizing", timeout=3000)
    assert paths.read_text(p.txt) == "다시 인식한 문장" and p.state == "ok"   # 실패하면 기존 유지
    assert "기존 텍스트를 유지" in win.st_msg.text()
    FakeWorker.reply = ("인식된 문장입니다", "")


def test_rerun_works_for_reopened_pair(win, qtbot):
    win.wave.set_selection((1.0, 2.0))
    p = make_pair(win, qtbot)
    win.load_index(1, ask=False)
    win.load_index(0, ask=False)
    q = win.pairs[-1]
    assert q is not p and len(q.after) == 8000                       # 다시 연 쌍은 Wav에서 읽음
    FakeWorker.reply = ("다시 연 파일 인식", "")
    win.run_out_stt()
    qtbot.waitUntil(lambda: q.state == "ok", timeout=3000)
    assert paths.read_text(q.txt) == "다시 연 파일 인식"
    FakeWorker.reply = ("인식된 문장입니다", "")


def test_no_other_product_names_on_screen(win):
    from PySide6.QtWidgets import QAbstractButton, QLabel
    texts = [win.src_text.placeholderText(), win.out_text.placeholderText()]
    texts += [w.text() + w.toolTip() for w in win.findChildren(QLabel) + win.findChildren(QAbstractButton)]
    texts += [a.text() for m in (x.menu() for x in win.menuBar().actions()) if m for a in m.actions()]
    assert not [t for t in texts if " App" in t]


def test_folder_setting_only_in_file_menu(win):
    menus = {m.title(): [a.text() for a in m.actions()] for m in (a.menu() for a in win.menuBar().actions()) if m}
    assert "저장 폴더 설정…" in menus["파일(&F)"]
    assert menus["설정(&O)"] == ["음성인식 서버 설정…"]


def test_badge_cleared_after_entering_api_key(win, monkeypatch):
    """API 키 없이 인식을 눌러 '✕ 서버 미설정'이 붙은 뒤, 설정에서 키를 저장하면 배지가 사라진다."""
    from wavetext.ui import dialogs
    win.cfg["stt"]["api_key"] = ""
    win._load_stt_label()
    win.run_src_stt(False)
    assert "서버 미설정" in win.src_badge.text()

    class Dlg:
        def __init__(self, cfg, parent=None):
            pass

        def exec(self):
            return True

        def values(self):
            return {"stt": {"server": {"scheme": "ws", "host": "127.0.0.1", "port": 1}, "api_key": "NEWKEY123456"}, "model": "model_v2"}
    monkeypatch.setattr("wavetext.ui.main_window.SettingsDialog", Dlg)
    win.open_settings()
    assert "서버 미설정" not in win.src_badge.text() and "미실행" in win.src_badge.text()
    assert "미설정" not in win.lbl_server.text() and "ws://127.0.0.1:1" in win.lbl_server.text()
