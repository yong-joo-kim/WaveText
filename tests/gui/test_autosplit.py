"""자동분리생성 화면 흐름: 인식 → 자동 분리 → 파형 클릭 ↔ 목록 → 문장 수정 → 선택 저장 → 다시 열기."""
import threading
import time

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from wavetext import paths
from wavetext.audio import wavio
from wavetext.ui import segments
from tests.gui.test_main_window import FakeWorker, make_pair, src_stt, win  # noqa: F401  (win은 픽스처)

REAL_QUESTION = QMessageBox.question  # 픽스처가 가짜로 바꾸기 전의 실제 확인 창

# 앞 발화 약 4초, 1.6초 묵음 뒤 한마디(짧음)
WORDS = ([{"word": f"앞{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(8)]
         + [{"word": "네", "start": 5.5, "end": 5.7}])


@pytest.fixture
def auto(win, qtbot, monkeypatch):  # noqa: F811
    monkeypatch.setattr(FakeWorker, "words", WORDS)
    FakeWorker.reply = (" ".join(w["word"] for w in WORDS), "")
    win.act_auto.trigger()
    return win


def split_done(win, qtbot):
    src_stt(win, qtbot)
    qtbot.waitUntil(lambda: win._auto_worker is None and bool(win.segs), timeout=5000)


def answer(monkeypatch, button):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: button)


def test_mode_switch_keeps_manual_panes(win, qtbot):  # noqa: F811
    assert not win.pane_auto.isVisible() and win.pane_out.isVisible()
    win.act_auto.trigger()
    assert win.pane_auto.isVisible() and not win.pane_out.isVisible() and not win.pane_txt.isVisible()
    assert not win.act_delete.isEnabled() and not win.act_out_stt.isEnabled()
    win.act_auto.trigger()
    assert not win.pane_auto.isVisible() and win.pane_out.isVisible()
    assert win.act_delete.isEnabled()


def test_delete_blocked_on_auto_screen(win, qtbot):  # noqa: F811
    src_stt(win, qtbot)
    win.wave.set_selection((1.0, 2.0))
    p = make_pair(win, qtbot)
    win.act_auto.trigger()
    win.delete_pair()
    win.create_pair()
    assert p.wav.exists() and len(win.pairs) == 1


def test_split_after_recognition(auto, qtbot):
    split_done(auto, qtbot)
    s = auto.segs
    assert [x.n for x in s] == [1, 2]
    assert s[0].selected and s[0].flag == ""
    assert s[1].flag == "short" and not s[1].selected and s[1].text == "네"
    assert auto.seg_table.rowCount() == 2 and auto.auto_stack.currentIndex() == 1
    assert "⚠ 짧음" in auto.seg_table.item(1, segments.COL_STATE).text()
    assert auto.seg_table.item(0, segments.COL_SAVE).checkState() == Qt.CheckState.Checked
    assert auto.seg_table.item(1, segments.COL_SAVE).checkState() == Qt.CheckState.Unchecked
    assert [b[2] for b in auto.wave.bands] == ["✓1", "⚠2"]
    assert all(x.audio is not None for x in s)
    assert paths.load_split(auto.folders, "a_0001")["segments"][1]["text"] == "네"


def test_wave_click_selects_row_and_row_moves_wave(auto, qtbot):
    split_done(auto, qtbot)
    auto.wave.playheadMoved.emit(5.6)
    assert auto.seg_table.currentRow() == 1 and auto.wave.cur_band == 1
    assert auto.src_text.extraSelections()[-1].cursor.selectedText() == "네"
    assert auto.seg_table.state() != auto.seg_table.State.EditingState   # 파형 클릭이 편집칸을 열지 않음
    auto.seg_table.setCurrentCell(0, segments.COL_LEN)
    assert auto.wave.cur_band == 0 and auto.wave.playhead == pytest.approx(auto.segs[0].t0)
    assert len(auto.src_text.extraSelections()) == 2                     # 구간 + 단어 강조


def test_edit_text_in_row(auto, qtbot):
    split_done(auto, qtbot)
    t = auto.seg_table
    t.edit_row(0)
    assert t.state() == t.State.EditingState
    t.indexWidget(t.currentIndex()).setText("고친 문장")
    auto._auto_flush()
    assert auto.segs[0].text == "고친 문장"
    assert paths.load_split(auto.folders, "a_0001")["segments"][0]["text"] == "고친 문장"


def test_save_selected_only(auto, qtbot, tmp_path):
    split_done(auto, qtbot)
    auto.segs[0].text = "  고친   문장 "
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    f = auto.folders
    assert paths.study_full_path(f, "a_0001").read_text(encoding="utf-8") == FakeWorker.reply[0]
    wav, txt = paths.study_wav_path(f, "a_0001", 1), paths.study_txt_path(f, "a_0001", 1)
    assert txt.read_text(encoding="utf-8") == "고친 문장"
    info = wavio.read_info(wav)
    assert (info.rate, info.channels) == (auto.audio.rate, auto.audio.info.channels)
    assert not paths.study_wav_path(f, "a_0001", 2).exists()
    assert auto.segs[0].saved_text == "고친 문장" and auto.segs[1].saved_text is None
    assert not (f.wav_dir / "a_0001.part-1.wav").exists()                # 수동 분할 폴더는 그대로
    auto.segs[0].text = "또 고침"
    assert segments.seg_state(auto.segs[0]) == "✎ 수정됨"


def test_uncheck_saved_pair_asks_then_deletes(auto, qtbot, monkeypatch):
    split_done(auto, qtbot)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    wav = paths.study_wav_path(auto.folders, "a_0001", 1)
    auto.seg_table.item(0, segments.COL_SAVE).setCheckState(Qt.CheckState.Unchecked)
    assert auto.segs[0].selected is False
    answer(monkeypatch, QMessageBox.StandardButton.No)
    auto.save_auto()
    assert wav.exists() and not auto._auto_saving
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    assert not wav.exists() and not paths.study_full_path(auto.folders, "a_0001").exists()


def test_empty_text_blocks_save(auto, qtbot):
    split_done(auto, qtbot)
    auto.segs[0].text = " "
    auto.save_auto()
    assert not auto._auto_saving and "비어 있습니다" in auto.st_msg.text()
    assert auto.seg_table.currentRow() == 0


def test_src_dirty_blocks_save(auto, qtbot):
    split_done(auto, qtbot)
    auto.src_text.insertPlainText("x")
    auto.save_auto()
    assert not auto._auto_saving and "먼저" in auto.st_msg.text()


def test_restore_after_reopen(auto, qtbot):
    split_done(auto, qtbot)
    auto.segs[0].text = "수정본"
    auto._on_seg_edited()
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    auto.load_index(1, ask=False)
    assert auto.segs == []
    auto.load_index(0, ask=False)
    assert [s.text for s in auto.segs] == ["수정본", "네"]
    assert auto.segs[0].saved_text == "수정본" and not auto.segs[1].selected
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert all(s.audio is not None for s in auto.segs)


def test_stale_record_ignored_when_length_differs(auto, qtbot):
    split_done(auto, qtbot)
    rec = paths.load_split(auto.folders, "a_0001")
    rec.pop("version")
    rec["duration"] = 99.0
    paths.save_split(auto.folders, "a_0001", rec)
    auto.load_index(1, ask=False)
    auto.load_index(0, ask=False)
    assert auto.segs == []


def test_resplit_needs_confirmation_and_removes_saved(auto, qtbot, monkeypatch):
    split_done(auto, qtbot)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    wav = paths.study_wav_path(auto.folders, "a_0001", 1)
    auto.segs[0].text = "수정본"
    answer(monkeypatch, QMessageBox.StandardButton.No)
    auto.run_auto_split()
    assert auto._auto_worker is None and auto.segs[0].text == "수정본" and wav.exists()
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.run_auto_split()
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert not wav.exists() and [s.n for s in auto.segs] == [1, 2] and auto.segs[0].saved_text is None


def test_late_result_after_file_change_is_dropped(auto, qtbot, monkeypatch):
    monkeypatch.setattr("wavetext.audio.autosplit.render", lambda *a: a[-1].wait(3) or True)  # 파일을 옮길 때까지 대기
    src_stt(auto, qtbot)
    w = auto._auto_worker
    assert w is not None
    auto.load_index(1, ask=False)
    qtbot.waitUntil(lambda: w not in auto._workers, timeout=5000)
    assert auto.segs == [] and auto.sources[auto.cur].name == "b_0002.wav"
    assert paths.load_split(auto.folders, "a_0001") is None


def test_select_all_toggles(auto, qtbot):
    split_done(auto, qtbot)
    auto.auto_select_all()
    assert all(s.selected for s in auto.segs)
    assert auto.seg_table.item(1, segments.COL_SAVE).checkState() == Qt.CheckState.Checked
    auto.auto_select_all()
    assert not any(s.selected for s in auto.segs)


def test_play_button_uses_saved_sound(auto, qtbot, monkeypatch):
    split_done(auto, qtbot)
    calls = []
    monkeypatch.setattr(auto.player, "play", lambda *a: calls.append(a) or True)
    auto.seg_table.select_row(1)
    auto.seg_table.playClicked.emit(1)
    tag, audio, rate = calls[-1]
    assert tag == "seg" and audio is auto.segs[1].audio and rate == auto.audio.rate
    assert auto.seg_table.item(1, segments.COL_PLAY).text() == "■"
    auto._on_play_pos("seg", 0.1)
    assert auto.wave.playhead == pytest.approx(auto.segs[1].t0 + 0.1)   # 구간 재생 위치를 원본 시간으로 표시
    auto._on_play_stop("seg", 0.2)
    assert auto.seg_table.item(1, segments.COL_PLAY).text() == "▶"
    auto.play_out()                                                      # Ctrl+Space도 현재 구간 재생
    assert len(calls) == 2


def test_ctrl_s_routes_to_auto_save(auto, qtbot):
    split_done(auto, qtbot)
    auto._save_shortcut()
    assert auto._auto_saving
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    auto.src_text.insertPlainText("x")                                   # ② 수정 중이면 ② 저장
    auto._save_shortcut()
    assert not auto.src_dirty and not auto._auto_saving


def test_menu_run_from_normal_screen_switches(win, qtbot, monkeypatch):  # noqa: F811
    monkeypatch.setattr(FakeWorker, "words", WORDS)
    src_stt(win, qtbot)
    assert not win.auto_mode
    win.run_auto_split()
    assert win.auto_mode and win.act_auto.isChecked() and win.pane_auto.isVisible()
    qtbot.waitUntil(lambda: win._auto_worker is None and bool(win.segs), timeout=5000)


def test_empty_screen_guides_next_step(auto, qtbot):
    assert "음성인식 실행" in auto.auto_empty.text() and auto.auto_stack.currentIndex() == 0
    assert not auto.btn_auto_save.isEnabled()


def test_recognition_never_opens_delete_prompt(auto, qtbot, monkeypatch):
    """AS-1: 저장본이 있는 파일을 다시 인식해도 확인 창 없이 이전 구간과 저장본을 둔다."""
    split_done(auto, qtbot)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.Yes)
    before = list(auto.segs)
    src_stt(auto, qtbot)
    assert asked == [] and auto.segs == before and auto._auto_worker is None
    assert paths.study_wav_path(auto.folders, "a_0001", 1).exists()
    assert "자동 분리 실행" in auto.st_msg.text()


