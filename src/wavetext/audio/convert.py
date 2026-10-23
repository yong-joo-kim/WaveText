"""음성인식 서버 입력 변환: 8kHz · 16비트 · 모노 PCM (NFR-05)."""
from math import gcd

import numpy as np
from scipy.signal import resample_poly

STT_RATE = 8000


def resample(x, rate, target):
    if rate == target:
        return x
    g = gcd(rate, target)
    return resample_poly(x, target // g, rate // g, axis=0).astype(np.float32)


def to_stt_pcm(samples, rate):
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    x = resample(mono.astype(np.float32), rate, STT_RATE)
    return np.clip(np.round(x * 32768.0), -32768, 32767).astype("<i2").tobytes()
