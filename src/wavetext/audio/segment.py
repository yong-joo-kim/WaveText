"""구간 자르기와 경계 페이드 (FR-07, FR-08, FR-10)."""
import numpy as np

MAX_SEG_S = 30.0  # 발췌 최대 길이
MIN_SEG_S = 0.05
FADE_MS = 10.0


def frame_range(rate, t0, t1, frames):
    a, b = sorted((t0, t1))
    i0 = max(0, min(frames, int(round(a * rate))))
    i1 = max(0, min(frames, int(round(b * rate))))
    return i0, i1


def selection_ok(t0, t1):
    """생성 가능한 구간인지: 0.05초 이상, 30초 이하."""
    length = abs(t1 - t0)
    return MIN_SEG_S <= length <= MAX_SEG_S + 1e-9


def cut(samples, rate, t0, t1):
    i0, i1 = frame_range(rate, t0, t1, len(samples))
    return samples[i0:i1].copy()


def fade(samples, rate, ms=FADE_MS):
    """양 끝을 선형으로 키우고 줄여 잘린 경계의 '딱' 소리를 막는다."""
    out = samples.copy()
    n = min(int(rate * ms / 1000.0), len(out) // 2)
    if n <= 0:
        return out
    ramp = np.linspace(0.0, 1.0, n, endpoint=False, dtype=np.float32)[:, None]
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out
