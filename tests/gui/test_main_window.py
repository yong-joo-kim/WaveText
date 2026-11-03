"""화면 흐름 시험: 폴더 열기 → 구간 선택 → 학습용 파일 생성 → 자동 인식 → 텍스트 수정·저장 → 삭제."""
import json

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from wavetext import config, paths
from wavetext.audio import wavio
from wavetext.paths import Folders
from wavetext.ui import dialogs
from wavetext.ui.main_window import MainWindow


def src_stt(win, qtbot):
    """흐름 1단계: ② 원본 음성인식 실행 → 결과·단어 시간 자동 저장."""
    win.run_src_stt(False)
    qtbot.waitUntil(lambda: win.src_worker is None, timeout=3000)


def make_pair(win, qtbot):
    """학습용 파일 생성은 작업 스레드에서 진행되므로 쌍이 추가될 때까지 기다린다."""
    before = len(win.pairs)
    win.create_pair()
    qtbot.waitUntil(lambda: len(win.pairs) > before and not win._creating, timeout=5000)
    return win.pairs[-1]


class FakeWorker(QObject):
    """SttWorker 대역: 서버 없이 정해진 결과(문장 + 단어 시간)를 돌려준다."""
    progressed = Signal(object, float)
    result = Signal(object, object, str)
    reply = ("인식된 문장입니다", "")
    # 0.5초마다 단어 하나: w0(0.0~0.3) w1(0.5~0.8) ... w11(5.5~5.8)
    words = [{"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.3} for i in range(12)]
    calls = []

    def __init__(self, pcm, cfg, model, parent=None):
        super().__init__(parent)
        self.pcm, self.cfg, self.model, self.tag = pcm, cfg, model, None
        self._cancel = False
        FakeWorker.calls.append(self)

    def start(self):
        QTimer.singleShot(20, self._finish)

    def _finish(self):
        self.progressed.emit(self, 1.0)
        text, err = FakeWorker.reply
        if self._cancel:
            text, err = None, "사용자 중지"
        self.result.emit(self, None if text is None else {"transcript": text, "words": list(FakeWorker.words)}, err)

    def cancel(self):
        self._cancel = True

    def wait(self, ms=0):
        return True


@pytest.fixture
def win(qtbot, tmp_path, monkeypatch, speech_wav):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "wavetext.json")
    src = tmp_path / "녹취"
    speech_wav("a_0001.wav", dur=6.0, folder=src)
    speech_wav("b_0002.wav", dur=40.0, folder=src)
    FakeWorker.reply = ("인식된 문장입니다", "")
    FakeWorker.calls = []
    cfg = config.load()
    cfg.update({"stt": {"server": {"scheme": "ws", "host": "127.0.0.1", "port": 1}, "api_key": "KEY12345"},
                "folders": {"src": str(src), "full": "", "wav": "", "part_txt": ""}})
    w = MainWindow(cfg, stt_factory=FakeWorker)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.audio is not None, timeout=3000)
    # pytest-qt가 픽스처 정리보다 먼저 창을 닫는다. 저장 확인 창(모달)이 떠서 멈추지 않도록 기본 응답을 '버리기'로 둔다.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    return w


def test_opens_first_unprocessed_and_status(win):
    assert win.sources[0].name == "a_0001.wav" and win.cur == 0
    assert win.st_fmt.text() == "6.000초 · Wave PCM signed 16 bit, 8000 Hz, 128 kbps, mono"
    assert "사용자 분할 음성 Wav →" in win.st_prog.text() and "원본 음성인식 Text →" in win.st_prog.text()
    assert "자동 분할 음성 Wav →" in win.st_prog.text()
    assert not win.btn_create.isEnabled()


