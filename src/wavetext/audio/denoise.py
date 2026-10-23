"""스펙트럴 게이팅 노이즈 제거 (FR-15, OI-02).

원본 파일 전체에서 에너지가 낮은 프레임(무음·잡음 구간)으로 주파수별 잡음 문턱을 추정하고,
분할 구간에서 문턱보다 약한 성분을 줄인다.
"""
from dataclasses import dataclass

import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter

# 강도: (문턱 = 잡음 평균 + n_std × 표준편차, 문턱 아래 성분 감쇠 비율)
LEVELS = {0: None, 1: (1.0, 0.55), 2: (1.5, 0.8), 3: (2.0, 0.95)}
LEVEL_NAMES = {0: "사용 안 함", 1: "약", 2: "중", 3: "강"}
QUIET_PERCENTILE = 15  # 이 비율 이하 에너지 프레임을 잡음으로 본다


@dataclass
class NoiseProfile:
    rate: int
    nperseg: int
    mean_db: np.ndarray
    std_db: np.ndarray


def _nperseg(rate):
    return int(2 ** round(np.log2(rate * 0.032)))  # 약 32ms (8kHz → 256)


def _stft(x, rate, nperseg):
    return signal.stft(x, fs=rate, nperseg=nperseg, noverlap=nperseg * 3 // 4, boundary="zeros", padded=True)


def estimate_profile(mono, rate):
    nperseg = _nperseg(rate)
    if len(mono) < nperseg * 4:
        return None
    _, _, z = _stft(mono.astype(np.float64), rate, nperseg)
    db = 20 * np.log10(np.abs(z) + 1e-10)
    energy = db.mean(axis=0)
    quiet = db[:, energy <= np.percentile(energy, QUIET_PERCENTILE)]
    if quiet.shape[1] < 2:
        return None
    return NoiseProfile(rate, nperseg, quiet.mean(axis=1), quiet.std(axis=1))


def reduce(samples, rate, profile, level):
    """samples (frames, channels) → 같은 모양의 노이즈 제거 결과. 적용할 수 없으면 원본 복사본."""
    params = LEVELS.get(level)
    if params is None or profile is None or profile.rate != rate or len(samples) < profile.nperseg:
        return samples.copy()
    n_std, prop = params
    thresh = (profile.mean_db + n_std * profile.std_db)[:, None]
    out = np.empty_like(samples)
    for c in range(samples.shape[1]):
        x = samples[:, c].astype(np.float64)
        _, _, z = _stft(x, rate, profile.nperseg)
        db = 20 * np.log10(np.abs(z) + 1e-10)
        mask = (db > thresh).astype(np.float64)
        mask = uniform_filter(mask, size=(3, 5), mode="nearest")  # 주파수·시간 방향으로 부드럽게
        gain = mask + (1.0 - mask) * (1.0 - prop)
        _, y = signal.istft(z * gain, fs=rate, nperseg=profile.nperseg, noverlap=profile.nperseg * 3 // 4, boundary=True)
        y = y[: len(x)]
        if len(y) < len(x):
            y = np.pad(y, (0, len(x) - len(y)))
        out[:, c] = y.astype(np.float32)
    return out
