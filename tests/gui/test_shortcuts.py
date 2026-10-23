"""글자 입력칸에 커서가 있을 때 단축키(Space, [, ], Del, ←)가 글자 편집으로 동작하는지 확인."""
from PySide6.QtCore import Qt

from tests.gui.test_main_window import win  # noqa: F401  (픽스처 재사용)


def test_typing_keys_go_to_text_box(win, qtbot):
    win.wave.set_selection((1.0, 2.0))
    win.src_text.setFocus()
    qtbot.waitUntil(lambda: win.src_text.hasFocus(), timeout=2000)
    for key in (Qt.Key.Key_Space, Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight):
        qtbot.keyClick(win.src_text, key)
    assert win.src_text.toPlainText() == " []"
    assert win.wave.sel == (1.0, 2.0)          # [ ] 가 마크를 바꾸지 않음
    assert not win.player.is_playing()         # Space 가 재생을 시작하지 않음
    qtbot.keyClick(win.src_text, Qt.Key.Key_Left)
    qtbot.keyClick(win.src_text, Qt.Key.Key_Delete)
    assert win.src_text.toPlainText() == " ["
    assert win.wave.playhead is None           # ← 가 재생 위치를 옮기지 않음


def test_shortcuts_work_when_wave_focused(win, qtbot):
    win.wave.setFocus()
    win.wave.set_playhead(2.0)
    qtbot.keyClick(win.wave, Qt.Key.Key_BracketLeft)
    assert win.wave.sel and abs(win.wave.sel[0] - 2.0) < 1e-6
    qtbot.keyClick(win.wave, Qt.Key.Key_Escape)
    assert win.wave.sel is None