def test_create_pair_writes_wav_and_txt(win, qtbot):
    """흐름: 원본 인식 → 구간 선택·생성 → 분할 Wav 생성, 선택 구간 문장 자동 출력·저장(다시 인식 안 함)."""
    src_stt(win, qtbot)
    meta = paths.load_meta(win.folders, "a_0001")
    assert len(meta["words"]) == 12                                   # 단어 시간 자동 저장
    assert FakeWorker.calls[-1].cfg["result"]["word_alignment"] is True
    n_calls = len(FakeWorker.calls)
    win.wave.set_selection((1.0, 3.5))
    assert win.btn_create.isEnabled()
    p = make_pair(win, qtbot)
    f = win.folders
    wav = f.wav_dir / "a_0001.part-1.wav"
    txt = f.part_txt_dir / "a_0001.stt-1.txt"
    info = wavio.read_info(wav)
    assert (info.rate, info.channels, info.sampwidth) == (8000, 1, 2)
    assert abs(info.duration - 2.5) < 1e-3
    assert len(FakeWorker.calls) == n_calls                           # 분할 음성을 다시 인식하지 않음
    assert txt.read_text(encoding="utf-8") == "w2 w3 w4 w5 w6"        # 1.0~3.5초 단어만
    assert win.out_text.toPlainText() == "w2 w3 w4 w5 w6" and win.out_text.isEnabled()
    assert "저장됨" in win.out_badge.text() and p.state == "ok"
    assert paths.load_meta(f, "a_0001")["parts"] == {"1": [1.0, 3.5]}  # 분할 구간 기록
    win.wave.set_selection((4.0, 5.0))
    make_pair(win, qtbot)
    assert (f.wav_dir / "a_0001.part-2.wav").exists()
    assert paths.read_text(f.part_txt_dir / "a_0001.stt-2.txt") == "w8 w9"
    assert len(win.pairs) == 2 and win.cur_pair == 1
    assert win.counts["a_0001"] == 2
    assert win.file_list.item(0).text().startswith("✔")


def test_selection_over_30s_blocked(win):
    win.load_index(1, ask=False)
    win.wave.set_selection((0.0, 30.5))
    assert not win.btn_create.isEnabled()
    assert "30초 초과" in win.lbl_len.text()
    win.create_pair()
    assert not win.folders.wav_dir.exists() or not any(win.folders.wav_dir.iterdir())
    win.wave.set_selection((0.0, 30.0))
    assert win.btn_create.isEnabled()


def test_no_source_stt_writes_empty_text_and_allows_edit(win, qtbot):
    """원본 인식 없이 생성하면 빈 stt-N.txt를 만들고 안내한다. 직접 입력해 저장할 수 있다."""
    win.wave.set_selection((1.0, 2.0))
    make_pair(win, qtbot)
    txt = win.folders.part_txt_dir / "a_0001.stt-1.txt"
    assert txt.read_text(encoding="utf-8") == ""
    assert "문장 없음" in win.out_badge.text() and win.out_text.isEnabled()
    assert "원본 음성인식" in win.st_msg.text()
    win.out_text.setPlainText("직접 입력한 문장")
    assert win.out_dirty and "수정됨" in win.out_badge.text()
    win.save_text("out")
    assert txt.read_text(encoding="utf-8") == "직접 입력한 문장"
    assert not win.out_dirty


def test_source_stt_and_save_full_text(win, qtbot):
    win.run_src_stt(False)
    qtbot.waitUntil(lambda: win.src_worker is None, timeout=3000)
    assert win.src_text.toPlainText() == "인식된 문장입니다"
    assert len(FakeWorker.calls[-1].pcm) == 6 * 16000  # 6초 · 8kHz · 16비트
    p = win.folders.full_dir / "a_0001.stt.txt"
    assert p.read_text(encoding="utf-8") == "인식된 문장입니다"   # 인식 결과 자동 저장
    assert not win.src_dirty and not win.btn_src_save.isEnabled() and "자동 저장" in win.src_badge.text()
    win.src_text.setPlainText("고친 문장")                       # 편집하면 [저장] 활성화
    assert win.btn_src_save.isEnabled()
    win.btn_src_save.click()
    assert p.read_text(encoding="utf-8") == "고친 문장" and not win.btn_src_save.isEnabled()
    FakeWorker.reply = ("다시 인식", "")
    win.run_src_stt(False)                                         # 다시 인식하면 기존 파일 덮어쓰기
    qtbot.waitUntil(lambda: win.src_worker is None, timeout=3000)
    assert p.read_text(encoding="utf-8") == "다시 인식"
    FakeWorker.reply = ("인식된 문장입니다", "")
    win.run_src_stt(False)
    qtbot.waitUntil(lambda: win.src_worker is None, timeout=3000)
    # 다시 열면 저장된 결과를 불러온다
    win.load_index(1, ask=False)
    win.load_index(0, ask=False)
    assert win.src_text.toPlainText() == "인식된 문장입니다" and "저장됨" in win.src_badge.text()


