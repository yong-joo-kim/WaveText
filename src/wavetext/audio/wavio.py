"""PCM WAV 읽기·쓰기와 속성 문자열 (FR-10, FR-19, NFR-05).

샘플은 float32 배열 (프레임 수, 채널 수), 범위 [-1, 1)로 다룬다.
"""
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class UnsupportedWav(Exception):
    """PCM 정수 WAV가 아니거나 읽을 수 없는 파일."""


@dataclass(frozen=True)
class WavInfo:
    rate: int
    channels: int
    sampwidth: int  # 바이트 (1, 2, 3, 4)
    frames: int

    @property
    def duration(self):
        return self.frames / self.rate if self.rate else 0.0

    @property
    def bits(self):
        return self.sampwidth * 8


@dataclass
class Audio:
    samples: np.ndarray  # float32 (frames, channels)
    info: WavInfo

    @property
    def rate(self):
        return self.info.rate

    @property
    def duration(self):
        return len(self.samples) / self.info.rate

    def mono(self):
        return self.samples.mean(axis=1) if self.samples.shape[1] > 1 else self.samples[:, 0]


def _open(path):
    try:
        return wave.open(str(path), "rb")
    except (wave.Error, EOFError) as e:
        raise UnsupportedWav(f"PCM WAV가 아닙니다: {e}") from e


def read_info(path):
    """헤더만 읽는다 (NFR-03)."""
    with _open(path) as w:
        info = WavInfo(w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes())
    if info.sampwidth not in (1, 2, 3, 4):
        raise UnsupportedWav(f"지원하지 않는 비트 수: {info.bits}비트")
    if info.channels not in (1, 2):
        raise UnsupportedWav(f"모노·스테레오만 지원합니다 ({info.channels}채널)")
    if info.rate <= 0:
        raise UnsupportedWav("샘플레이트 정보가 없습니다")
    return info


def _decode(raw, sampwidth):
    if sampwidth == 1:  # 8비트 WAV는 부호 없는 정수
        return (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    if sampwidth == 2:
        return np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if sampwidth == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        return v.astype(np.float32) / 8388608.0
    return (np.frombuffer(raw, "<i4").astype(np.float64) / 2147483648.0).astype(np.float32)


def _encode(x, sampwidth):
    x = np.clip(x, -1.0, 1.0)
    if sampwidth == 1:
        return np.clip(np.round(x * 128.0 + 128.0), 0, 255).astype(np.uint8).tobytes()
    if sampwidth == 2:
        return np.clip(np.round(x * 32768.0), -32768, 32767).astype("<i2").tobytes()
    if sampwidth == 3:
        v = np.clip(np.round(x.astype(np.float64) * 8388608.0), -8388608, 8388607).astype(np.int32)
        v = np.where(v < 0, v + 0x1000000, v).astype(np.uint32)
        out = np.empty((len(v), 3), np.uint8)
        out[:, 0], out[:, 1], out[:, 2] = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
        return out.tobytes()
    return np.clip(np.round(x.astype(np.float64) * 2147483648.0), -2147483648, 2147483647).astype("<i4").tobytes()


def read(path):
    info = read_info(path)
    with _open(path) as w:
        raw = w.readframes(info.frames)
    data = _decode(raw, info.sampwidth)
    frames = len(data) // info.channels
    samples = data[: frames * info.channels].reshape(frames, info.channels)
    return Audio(np.ascontiguousarray(samples, dtype=np.float32), WavInfo(info.rate, info.channels, info.sampwidth, frames))


def write(path, samples, rate, sampwidth):
    """samples (frames, channels)를 원본과 같은 형식으로 저장한다. 같은 이름 파일이 있으면 FileExistsError."""
    path = Path(path)
    if samples.ndim == 1:
        samples = samples[:, None]
    if path.exists():
        raise FileExistsError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(_encode(samples.reshape(-1), sampwidth))
    try:
        tmp.rename(path)  # 이미 있으면 Windows에서 실패 → 덮어쓰지 않음 (FR-12)
    except FileExistsError:
        tmp.unlink(missing_ok=True)
        raise


def channel_name(ch):
    return {1: "mono", 2: "stereo"}.get(ch, f"{ch} ch")


def describe(info):
    """상태줄 2행 형식: 18.212초 · Wave PCM signed 16 bit, 8000 Hz, 128 kbps, mono"""
    sign = "unsigned" if info.sampwidth == 1 else "signed"
    kbps = round(info.rate * info.bits * info.channels / 1000)
    return f"{info.duration:.3f}초 · Wave PCM {sign} {info.bits} bit, {info.rate} Hz, {kbps} kbps, {channel_name(info.channels)}"
