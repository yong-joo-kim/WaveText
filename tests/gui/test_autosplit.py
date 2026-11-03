"""자동분할생성 화면 흐름: 인식 → 자동 분할 → 파형 클릭 ↔ 목록 → 문장 수정 → 선택 저장 → 다시 열기."""
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from wavetext import paths
from wavetext.audio import segment, wavio
from wavetext.stt import client
from wavetext.ui import segments, waveform
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


def test_menu_opens_modal_popup_and_keeps_main_screen(win, qtbot):  # noqa: F811
    assert not win.auto_dlg.isVisible()
    win.act_auto.trigger()
    assert win.auto_dlg.isVisible() and win.auto_dlg.isModal() and win.auto_dlg.windowTitle() == "자동분할생성"
    assert win.auto_wave.duration == win.wave.duration
    win.auto_dlg.reject()
    assert not win.auto_dlg.isVisible() and not win.auto_mode
    assert win.btn_create.isVisible() and win.out_text.isVisible()      # 메인 화면은 그대로


def test_popup_split_grows_evenly_and_user_can_move_it(auto, qtbot):
    d, sp = auto.auto_dlg, auto.auto_split
    d.resize(1280, 840)
    QApplication.processEvents()
    up, down = sp.sizes()
    assert abs(up - down) <= 1                                           # 처음엔 위·아래가 같다
    d.resize(1280, 1200)
    QApplication.processEvents()
    up2, down2 = sp.sizes()
    assert abs((up2 - up) - (down2 - down)) <= 1 and up2 > up            # 늘린 높이를 똑같이 나눈다
    sp.moveSplitter(up2 - 200, 1)                                        # 사용자가 경계를 위로 끈다
    assert sp.sizes()[0] == up2 - 200 and sp.sizes()[1] == down2 + 200
    assert auto.btn_auto_save.toolTip().startswith("선택한 구간 분할된 Wave 파일과 음성인식 Text 파일")


def test_menu_item_is_on_menu_bar(win):  # noqa: F811
    assert win.act_auto in win.menuBar().actions() and win.act_auto.menu() is None   # 항목을 더 고르지 않고 바로 열림


def test_popup_noise_and_pacing_follow_main(win, qtbot):  # noqa: F811
    win.pacing.setCurrentIndex(1)
    win.nr.setCurrentIndex(3)
    win.act_auto.trigger()
    assert win.auto_pacing.currentIndex() == 1 and win.auto_nr.currentIndex() == 3
    win.auto_pacing.setCurrentIndex(0)
    win.auto_nr.setCurrentIndex(1)
    assert win.pacing.currentIndex() == 0 and win.nr.currentIndex() == 1
    assert win.cfg["stt"]["realtime_pacing"] is True and win.cfg["nr_level"] == 1


def test_popup_file_choice_and_messages(auto, qtbot):
    assert auto.auto_file.count() == 2 and auto.auto_file.currentIndex() == 0
    auto.auto_file.setCurrentIndex(1)
    assert auto.sources[auto.cur].name == "b_0002.wav" and auto.auto_wave.duration == pytest.approx(40.0)
    auto.btn_auto_prev.click()
    assert auto.cur == 0 and auto.auto_file.currentIndex() == 0
    auto.message("안내")
    assert auto.auto_msg.text() == "안내"


def test_recognize_button_in_popup(auto, qtbot):
    auto.btn_auto_recog.click()
    assert auto.btn_auto_recog.text() == "인식 중지"
    qtbot.waitUntil(lambda: auto._auto_worker is None and bool(auto.segs), timeout=5000)
    assert auto.btn_auto_recog.text() == "음성인식 실행"
    assert auto.auto_text.toPlainText() == FakeWorker.reply[0]


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
    assert [b[2] for b in auto.auto_wave.bands] == ["✓1", "⚠2"]
    assert all(x.audio is not None for x in s)
    assert paths.load_split(auto.folders, "a_0001")["segments"][1]["text"] == "네"


