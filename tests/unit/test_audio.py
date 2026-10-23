import numpy as np
import pytest

from wavetext.audio import convert, denoise, segment, wavio
from tests.conftest import synth_speech


@pytest.mark.parametrize("sampwidth", [1, 2, 3, 4])
@pytest.mark.parametrize("channels", [1, 2])
def test_wav_round_trip(tmp_path, sampwidth, channels):
    x = synth_speech(1.0, 8000, channels)
    p = tmp_path / f"t{sampwidth}{channels}.wav"
    wavio.write(p, x, 8000, sampwidth)
    a = wavio.read(p)
    assert a.info.sampwidth == sampwidth and a.info.channels == channels and a.info.rate == 8000
    assert a.samples.shape == x.shape
    tol = 1.5 / 128 if sampwidth == 1 else 1e-4
    assert np.max(np.abs(a.samples - x)) < tol


def test_write_never_overwrites(tmp_path):
    p = tmp_path / "a.wav"
    wavio.write(p, synth_speech(0.2), 8000, 2)
    with pytest.raises(FileExistsError):
        wavio.write(p, synth_speech(0.2), 8000, 2)


def test_describe_goldwave_format():
    info = wavio.WavInfo(rate=8000, channels=1, sampwidth=2, frames=145696)
    assert wavio.describe(info) == "18.212초 · Wave PCM signed 16 bit, 8000 Hz, 128 kbps, mono"
    assert "unsigned 8 bit" in wavio.describe(wavio.WavInfo(16000, 2, 1, 16000))
    assert wavio.describe(wavio.WavInfo(16000, 2, 1, 16000)).endswith("256 kbps, stereo")


def test_unsupported_file(tmp_path):
    p = tmp_path / "bad.wav"
    p.write_bytes(b"not a wave file")
    with pytest.raises(wavio.UnsupportedWav):
        wavio.read_info(p)


def test_cut_exact_frames_and_clamp():
    x = np.arange(8000, dtype=np.float32)[:, None]
    y = segment.cut(x, 8000, 0.25, 0.5)
    assert len(y) == 2000 and y[0, 0] == 2000
    assert len(segment.cut(x, 8000, 0.9, 5.0)) == 800  # 파일 끝을 넘으면 끝까지
    assert len(segment.cut(x, 8000, 0.5, 0.25)) == 2000  # 역순 선택


def test_selection_limits():
    assert segment.selection_ok(0, 30.0)
    assert not segment.selection_ok(0, 30.01)
    assert not segment.selection_ok(1.0, 1.04)
    assert segment.selection_ok(1.0, 1.05)


def test_fade_edges_zero_and_middle_kept():
    x = np.ones((800, 1), np.float32)
    y = segment.fade(x, 8000, 10)
    assert y[0, 0] == 0.0 and y[-1, 0] < 0.02
    assert np.all(y[80:-80] == 1.0)


def test_denoise_lowers_noise_keeps_speech():
    rate = 8000
    x = synth_speech(6.0, rate, noise=0.02)
    prof = denoise.estimate_profile(x[:, 0], rate)
    assert prof is not None
    y = denoise.reduce(x, rate, prof, 2)
    assert y.shape == x.shape
    noise_before = np.sqrt(np.mean(x[: rate // 2, 0] ** 2))   # 앞 0.5초: 잡음만
    noise_after = np.sqrt(np.mean(y[: rate // 2, 0] ** 2))
    speech_m = slice(int(1.05 * rate), int(1.2 * rate))       # 음절 가운데
    assert noise_after < noise_before * 0.5
    assert np.sqrt(np.mean(y[speech_m, 0] ** 2)) > 0.8 * np.sqrt(np.mean(x[speech_m, 0] ** 2))


def test_denoise_level0_and_short_input_passthrough():
    x = synth_speech(6.0)
    prof = denoise.estimate_profile(x[:, 0], 8000)
    assert np.array_equal(denoise.reduce(x, 8000, prof, 0), x)
    short = x[:100]
    assert np.array_equal(denoise.reduce(short, 8000, prof, 3), short)


def test_stronger_level_reduces_more():
    x = synth_speech(6.0, noise=0.03)
    prof = denoise.estimate_profile(x[:, 0], 8000)
    rms = [np.sqrt(np.mean(denoise.reduce(x, 8000, prof, lv)[:4000, 0] ** 2)) for lv in (1, 2, 3)]
    assert rms[0] > rms[1] > rms[2]


def test_to_stt_pcm_resamples_and_mixes():
    x = synth_speech(1.0, 16000, channels=2)
    pcm = convert.to_stt_pcm(x, 16000)
    assert len(pcm) == 8000 * 2  # 1초 · 8kHz · 16비트
    assert len(convert.to_stt_pcm(synth_speech(1.0, 8000), 8000)) == 16000
