"""화면 흐름 시험: 폴더 열기 → 구간 선택 → 학습용 파일 생성 → 자동 인식 → 텍스트 수정·저장 → 삭제."""
import json

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

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
    assert "분할 Wav →" in win.st_prog.text() and "원본 음성인식 Text →" in win.st_prog.text()
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
    assert dlg.edits["wav"].placeholderText() == f"비우면: {tmp_path}\\part-wav"
    dlg.edits["wav"].setText(str(tmp_path / "W"))
    dlg.accept()
    assert dlg.result() == 1
    f = dlg.folders()
    assert f.wav_dir == tmp_path / "W" and f.part_txt_dir == tmp_path / "part-stt" and f.full_dir == tmp_path / "full-stt"


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