def test_wave_click_selects_row_and_row_moves_wave(auto, qtbot):
    split_done(auto, qtbot)
    auto.auto_wave.playheadMoved.emit(5.6)
    assert auto.seg_table.currentRow() == 1 and auto.auto_wave.cur_band == 1
    assert auto.auto_text.extraSelections()[-1].cursor.selectedText() == "네"
    assert auto.seg_table.state() != auto.seg_table.State.EditingState   # 파형 클릭이 편집칸을 열지 않음
    auto.seg_table.setCurrentCell(0, segments.COL_LEN)
    assert auto.auto_wave.cur_band == 0 and auto.auto_wave.playhead == pytest.approx(auto.segs[0].t0)
    assert len(auto.auto_text.extraSelections()) == 2                     # 구간 + 단어 강조


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
    # 원본 인식 결과는 사용자 분할과 같은 full-stt의 txt·json 하나만 쓰고, 복사본 폴더는 만들지 않는다
    assert paths.full_stt_path(f, "a_0001").read_text(encoding="utf-8") == FakeWorker.reply[0]
    assert paths.meta_path(f, "a_0001").exists() and paths.split_path(f, "a_0001").exists()
    assert paths.full_stt_path(f, "a_0001").parent.name == "full-stt"
    assert not (Path(f.src) / "study-full-stt").exists()
    assert paths.auto_wav_path(f, "a_0001", 1).parent.name == "auto-part-wav"
    assert paths.auto_txt_path(f, "a_0001", 1).parent.name == "auto-part-stt"
    wav, txt = paths.auto_wav_path(f, "a_0001", 1), paths.auto_txt_path(f, "a_0001", 1)
    assert txt.read_text(encoding="utf-8") == "고친 문장"
    info = wavio.read_info(wav)
    assert (info.rate, info.channels) == (auto.audio.rate, auto.audio.info.channels)
    assert not paths.auto_wav_path(f, "a_0001", 2).exists()
    assert auto.segs[0].saved_text == "고친 문장" and auto.segs[1].saved_text is None
    assert not (f.wav_dir / "a_0001.part-1.wav").exists()                # 수동 분할 폴더는 그대로
    auto.segs[0].text = "또 고침"
    assert segments.seg_state(auto.segs[0]) == "✎ 수정됨"


def drag_edge(win, row, side, to_t):
    """파형에서 row 구간을 고르고 시작(0)·끝(1) 경계를 to_t초로 끈다."""
    win.auto_wave.playheadMoved.emit((win.segs[row].t0 + win.segs[row].t1) / 2)
    v = win.auto_wave
    y = v.height() // 2
    x0 = round(v.x_of((win.segs[row].t0, win.segs[row].t1)[side]))
    QTest.mousePress(v, Qt.MouseButton.LeftButton, pos=QPoint(x0, y))
    QTest.mouseMove(v, QPoint(round(v.x_of(to_t)), y))
    QTest.mouseRelease(v, Qt.MouseButton.LeftButton, pos=QPoint(round(v.x_of(to_t)), y))


def test_drag_band_edge_moves_segment(auto, qtbot):
    split_done(auto, qtbot)
    s = auto.segs[0]
    old_t0, old_t1 = s.t0, s.t1
    drag_edge(auto, 0, 1, old_t1 - 1.2)
    assert s.t0 == old_t0 and s.t1 == pytest.approx(old_t1 - 1.2, abs=0.05)
    assert s.text == client.words_in(WORDS, s.t0, s.t1) and len(s.text.split()) < 8   # 끝 단어가 빠진다
    assert len(s.audio) == round((s.t1 - s.t0) * auto.audio.rate)                      # 저장될 소리도 새 구간
    assert s.flag == "short" and s.wav_stale
    t = auto.seg_table
    assert t.item(0, segments.COL_SPAN).text() == f"{segments.fmt_t(s.t0)} ~ {segments.fmt_t(s.t1)}"
    assert t.item(0, segments.COL_TEXT).text() == s.text
    assert auto.auto_wave.bands[0][:2] == (s.t0, s.t1)
    auto._auto_flush()
    g = paths.load_split(auto.folders, "a_0001")["segments"][0]
    assert (g["t1"], g["wav_stale"]) == (round(s.t1, 3), True)


def test_band_edge_stops_at_neighbor_and_keeps_typed_text(auto, qtbot):
    split_done(auto, qtbot)
    auto.segs[0].text = "직접 고친 문장"
    drag_edge(auto, 0, 1, auto.segs[1].t0 + 0.3)
    assert auto.segs[0].t1 == auto.segs[1].t0                     # 다음 구간과 겹치지 않는다
    assert auto.segs[0].text == "직접 고친 문장"                    # 손으로 고친 문장은 그대로
    drag_edge(auto, 1, 0, auto.segs[1].t1)
    assert auto.segs[1].duration == pytest.approx(waveform.BAND_MIN_S)   # 최소 길이 아래로 줄지 않는다


