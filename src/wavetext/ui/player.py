"""스피커/이어폰 재생 (FR-06, FR-14).

장치가 원본 샘플레이트(예: 8kHz)를 직접 지원하지 않는 경우가 많다. 파일 전체를 미리 변환하면 긴 녹취에서
화면이 멈추고 메모리를 많이 쓰므로, 장치가 요청할 때마다 필요한 만큼만 선형 보간으로 변환해 보낸다(_Stream).
"""
import numpy as np
from PySide6.QtCore import QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices


def _is(value, name):
    """장치 상태·오류 비교. PySide6 6.12는 QAudioSink가 QtAudio.* 값을 돌려주어 QAudio.*와 같지 않으므로
    (X-1: 오류가 없어도 '오류'로 판정되어 재생이 전혀 안 됨) 버전에 상관없이 이름으로 비교한다."""
    return getattr(value, "name", str(value)).split(".")[-1] == name


class _Stream(QIODevice):
    """samples (frames, channels) 를 out_rate·out_ch·형식으로 조금씩 변환해 내보내는 읽기 전용 장치."""

    def __init__(self, samples, rate, out_rate, out_ch, sample_format, parent=None):
        super().__init__(parent)
        x = samples
        if x.shape[1] != out_ch:
            x = x.mean(axis=1, keepdims=True)
            if out_ch > 1:
                x = np.repeat(x, out_ch, axis=1)
        self.x = np.ascontiguousarray(x, dtype=np.float32)
        self.step = rate / out_rate           # 출력 1프레임당 원본 프레임 이동량
        self.out_ch = out_ch
        self.float_out = sample_format == QAudioFormat.SampleFormat.Float
        self.bytes_per_frame = out_ch * (4 if self.float_out else 2)
        self.total_out = int(len(self.x) / self.step)
        self.pos_out = 0                      # 내보낸 출력 프레임 수

    def render(self, frames):
        """다음 frames 개 출력 프레임을 바이트로 만든다 (시험에서도 직접 호출)."""
        n = min(frames, self.total_out - self.pos_out)
        if n <= 0:
            return b""
        t = (self.pos_out + np.arange(n)) * self.step
        i = np.minimum(t.astype(np.int64), len(self.x) - 1)
        j = np.minimum(i + 1, len(self.x) - 1)
        f = (t - i)[:, None].astype(np.float32)
        y = self.x[i] * (1 - f) + self.x[j] * f
        self.pos_out += n
        if self.float_out:
            return np.clip(y, -1, 1).astype("<f4").tobytes()
        return np.clip(np.round(y * 32767.0), -32768, 32767).astype("<i2").tobytes()

    def readData(self, maxlen):
        return self.render(max(0, maxlen // self.bytes_per_frame))

    def writeData(self, data):
        return -1

    def bytesAvailable(self):
        return (self.total_out - self.pos_out) * self.bytes_per_frame + super().bytesAvailable()

    def isSequential(self):
        return True


def output_format(dev, rate, channels):
    """장치가 지원하는 형식: 원본 그대로 → 장치 기본 샘플레이트(Int16) → 장치 기본 형식(Float 등) 순으로 시도."""
    pref = dev.preferredFormat()
    out_ch = max(1, min(2, pref.channelCount() or 2))
    candidates = []
    for r, ch, sf in ((rate, min(channels, 2), QAudioFormat.SampleFormat.Int16),
                      (pref.sampleRate() or 48000, out_ch, QAudioFormat.SampleFormat.Int16),
                      (pref.sampleRate() or 48000, out_ch, pref.sampleFormat())):
        fmt = QAudioFormat()
        fmt.setSampleRate(r)
        fmt.setChannelCount(ch)
        fmt.setSampleFormat(sf)
        candidates.append(fmt)
    for fmt in candidates:
        if fmt.sampleFormat() in (QAudioFormat.SampleFormat.Int16, QAudioFormat.SampleFormat.Float) and dev.isFormatSupported(fmt):
            return fmt
    return None


class Player(QObject):
    position = Signal(str, float)   # (재생 대상 이름, 시간축 위치 초)
    stopped = Signal(str, float)    # (재생 대상 이름, 멈춘 위치 초)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sink = None
        self._dev = None
        self._tag = None
        self._start = 0.0
        self._end = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    @property
    def tag(self):
        return self._tag

    def is_playing(self, tag=None):
        return self._sink is not None and (tag is None or tag == self._tag)

    def current(self):
        """들리는 위치: 장치에 넘긴 양에서 아직 장치 버퍼에 남은 양을 뺀다."""
        if self._sink is None:
            return None
        fmt = self._sink.format()
        queued = (self._sink.bufferSize() - self._sink.bytesFree()) / max(1, fmt.bytesPerFrame()) / max(1, fmt.sampleRate())
        t = self._start + self._sink.processedUSecs() / 1e6 - max(0.0, queued)
        return max(self._start, min(self._end, t))

    def play(self, tag, samples, rate, start_s=0.0, end_s=None):
        """samples (frames, channels)의 start_s~end_s 구간 재생. 위치는 원본 시간축(초)으로 알린다."""
        self.stop()
        frames = len(samples)
        end_s = frames / rate if end_s is None else end_s
        i0, i1 = max(0, int(start_s * rate)), min(frames, int(end_s * rate))
        if i1 - i0 < 2:
            return False
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            self.error.emit("재생 장치(스피커/이어폰)를 찾을 수 없습니다.")
            return False
        fmt = output_format(dev, rate, samples.shape[1])
        if fmt is None:
            self.error.emit("재생 장치가 지원하는 형식을 찾지 못했습니다.")
            return False
        self._dev = _Stream(samples[i0:i1], rate, fmt.sampleRate(), fmt.channelCount(), fmt.sampleFormat(), self)
        self._dev.open(QIODevice.OpenModeFlag.ReadOnly)
        # stateChanged 신호는 PySide6 6.12에서 인자 변환에 실패해 받을 수 없으므로(QAudio::State),
        # 30ms 타이머에서 state()를 직접 확인해 끝을 알아낸다.
        self._sink = QAudioSink(dev, fmt, self)
        self._tag, self._start, self._end = tag, i0 / rate, i1 / rate
        self._sink.start(self._dev)
        if not _is(self._sink.error(), "NoError"):
            self.error.emit(f"재생할 수 없습니다 (장치 오류 {self._sink.error().name}).")
            self._cleanup()
            return False
        self._timer.start()
        return True

    def stop(self):
        if self._sink is None:
            return
        tag, pos = self._tag, self.current()
        self._cleanup()
        self.stopped.emit(tag, pos)

    def _cleanup(self):
        self._timer.stop()
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
        if self._dev is not None:
            self._dev.close()
            self._dev.deleteLater()
        self._sink = self._dev = None
        self._tag = None

    def _tick(self):
        if self._sink is None:
            return
        state = self._sink.state()
        if _is(state, "StoppedState"):  # 장치 쪽에서 멈춤(오류, 기본 장치 변경 등) → 재생 중 상태를 남기지 않는다 (n-5)
            if not _is(self._sink.error(), "NoError"):
                self.error.emit(f"재생 중 장치 오류 ({getattr(self._sink.error(), 'name', '')}).")
            self.stop()
            return
        # 끝까지 재생함: 보낼 데이터가 없고, 장치 버퍼까지 다 비웠음(대기 상태 또는 들리는 위치가 끝에 도달).
        # 장치에 넘긴 양만 보고 멈추면 버퍼에 남은 마지막 부분이 잘린다.
        done = self._dev.pos_out >= self._dev.total_out and (
            _is(state, "IdleState") or self.current() >= self._end - 1e-3)
        if done:
            tag, end = self._tag, self._end
            self._cleanup()
            self.stopped.emit(tag, end)
            return
        self.position.emit(self._tag, self.current())