def test_existing_pairs_loaded_on_reopen(win, qtbot):
    """다시 열어도 분할 구간 기록으로 구간 표시·원음 비교가 된다."""
    src_stt(win, qtbot)
    win.wave.set_selection((1.0, 2.0))
    make_pair(win, qtbot)
    win.load_index(1, ask=False)
    win.load_index(0, ask=False)
    q = win.pairs[0]
    assert len(win.pairs) == 1 and q.n == 1 and (q.t0, q.t1) == (1.0, 2.0)
    assert q.before is not None and win.tg_before.isEnabled()
    assert win.out_text.toPlainText() == "w2 w3"
    assert "part-1  1.000~2.000" in [b.text() for b in win.chip_group.buttons()]


def test_delete_pair(win, qtbot, monkeypatch):
    win.wave.set_selection((1.0, 2.0))
    make_pair(win, qtbot)
    txt = win.folders.part_txt_dir / "a_0001.stt-1.txt"
    qtbot.waitUntil(lambda: txt.exists(), timeout=3000)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.delete_pair()
    assert not txt.exists() and not (win.folders.wav_dir / "a_0001.part-1.wav").exists()
    assert win.pairs == [] and win.counts["a_0001"] == 0


def test_toggle_before_after_switches_wave(win, qtbot):
    win.nr.setCurrentIndex(3)
    win.wave.set_selection((0.2, 2.0))  # 앞부분은 잡음만 있음
    make_pair(win, qtbot)
    p = win.pairs[-1]
    assert p.before is not None and p.nr == 3
    after_peak = abs(win.out_wave.mono[:2000]).max()
    win.tg_before.click()
    assert win.show_before
    before_peak = abs(win.out_wave.mono[:2000]).max()
    assert before_peak > after_peak  # 원음에는 잡음이 더 크다


def test_unsaved_prompt_cancel_keeps_file(win, monkeypatch):
    win.src_text.setPlainText("수정")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    win.step_file(1)
    assert win.cur == 0
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    win.step_file(1)
    assert win.cur == 1


def test_folder_dialog_requires_source(qtbot, tmp_path):
    dlg = dialogs.FolderDialog(Folders(), None)
    qtbot.addWidget(dlg)
    dlg.accept()
    assert dlg.result() == 0 and "필수" in dlg.src_hint.text()
    dlg.src.setText(str(tmp_path))
    assert list(dlg.edits) == ["full", "wav", "part_txt", "auto_wav", "auto_txt"]
    assert dlg.edits["wav"].placeholderText() == f"비우면: {tmp_path}\\manual-part-wav"
    assert dlg.edits["auto_txt"].placeholderText() == f"비우면: {tmp_path}\\auto-part-stt"
    dlg.edits["wav"].setText(str(tmp_path / "W"))
    dlg.edits["auto_wav"].setText(str(tmp_path / "AW"))
    dlg.accept()
    assert dlg.result() == 1
    f = dlg.folders()
    assert f.wav_dir == tmp_path / "W" and f.part_txt_dir == tmp_path / "manual-part-stt" and f.full_dir == tmp_path / "full-stt"
    assert f.auto_wav_dir == tmp_path / "AW" and f.auto_txt_dir == tmp_path / "auto-part-stt"