def test_edited_bounds_rewrite_saved_wav(auto, qtbot):
    split_done(auto, qtbot)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    wav = paths.auto_wav_path(auto.folders, "a_0001", 1)
    before = wavio.read_info(wav).frames
    s = auto.segs[0]
    drag_edge(auto, 0, 0, s.t0 + 0.8)
    assert segments.seg_state(s).endswith("✎ 수정됨")
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    after = wavio.read_info(wav).frames
    assert after == round((s.t1 - s.t0) * auto.audio.rate) and after < before
    assert paths.auto_txt_path(auto.folders, "a_0001", 1).read_text(encoding="utf-8") == s.text
    assert not s.wav_stale and segments.seg_state(s).endswith("✓ 저장됨")
    assert paths.load_split(auto.folders, "a_0001")["segments"][0]["wav_stale"] is False


def test_band_edit_without_noise_profile_renders_in_background(auto, qtbot):
    split_done(auto, qtbot)
    auto._auto_nr, auto.profile = 2, None
    other = auto.segs[1].audio
    drag_edge(auto, 0, 1, auto.segs[0].t1 - 0.5)
    s = auto.segs[0]
    qtbot.waitUntil(lambda: auto._auto_worker is None and s.audio is not None, timeout=5000)
    assert len(s.audio) == round((s.t1 - s.t0) * auto.audio.rate)
    assert auto.segs[1].audio is other                                   # 고치지 않은 구간은 다시 만들지 않는다


def test_popup_zoom_buttons(auto, qtbot):
    from PySide6.QtWidgets import QToolButton
    split_done(auto, qtbot)
    btn = {b.text(): b for b in auto.auto_dlg.findChildren(QToolButton)}
    v = auto.auto_wave
    auto.seg_table.setCurrentCell(1, segments.COL_LEN)
    s = auto.segs[1]
    btn["확대"].click()
    assert v.view[1] - v.view[0] < auto.audio.duration
    assert v.view[0] <= (s.t0 + s.t1) / 2 <= v.view[1]                  # 고른 구간을 가운데에 두고 확대
    span = v.view[1] - v.view[0]
    btn["축소"].click()
    assert v.view[1] - v.view[0] > span
    btn["구간 맞춤"].click()
    assert v.view[0] < s.t0 and s.t1 < v.view[1] and v.view[1] - v.view[0] < s.duration + 1.0
    btn["전체"].click()
    assert v.view == (0.0, auto.audio.duration)


def test_band_edit_locked_while_saving(auto, qtbot):
    split_done(auto, qtbot)
    auto._auto_saving = True
    auto._auto_refresh()
    old = auto.segs[0].t1
    drag_edge(auto, 0, 1, old - 1.0)
    assert auto.segs[0].t1 == old
    auto._auto_saving = False
    auto._auto_refresh()


def test_uncheck_saved_pair_asks_then_deletes(auto, qtbot, monkeypatch):
    split_done(auto, qtbot)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    wav = paths.auto_wav_path(auto.folders, "a_0001", 1)
    auto.seg_table.item(0, segments.COL_SAVE).setCheckState(Qt.CheckState.Unchecked)
    assert auto.segs[0].selected is False
    answer(monkeypatch, QMessageBox.StandardButton.No)
    auto.save_auto()
    assert wav.exists() and not auto._auto_saving
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    assert not wav.exists()
    assert paths.full_stt_path(auto.folders, "a_0001").exists()   # 원본 인식 결과는 공용이라 지우지 않는다


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
    wav = paths.auto_wav_path(auto.folders, "a_0001", 1)
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
    assert auto.auto_wave.playhead == pytest.approx(auto.segs[1].t0 + 0.1)   # 구간 재생 위치를 원본 시간으로 표시
    auto._on_play_stop("seg", 0.2)
    assert auto.seg_table.item(1, segments.COL_PLAY).text() == "▶"
    auto._auto_play_current()                                            # Ctrl+Space도 현재 구간 재생
    assert len(calls) == 2


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
    assert paths.auto_wav_path(auto.folders, "a_0001", 1).exists()
    assert "자동 분할 실행" in auto.st_msg.text()


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
    real = paths.auto_remove

    def fail_second(folders, base, n):
        if n == 2:
            raise PermissionError("잠김")
        real(folders, base, n)

    monkeypatch.setattr(paths, "auto_remove", fail_second)
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
    assert auto.auto_text.extraSelections()[-1].cursor.selectedText() == "네"


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
    assert win._ask_no_default("다시 자동 분할", "시험") is False
    assert len(res) == 1