def test_delete_prompts_default_to_no(auto, qtbot, monkeypatch):
    split_done(auto, qtbot)
    seen = []

    def spy(*a, **k):
        seen.append(a[-1])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", spy)
    auto.run_auto_split()
    assert seen == [QMessageBox.StandardButton.No]


def test_blocked_while_recognizing(auto, qtbot):
    """AS-2: 인식 중에는 저장·분리를 시작하지 않아 확인 창과 인식 결과가 겹치지 않는다."""
    split_done(auto, qtbot)
    auto.run_src_stt(False)
    auto.save_auto()
    assert not auto._auto_saving and "끝난 뒤" in auto.st_msg.text()
    auto.run_auto_split()
    assert auto._auto_worker is None
    qtbot.waitUntil(lambda: auto.src_worker is None, timeout=3000)


def test_failed_delete_keeps_old_list_and_shows_real_state(auto, qtbot, monkeypatch):
    """AS-3: 삭제가 중간에 실패하면 이전 구간을 두고 실제 남은 파일대로 표시한다."""
    split_done(auto, qtbot)
    auto.auto_select_all()
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    real = paths.study_remove

    def fail_second(folders, base, n):
        if n == 2:
            raise PermissionError("잠김")
        real(folders, base, n)

    monkeypatch.setattr(paths, "study_remove", fail_second)
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.run_auto_split()
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert [s.saved_text is not None for s in auto.segs] == [False, True]
    assert "삭제하지 못했습니다" in auto.st_msg.text()