def test_drawer_filter(win, qtbot):
    win.wave.set_selection((1.0, 2.0))
    make_pair(win, qtbot)
    win.toggle_drawer()
    assert win.drawer.isVisible()
    win.flt_state.setCurrentIndex(1)  # 미처리
    assert win.file_list.item(0).isHidden() and not win.file_list.item(1).isHidden()
    win.flt_state.setCurrentIndex(0)
    win.flt_text.setText("b_")
    assert win.file_list.item(0).isHidden()
    assert "전체 2개 · 생성 완료 1개" in win.drawer_sum.text()


def test_wave_click_syncs_src_text(win, qtbot):
    """① 파형을 누르면 ② 인식 결과에서 그 시각의 단어를 강조하고 글자 커서를 옮긴다."""
    FakeWorker.reply = (" ".join(w["word"] for w in FakeWorker.words), "")
    src_stt(win, qtbot)
    text = win.src_text.toPlainText()
    win.wave.playheadMoved.emit(2.6)                       # w5(2.5~2.8)
    sel = win.src_text.extraSelections()
    assert len(sel) == 1 and sel[0].cursor.selectedText() == "w5"
    assert win.src_text.textCursor().position() == text.index("w5")
    assert win.lcd.text()
    win.src_text.insertPlainText("x")                     # 글을 고치면 강조를 지우고 다시 찾는다
    assert win.src_text.extraSelections() == []
    assert win.sync_src_text(0.1) == 0
    win.load_index(1, ask=False)                           # 단어 시간이 없는 파일에서는 아무것도 하지 않음
    assert win.sync_src_text(1.0) is None


def test_part_text_from_selection_only_stt(win, qtbot):
    """원본 전체가 아니라 ② 선택 구간만 인식해도 ④ [선택 구간 문장 가져오기]가 원본 시각에 맞춰 동작한다."""
    win.wave.set_selection((2.5, 4.0))
    p = make_pair(win, qtbot)                               # 원본 인식 전 생성 → 문장 없음
    assert p.state == "err" and p.text == ""
    win.wave.set_selection((2.0, 5.0))
    win.run_src_stt(True)                                   # 선택 구간 인식: 단어 시간은 2.0초 기준
    qtbot.waitUntil(lambda: win.src_worker is None, timeout=3000)
    assert paths.load_meta(win.folders, "a_0001")["words"] == []   # 전체 인식 기록은 그대로
    win.copy_part_from_src()
    # w_i 가운데 = 2.0 + 0.5i + 0.15 → 2.5~4.0초 안: w1 w2 w3
    assert win.out_text.toPlainText() == "w1 w2 w3"
    win.wave.set_selection((3.0, 4.5))
    p2 = make_pair(win, qtbot)                              # 생성할 때도 같은 단어 시간으로 자동 저장
    assert p2.state == "ok" and paths.read_text(p2.txt) == "w2 w3 w4"


def test_selection_length_resets_on_file_change(win):
    """다른 파일로 넘어가면 선택 구간 시작·끝과 함께 길이 표시도 비운다."""
    win.wave.set_selection((1.0, 3.5))
    assert win.lbl_len.text().startswith("길이 2.500초")
    win.load_index(1, ask=False)
    assert win.wave.sel is None
    assert (win.sp_start.value(), win.sp_end.value()) == (0, 0)
    assert win.lbl_len.text() == "길이 - (최대 30초)"
    assert not win.btn_create.isEnabled()


def test_delete_icon_next_to_play(win, qtbot, monkeypatch):
    """③ 재생 단추 옆 🗑 단추로 선택한 쌍을 확인 후 삭제한다. 쌍이 없으면 꺼진다."""
    assert win.btn_out_del.text() == "🗑 삭제" and not win.btn_out_del.isEnabled()
    lay = win.btn_out_play.parentWidget().layout()
    assert lay.indexOf(win.btn_out_del) == lay.indexOf(win.btn_out_play) + 1   # 재생 단추 바로 옆
    win.wave.set_selection((1.0, 2.0))
    make_pair(win, qtbot)
    wav = win.folders.wav_dir / "a_0001.part-1.wav"
    assert win.btn_out_del.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    win.btn_out_del.click()
    assert wav.exists() and len(win.pairs) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.btn_out_del.click()
    assert not wav.exists() and win.pairs == []
    assert not win.btn_out_del.isEnabled()