def test_new_segments_survive_moving_away_during_delete(auto, qtbot, monkeypatch):
    """AS-4: 저장본을 지우기 시작한 뒤 파일을 옮겨도 새 구간은 기록에 남아 돌아오면 보인다."""
    split_done(auto, qtbot)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    started = threading.Event()
    real = paths.auto_remove

    def slow(*a):
        started.set()
        time.sleep(0.2)
        real(*a)

    monkeypatch.setattr(paths, "auto_remove", slow)
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


def test_rerecognition_marks_list_as_old(auto, qtbot, monkeypatch):
    """RV-5: 다시 인식해 단어가 바뀌면 표의 구간이 이전 결과 기준임을 배지로 알린다. 같으면 알리지 않는다."""
    split_done(auto, qtbot)
    src_stt(auto, qtbot)
    assert "이전 인식 결과" not in auto.auto_badge.text()
    monkeypatch.setattr(FakeWorker, "words", WORDS[:-1])
    src_stt(auto, qtbot)
    assert "이전 인식 결과" in auto.auto_badge.text()


def test_saved_pairs_mark_file_list(auto, qtbot, monkeypatch):
    """자동분할로 저장한 원본도 파일 목록에 ✔로 표시하고, 지우면 다시 ○가 된다."""
    split_done(auto, qtbot)
    assert auto.file_list.item(0).text().startswith("○")
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    assert auto.file_list.item(0).text().startswith("✔") and "1쌍" in auto.file_list.item(0).text()
    assert "생성 완료 1개" in auto.drawer_sum.text()
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.run_auto_split()
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert auto.file_list.item(0).text().startswith("○")


def check_image(table, row):
    """체크 칸의 체크 네모를 이미지로 얻는다."""
    table.resize(900, 200)
    rect = table.visualRect(table.model().index(row, segments.COL_SAVE))
    return table.viewport().grab(rect).toImage(), rect


def test_check_is_blue_on_white(auto, qtbot):
    split_done(auto, qtbot)
    table = auto.seg_table
    img, _ = check_image(table, 0)                                       # 선택된 구간
    box = [img.pixelColor(x, y) for x in range(img.width()) for y in range(img.height())]
    blue = [c for c in box if c.blue() > 200 and c.red() < 90]
    white = [c for c in box if c.red() > 250 and c.green() > 250 and c.blue() > 250]
    assert blue and len(white) > len(blue)                               # 파란 체크, 흰 바탕
    off, _ = check_image(table, 1)                                       # 해제된 구간에는 파란색이 없음
    assert not [1 for x in range(off.width()) for y in range(off.height())
                if off.pixelColor(x, y).blue() > 200 and off.pixelColor(x, y).red() < 90]


def test_clicking_painted_check_toggles(auto, qtbot):
    split_done(auto, qtbot)
    table = auto.seg_table
    opt_rect = table.visualRect(table.model().index(1, segments.COL_SAVE))
    pos = opt_rect.center()
    pos.setX(opt_rect.left() + 12)                                       # 체크 네모 위치 (칸 왼쪽)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert auto.segs[1].selected is True


def test_stale_mark_survives_closed_popup_and_reopen(auto, qtbot, monkeypatch):
    """PX-2: 창을 닫고 다시 인식하거나 파일을 오가도 구간이 이전 결과 기준임을 표시한다."""
    split_done(auto, qtbot)
    assert "이전 인식 결과" not in auto.auto_badge.text()
    auto.auto_dlg.reject()
    monkeypatch.setattr(FakeWorker, "words", WORDS[:-1])
    src_stt(auto, qtbot)
    auto.act_auto.trigger()
    assert "이전 인식 결과" in auto.auto_badge.text()
    auto.load_index(1, ask=False)
    auto.load_index(0, ask=False)
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert "이전 인식 결과" in auto.auto_badge.text()