def test_failed_split_keeps_noise_level(auto, qtbot, monkeypatch):
    """AS-7: 분리가 실패하면 기록된 노이즈 제거 강도를 바꾸지 않는다."""
    split_done(auto, qtbot)
    level = auto._auto_nr
    auto.nr.setCurrentIndex((level + 1) % 4)
    monkeypatch.setattr("wavetext.audio.autosplit.split", lambda *a: 1 / 0)
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.run_auto_split()
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert auto._auto_nr == level and len(auto.segs) == 2


def test_broken_record_duration_is_ignored(auto, qtbot):
    """AS-8"""
    split_done(auto, qtbot)
    paths.split_path(auto.folders, "a_0001").write_text('{"version": 1, "duration": null, "segments": []}', encoding="utf-8")
    auto.load_index(1, ask=False)
    auto.load_index(0, ask=False)
    assert auto.segs == []


def test_row_pick_marks_word_of_that_segment(auto, qtbot):
    """AS-9: 구간 시작이 앞 여유라도 그 구간의 첫 단어를 강조한다."""
    split_done(auto, qtbot)
    auto.seg_table.setCurrentCell(1, segments.COL_LEN)
    assert auto.src_text.extraSelections()[-1].cursor.selectedText() == "네"


def test_time_label_rounds_up_minute():
    assert segments.fmt_t(59.96) == "01:00.0"


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Space])
def test_real_dialog_enter_key_answers_no(win, qtbot, monkeypatch, key):  # noqa: F811
    """실제 확인 창: 초점이 [아니요]에 있어 Enter나 Space를 눌러도 삭제하지 않는다."""
    res = []

    def press():
        box = QApplication.activeModalWidget()
        res.append(box.defaultButton().text())
        QTest.keyClick(box.focusWidget(), key)

    monkeypatch.setattr(QMessageBox, "question", REAL_QUESTION)
    QTimer.singleShot(150, press)
    assert win._ask_no_default("다시 자동 분리", "시험") is False
    assert len(res) == 1


