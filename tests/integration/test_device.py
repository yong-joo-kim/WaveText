"""실제 재생 장치 시험 (X-1 회귀). 무음을 재생하므로 소리는 나지 않는다. 재생 장치가 없으면 건너뛴다."""
import time

import numpy as np
import pytest
from PySide6.QtMultimedia import QMediaDevices

from wavetext.ui.player import Player, output_format


@pytest.fixture
def player(qtbot):
    if QMediaDevices.defaultAudioOutput().isNull():
        pytest.skip("재생 장치 없음")
    p = Player()
    yield p
    p.stop()


def test_device_format_found(player):
    dev = QMediaDevices.defaultAudioOutput()
    assert output_format(dev, 8000, 1) is not None


def test_play_to_end_emits_stopped(player, qtbot):
    """재생이 시작되고(오류로 판정되지 않음), 끝까지 재생하면 stopped 신호가 온다."""
    silence = np.zeros((4000, 1), np.float32)  # 0.5초 · 8kHz
    with qtbot.waitSignal(player.stopped, timeout=5000) as sig:
        assert player.play("t", silence, 8000) is True
        assert player.is_playing("t")
    assert sig.args[0] == "t" and abs(sig.args[1] - 0.5) < 1e-6
    assert not player.is_playing()


def test_pause_position_close_to_elapsed(player, qtbot):
    """일시정지 위치가 실제 경과 시간과 크게 다르지 않다 (C-2)."""
    silence = np.zeros((16000, 1), np.float32)  # 2초
    assert player.play("t", silence, 8000, start_s=0.5)
    t0 = time.monotonic()
    qtbot.wait(600)
    elapsed = time.monotonic() - t0
    with qtbot.waitSignal(player.stopped, timeout=2000) as sig:
        player.stop()
    pos = sig.args[1]
    assert 0.5 <= pos <= 0.5 + elapsed + 0.05          # 시작점 기준, 경과 시간을 넘지 않음
    assert pos >= 0.5 + elapsed - 0.35                  # 장치 버퍼만큼만 뒤처짐