def test_old_record_without_key_is_not_marked_stale(auto, qtbot):
    split_done(auto, qtbot)
    rec = paths.load_split(auto.folders, "a_0001")
    del rec["stt"], rec["version"]
    paths.save_split(auto.folders, "a_0001", rec)
    auto.load_index(1, ask=False)
    auto.load_index(0, ask=False)
    qtbot.waitUntil(lambda: auto._auto_worker is None, timeout=5000)
    assert auto.segs and not auto._auto_stale()


def test_discard_on_open_restores_saved_text(win, qtbot):  # noqa: F811
    """PX-3: 메인 창 ②의 수정을 버리면 팝업과 ②에 저장된 문장이 보인다."""
    src_stt(win, qtbot)
    saved = win.src_text.toPlainText()
    win.src_text.setPlainText("버릴 문장")
    win.act_auto.trigger()                                               # 픽스처의 기본 응답은 [버리기]
    assert win.src_text.toPlainText() == saved and win.auto_text.toPlainText() == saved
    assert not win.src_dirty


def test_popup_focus_stays_off_file_list(auto):
    """PX-4: 방향키나 휠로 파일이 바뀌지 않게 한다."""
    assert auto.auto_dlg.focusWidget() is auto.auto_wave
    assert auto.auto_file.focusPolicy() == Qt.FocusPolicy.ClickFocus


def test_delete_pair_recounts_from_disk(win, qtbot, monkeypatch):  # noqa: F811
    """PX-7: Wav가 이미 없는 쌍이 있어도 표시와 실제 개수가 같다."""
    src_stt(win, qtbot)
    for sel in ((1.0, 2.0), (3.0, 4.0)):
        win.wave.set_selection(sel)
        make_pair(win, qtbot)
    win.pairs[0].wav.unlink()
    win.cur_pair = 1
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    win.delete_pair()
    answer(monkeypatch, QMessageBox.StandardButton.Discard)
    assert win.counts["a_0001"] == paths.pair_count(win.folders, "a_0001") == 0


def test_merge_with_prev_and_next(auto, qtbot, monkeypatch):
    """선택한 구간을 앞·뒤 구간과 합친다: 앞 구간 번호를 쓰고 문장·소리를 새 구간에 맞춘다."""
    split_done(auto, qtbot)
    assert len(auto.segs) == 2
    a, b = auto.segs
    t0, t1, n = a.t0, b.t1, a.n
    auto.seg_table.select_row(0)
    auto._on_seg_picked(0)
    assert not auto.btn_merge_prev.isEnabled() and auto.btn_merge_next.isEnabled()
    auto.merge_seg(-1)                                     # 첫 구간은 앞 구간이 없다
    assert len(auto.segs) == 2
    auto.seg_table.select_row(1)
    auto._on_seg_picked(1)
    assert auto.btn_merge_prev.isEnabled() and not auto.btn_merge_next.isEnabled()
    auto.merge_seg(-1)                                     # 뒤 구간('네')을 앞 구간과 합친다
    assert len(auto.segs) == 1 and auto.seg_table.rowCount() == 1
    m = auto.segs[0]
    assert (m.t0, m.t1, m.n) == (t0, t1, n)
    assert m.text == " ".join(w["word"] for w in WORDS)
    assert m.flag == "" and m.selected and m.wav_stale and m.audio is not None
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    wav = paths.auto_wav_path(auto.folders, "a_0001", n)
    assert abs(wavio.read_info(wav).duration - (t1 - t0)) < 0.01
    assert paths.read_text(paths.auto_txt_path(auto.folders, "a_0001", n)) == m.text
    assert not m.wav_stale


def test_merge_removes_saved_pair_of_absorbed_segment(auto, qtbot, monkeypatch):
    """이미 저장한 뒤 구간을 합치면 [선택 저장] 때 확인을 거쳐 그 쌍을 지운다."""
    split_done(auto, qtbot)
    auto.seg_table.item(1, segments.COL_SAVE).setCheckState(Qt.CheckState.Checked)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    n1, n2 = auto.segs[0].n, auto.segs[1].n
    assert paths.auto_numbers(auto.folders, "a_0001") == {n1, n2}
    auto.seg_table.select_row(0)
    auto.merge_seg(1)                                      # 앞 구간을 뒤 구간과 합친다
    assert [s.n for s in auto.segs] == [n1]
    answer(monkeypatch, QMessageBox.StandardButton.No)
    auto.save_auto()                                       # 삭제를 거절하면 저장하지 않는다
    assert paths.auto_numbers(auto.folders, "a_0001") == {n1, n2}
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    auto.save_auto()
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    assert paths.auto_numbers(auto.folders, "a_0001") == {n1}


