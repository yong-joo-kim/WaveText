"""화면이 멈추지 않도록 음성인식을 작업 스레드에서 실행 (NFR-04)."""
import threading
import uuid

from PySide6.QtCore import QThread, Signal

from wavetext.stt import client


class SttWorker(QThread):
    """신호에 작업자 자신을 함께 실어 보내, 받는 쪽(메인 창 메서드)이 어느 작업인지 알 수 있게 한다.
    메인 창의 바운드 메서드로 연결하면 Qt가 화면 스레드에서 실행한다(대기열 연결)."""
    progressed = Signal(object, float)        # (작업자, 0~1)
    result = Signal(object, object, str)      # (작업자, {"transcript","words"} 또는 None, 오류 메시지)

    def __init__(self, pcm, stt_cfg, model, parent=None, transcribe=None):
        super().__init__(parent)
        self.pcm = pcm
        self.cfg = stt_cfg
        self.model = model
        self.tag = None
        self.stop_event = threading.Event()
        self._transcribe = transcribe or client.transcribe_with_retry

    def cancel(self):
        self.stop_event.set()

    @property
    def cancelled(self):
        return self.stop_event.is_set()

    def run(self):
        try:
            r = self._transcribe(self.pcm, self.cfg, self.model, f"wavetext-{uuid.uuid4().hex[:12]}",
                                 on_progress=lambda x: self.progressed.emit(self, x), stop=self.stop_event)
            self.result.emit(self, {"transcript": r["transcript"], "words": r.get("words") or []}, "")
        except client.SttError as e:
            self.result.emit(self, None, str(e))
        except Exception as e:  # 예상 못 한 오류도 화면에 알린다
            self.result.emit(self, None, f"{type(e).__name__}: {e}")


class TaskWorker(QThread):
    """시간이 걸리는 계산·파일 저장(노이즈 제거, Wav 쓰기)을 작업 스레드에서 실행한다 (NFR-04)."""
    result = Signal(object, object, str)   # (작업자, 반환값 또는 None, 오류 메시지)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.tag = None

    def cancel(self):
        pass

    def run(self):
        try:
            self.result.emit(self, self.fn(), "")
        except OSError as e:
            self.result.emit(self, None, f"파일 저장 실패: {e}")
        except Exception as e:
            self.result.emit(self, None, f"{type(e).__name__}: {e}")


class HealthWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, server, parent=None):
        super().__init__(parent)
        self.server = server

    def run(self):
        ok, msg = client.health(self.server)
        self.done.emit(ok, msg)