def test_selection_stt_text_only_inside_recognized_range(win, qtbot):
    """V3-1: 선택 구간 인식 단어 시간은 인식 범위 안의 구간에만 쓴다. 벗어나면 문장 없음으로 둔다."""
    win.wave.set_selection((2.0, 3.0))
    win.run_src_stt(True)                                   # 2.0~3.0초만 인식 (단어 시간은 2.0초 기준)
    qtbot.waitUntil(lambda: win.src_worker is None, timeout=3000)
    win.wave.set_selection((2.0, 5.0))                      # 인식 범위를 벗어남
    p = make_pair(win, qtbot)
    assert p.state == "err" and p.text == "" and "범위" in p.error
    assert "가져오기 필요" in win.out_badge.text()
    win.copy_part_from_src()
    assert "범위" in win.st_msg.text() and win.out_text.toPlainText() == ""
    win.wave.set_selection((2.2, 2.9))                      # 범위 안: w1(2.5~2.8)
    p2 = make_pair(win, qtbot)
    assert p2.state == "ok" and p2.text == "w1"
    win.load_index(1, ask=False)                            # 다른 파일로 가면 선택 구간 인식 결과를 쓰지 않는다
    assert win._src_range is None and win._span_text(0.0, 1.0)[1] is not None


def test_folder_dialog_rejects_same_manual_and_auto_folder(qtbot, tmp_path):
    """V3-2: 사용자·자동 분할 폴더를 같게 지정하면 적용하지 않고 두 칸을 표시한다."""
    dlg = dialogs.FolderDialog(Folders(src=str(tmp_path)), None)
    qtbot.addWidget(dlg)
    dlg.edits["wav"].setText(str(tmp_path / "X"))
    dlg.edits["auto_wav"].setText(str(tmp_path / "X"))
    dlg.accept()
    assert dlg.result() == 0 and "폴더가 같습니다" in dlg.src_hint.text()
    assert dlg.edits["wav"].property("error") and dlg.edits["auto_wav"].property("error")
    assert not dlg.src.property("error")
    dlg.edits["auto_wav"].setText(str(tmp_path / "Y"))     # 고치면 표시를 지운다
    assert not dlg.edits["auto_wav"].property("error")
    dlg.accept()
    assert dlg.result() == 1


def test_delete_blocked_while_creating(win, qtbot, monkeypatch):
    """V3-5: 생성 중에는 Del·메뉴로도 삭제하지 않는다."""
    win.wave.set_selection((1.0, 2.0))
    make_pair(win, qtbot)
    wav = win.folders.wav_dir / "a_0001.part-1.wav"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._creating = True
    win.delete_pair()
    assert wav.exists() and len(win.pairs) == 1 and "만드는 중" in win.st_msg.text()
    win._creating = False
    win.delete_pair()
    assert not wav.exists()


def test_layout_shrinks_for_1360(win, qtbot):
    """C-7: 좁은 화면에서 ① 안내 글은 줄어들고, 구역 머리 줄 단추는 여백을 줄여 ④ 단추 글자가 잘리지 않게 한다.
    (offscreen은 실제 글꼴·테마가 없어 픽셀 폭을 비교할 수 없다. 실제 Windows 측정은 검증보고서 11절)"""
    from PySide6.QtWidgets import QLabel, QSizePolicy
    from wavetext.ui import theme
    hint = next(l for l in win.findChildren(QLabel) if l.text().startswith("끌어서 구간 선택"))
    assert hint.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored and hint.toolTip()
    head = win.btn_out_part.parentWidget()
    assert head.objectName() == "paneHead" and head.layout().spacing() <= 6
    assert "#paneHead QPushButton { padding: 0 7px; }" in theme.stylesheet("Malgun Gothic", "Consolas")