def test_merge_refuses_over_max_length(auto, qtbot, monkeypatch):
    split_done(auto, qtbot)
    monkeypatch.setattr(segment, "MAX_SEG_S", 3.0)
    auto.seg_table.select_row(0)
    auto.merge_seg(1)
    assert len(auto.segs) == 2 and "넘어 합칠 수 없습니다" in auto.auto_msg.text()


def _three_segs(auto):
    import numpy as np
    from wavetext.audio.autosplit import Seg
    segs = [Seg(t0, t1, "", "", n, True, audio=np.zeros((10, 1), np.float32)) for n, (t0, t1) in
            enumerate(((0.0, 1.5), (2.0, 3.5), (4.0, 5.5)), 1)]
    auto._auto_set_segs(segs, auto._auto_key)
    return segs


def test_auto_save_blocked_when_folders_clash(auto, qtbot):
    """V3-2: 설정 파일을 고쳐 폴더가 겹쳐도 자동분할 저장·다시 분할을 하지 않는다."""
    from dataclasses import replace
    split_done(auto, qtbot)
    x = Path(auto.folders.src) / "X"
    auto.folders = replace(auto.folders, wav=str(x), auto_wav=str(x))
    auto.save_auto()
    assert not auto._auto_saving and not x.exists() and "폴더가 같습니다" in auto.auto_msg.text()
    auto.run_auto_split()
    assert auto._auto_worker is None


def test_merge_keeps_play_mark_of_other_segment(auto, qtbot, monkeypatch):
    """V3-4: 다른 구간을 재생하는 중에 합쳐도 그 구간의 ■ 표시를 유지한다."""
    split_done(auto, qtbot)
    _three_segs(auto)
    monkeypatch.setattr(auto.player, "play", lambda *a: True)
    auto.seg_table.select_row(0)
    auto.seg_table.playClicked.emit(0)
    assert auto.seg_table.item(0, segments.COL_PLAY).text() == "■"
    auto.seg_table.select_row(1)
    auto.merge_seg(1)                                      # 2·3번을 합친다
    assert len(auto.segs) == 2 and auto.seg_table.item(0, segments.COL_PLAY).text() == "■"


def test_merge_message_when_split_is_stale(auto, qtbot):
    """V3-6: 다시 인식해 구간이 이전 결과 기준이면 그에 맞게 안내한다."""
    split_done(auto, qtbot)
    auto._auto_src_key = "이전 인식"
    assert auto._auto_stale()
    auto.seg_table.select_row(0)
    auto.merge_seg(1)
    msg = auto.auto_msg.text()
    assert "이전 인식 결과 기준" in msg and "직접 고친 문장" not in msg


def test_save_message_shows_saved_folder(auto, qtbot):
    """PX-10: 저장 중 폴더를 바꿔도 완료 안내는 저장한 폴더를 보여 준다."""
    from dataclasses import replace
    split_done(auto, qtbot)
    saved_dir = auto.folders.auto_wav_dir
    auto.save_auto()
    auto.folders = replace(auto.folders, auto_wav=str(Path(auto.folders.src) / "다른"))
    qtbot.waitUntil(lambda: not auto._auto_saving, timeout=5000)
    assert str(saved_dir) in auto.auto_msg.text()


def test_clashing_folders_warn_on_open_and_after_stt(auto, qtbot):
    """V3R-2: 겹친 폴더로 열면 바로 알리고, 인식 뒤에도 '이전 구간은 그대로' 대신 겹침을 안내한다."""
    from dataclasses import replace
    x = Path(auto.folders.src) / "X"
    f = replace(auto.folders, wav=str(x), auto_wav=str(x))
    auto.auto_dlg.reject()
    auto.open_folders(f)
    assert "폴더가 같습니다" in auto.st_msg.text()
    auto.act_auto.trigger()
    src_stt(auto, qtbot)
    assert not auto.segs and auto._auto_worker is None
    assert "폴더가 같습니다" in auto.auto_msg.text() and "이전 구간은 그대로" not in auto.auto_msg.text()