def test_new_segments_survive_moving_away_during_delete(auto, qtbot, monkeypatch):
    """AS-4: 저장본을 지우기 시작한 뒤 파일을 옮겨도 새 구간은 기록에 남아 돌아오면 보인다."""
    split_done(auto, qtbot)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    started = threading.Event()
    real = paths.study_remove

    def slow(*a):
        started.set()
        time.sleep(0.2)
        real(*a)

    monkeypatch.setattr(paths, "study_remove", slow)
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.run_auto_split()
    w = auto._auto_worker
    qtbot.waitUntil(started.is_set, timeout=3000)
    auto.load_index(1, ask=False)                                        # RV-4: 삭제·기록이 끝나야 넘어간다
    assert auto.seg_table.isEnabled()
    qtbot.waitUntil(lambda: w not in auto._workers, timeout=5000)
    auto.load_index(0, ask=False)
    assert [s.n for s in auto.segs] == [1, 2] and auto.segs[0].saved_text is None
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)


def test_table_locked_while_splitting_again(auto, qtbot, monkeypatch):
    """RV-1, RV-3: 다시 나누는 동안 표를 고칠 수 없어 기록을 두 스레드가 쓰지 않는다."""
    split_done(auto, qtbot)
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.run_auto_split()
    assert not auto.seg_table.isEnabled() and not auto.btn_auto_all.isEnabled()
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert auto.seg_table.isEnabled()


def test_record_failure_keeps_new_segments(auto, qtbot, monkeypatch):
    """RV-2: 기록을 못 써도 새 구간을 버리지 않고 알린다."""
    split_done(auto, qtbot)
    old = auto.segs
    monkeypatch.setattr(paths, "save_split", lambda *a: (_ for _ in ()).throw(OSError("잠김")))
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.run_auto_split()
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert auto.segs is not old and len(auto.segs) == 2


def test_rerecognition_marks_list_as_old(auto, qtbot):
    """RV-5: 다시 인식하면 표의 구간이 이전 결과 기준임을 배지로 알린다."""
    split_done(auto, qtbot)
    src_stt(auto, qtbot)
    assert "이전 인식 결과" in auto.auto_badge.text()
