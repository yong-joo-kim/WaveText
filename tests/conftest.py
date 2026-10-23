"""시험용 음성은 코드로 합성한다 (현장 녹취는 저장소에 넣지 않음)."""
import numpy as np
import pytest

from wavetext.audio import wavio


def synth_speech(dur=6.0, rate=8000, channels=1, noise=0.01, seed=0):
    """음절처럼 끊어지는 톤 + 배경 잡음. 앞 1초는 잡음만 있다."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * rate)) / rate
    env = np.zeros_like(t)
    s = 1.0
    while s < dur - 0.3:
        ln = 0.25
        m = (t >= s) & (t < s + ln)
        env[m] = np.sin(np.pi * (t[m] - s) / ln)
        s += ln + 0.15
    tone = 0.4 * env * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 660 * t))
    x = tone + noise * rng.standard_normal(len(t))
    x = np.repeat(x[:, None], channels, axis=1).astype(np.float32)
    return x


@pytest.fixture
def speech_wav(tmp_path):
    def make(name="원본_0001.wav", dur=6.0, rate=8000, channels=1, sampwidth=2, folder=None):
        folder = folder or (tmp_path / "녹취")
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / name
        wavio.write(p, synth_speech(dur, rate, channels), rate, sampwidth)
        return p
    return make
