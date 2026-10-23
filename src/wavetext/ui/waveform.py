"""파형 보기 위젯 (FR-05, FR-07, FR-13).

WaveformView: 진폭 눈금 + 파형 + 시간 눈금자. 끌어서 구간 선택, "시작"/"끝" 경계 끌기, 클릭으로 재생 위치, 휠로 확대.
OverviewBar: 파일 전체 미니 파형과 현재 보기 범위. 클릭·끌기로 보기 위치 이동.
"""
import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from wavetext.ui.theme import WAVE

GUTTER = 44      # 진폭 눈금 폭
RULER_H = 24     # 시간 눈금자 높이
EDGE_PX = 6      # 경계 잡기 허용 거리
MIN_VIEW_S = 0.02


def nice_step(raw):
    for s in (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if s >= raw:
            return s
    return 600


def fmt_time(t, step):
    if step >= 1:
        h, m, s = int(t // 3600), int(t // 60) % 60, int(round(t)) % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{t:.{3 if step < 0.01 else 2 if step < 0.1 else 1}f}"


def peaks(mono, rate, v0, v1, width):
    """보기 범위를 width 칸으로 나눈 칸별 (최솟값, 최댓값)."""
    n = len(mono)
    i0, i1 = max(0, int(v0 * rate)), min(n, int(math.ceil(v1 * rate)))
    if i1 - i0 < 1 or width < 1:
        return None
    seg = mono[i0:i1]
    total = (v1 - v0) * rate
    edges = (np.arange(width + 1) * total / width + v0 * rate).astype(np.int64) - i0
    edges = np.clip(edges, 0, len(seg))
    starts = edges[:-1]
    valid = starts < np.maximum(edges[1:], starts + 1)
    valid &= starts < len(seg)
    lo = np.zeros(width, np.float32)
    hi = np.zeros(width, np.float32)
    idx = np.where(valid)[0]
    if len(idx):
        s = starts[idx]
        lo[idx] = np.minimum.reduceat(seg, s)
        hi[idx] = np.maximum.reduceat(seg, s)
    # reduceat은 다음 시작점까지 계산하므로 칸이 1샘플 미만일 때도 값이 채워진다
    return lo, hi, valid


class WaveformView(QWidget):
    selectionChanged = Signal(object)   # (t0, t1) 또는 None
    playheadMoved = Signal(float)
    viewChanged = Signal(float, float)

    def __init__(self, parent=None, selectable=True, mono_family="Consolas"):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(90)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.selectable = selectable
        self.mono = None
        self.rate = 8000
        self.duration = 0.0
        self.view = (0.0, 1.0)
        self.vzoom = 1.0
        self.sel = None
        self.playhead = None
        self._drag = None
        self._font = QFont(mono_family)
        self._font.setPixelSize(12)
        self._mark_font = QFont()
        self._mark_font.setPixelSize(12)
        self._mark_font.setBold(True)

    # ---------- 데이터 ----------
    def set_audio(self, mono, rate):
        self.mono = None if mono is None else np.ascontiguousarray(mono, dtype=np.float32)
        self.rate = rate
        self.duration = 0.0 if mono is None else len(mono) / rate
        self.view = (0.0, max(self.duration, MIN_VIEW_S))
        self.vzoom = 1.0
        self.sel = None
        self.playhead = None
        self.update()
        self.viewChanged.emit(*self.view)

    def set_selection(self, sel, emit=True):
        if sel is not None:
            a, b = sorted((max(0.0, min(self.duration, sel[0])), max(0.0, min(self.duration, sel[1]))))
            sel = None if b - a <= 0 else (a, b)
        self.sel = sel
        self.update()
        if emit:
            self.selectionChanged.emit(sel)

    def set_playhead(self, t):
        self.playhead = t
        if t is not None and self.duration and not (self.view[0] <= t <= self.view[1]):
            span = self.view[1] - self.view[0]
            self.set_view(t - span * 0.1, t + span * 0.9)
        self.update()

    def set_view(self, a, b):
        d = max(self.duration, MIN_VIEW_S)
        span = min(d, max(MIN_VIEW_S, b - a))
        a = max(0.0, min(d - span, a))
        self.view = (a, a + span)
        self.update()
        self.viewChanged.emit(*self.view)

    def zoom_at(self, t, k):
        a, b = self.view
        self.set_view(t - (t - a) * k, t + (b - t) * k)

    def zoom_all(self):
        self.set_view(0, self.duration)

    def zoom_selection(self):
        if self.sel:
            pad = (self.sel[1] - self.sel[0]) * 0.1
            self.set_view(self.sel[0] - pad, self.sel[1] + pad)

    def set_vzoom(self, z):
        self.vzoom = max(1.0, min(16.0, z))
        self.update()

    # ---------- 좌표 ----------
    def _plot_rect(self):
        return QRectF(GUTTER, 0, max(1, self.width() - GUTTER), max(1, self.height() - RULER_H))

    def x_of(self, t):
        r = self._plot_rect()
        a, b = self.view
        return r.left() + (t - a) / (b - a) * r.width()

    def t_of(self, x):
        r = self._plot_rect()
        a, b = self.view
        return max(0.0, min(self.duration, a + (x - r.left()) / r.width() * (b - a)))

    # ---------- 그리기 ----------
    def paintEvent(self, _):
        p = QPainter(self)
        r = self._plot_rect()
        p.fillRect(self.rect(), WAVE["bg"])
        mid = r.center().y()
        amp = r.height() / 2 * 0.94
        p.setFont(self._font)
        # 진폭 격자·눈금
        step_px = amp / 10
        stride = next((s for s in (1, 2, 5, 10) if s * step_px >= 16), 10)
        for i in range(-9, 10):
            y = mid - i / 10 * amp
            p.setPen(QPen(WAVE["zero"] if i == 0 else WAVE["grid_mid"] if i % 5 == 0 else WAVE["grid"], 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            if i % stride == 0:
                p.setPen(WAVE["label"])
                p.drawText(QRectF(0, y - 8, GUTTER - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{i / 10 / self.vzoom:.2g}")
        a, b = self.view
        tstep = nice_step((b - a) / max(1, r.width() / 80))
        p.setPen(QPen(WAVE["grid"], 1))
        t = math.ceil(a / tstep) * tstep
        while t <= b:
            x = self.x_of(t)
            p.drawLine(QPointF(x, 0), QPointF(x, r.bottom()))
            t += tstep
        # 선택 구간
        if self.sel:
            x0, x1 = self.x_of(self.sel[0]), self.x_of(self.sel[1])
            p.fillRect(QRectF(x0, 0, x1 - x0, r.height()), WAVE["sel"])
        # 파형
        if self.mono is not None and len(self.mono):
            w = int(r.width())
            pk = peaks(self.mono, self.rate, a, b, w)
            if pk is not None:
                lo, hi, valid = pk
                p.setPen(QPen(WAVE["wave"], 1))
                idx = np.where(valid)[0]
                y0 = mid - np.minimum(1.0, hi[idx] * self.vzoom) * amp
                y1 = mid - np.maximum(-1.0, lo[idx] * self.vzoom) * amp + 1
                xs = r.left() + idx + 0.5
                p.drawLines([QLineF(x, a0, x, a1) for x, a0, a1 in zip(xs.tolist(), y0.tolist(), y1.tolist())])
        # 마크 경계 + "시작"/"끝" 글자 탭 (색만으로 구분하지 않음)
        if self.sel and self.selectable:
            p.setFont(self._mark_font)
            fm = p.fontMetrics()
            for t, label, d in ((self.sel[0], "시작", 1), (self.sel[1], "끝", -1)):
                x = self.x_of(t)
                p.setPen(QPen(WAVE["mark"], 2))
                p.drawLine(QPointF(x, 0), QPointF(x, r.bottom()))
                tw = fm.horizontalAdvance(label) + 10
                bx = x if d > 0 else x - tw
                p.fillRect(QRectF(bx, 0, tw, 18), WAVE["mark"])
                p.setPen(WAVE["play"])
                p.drawText(QRectF(bx, 0, tw, 18), Qt.AlignmentFlag.AlignCenter, label)
            p.setFont(self._font)
        # 재생 위치
        if self.playhead is not None and a <= self.playhead <= b:
            x = self.x_of(self.playhead)
            p.setPen(QPen(WAVE["play"], 1.5))
            p.drawLine(QPointF(x, 0), QPointF(x, r.bottom()))
        # 시간 눈금자
        ry = r.bottom()
        p.fillRect(QRectF(0, ry, self.width(), RULER_H), WAVE["bg"])
        p.setPen(QPen(WAVE["grid_mid"], 1))
        p.drawLine(QPointF(0, ry), QPointF(self.width(), ry))
        minor = tstep / 5
        t = math.ceil(a / minor) * minor
        while t <= b + 1e-9:
            x = self.x_of(t)
            major = abs(t / tstep - round(t / tstep)) < 1e-6
            p.setPen(QPen(WAVE["zero"], 1))
            p.drawLine(QPointF(x, ry), QPointF(x, ry + (7 if major else 3)))
            if major:
                p.setPen(WAVE["label"])
                p.drawText(QPointF(x + 2, ry + RULER_H - 5), fmt_time(t, tstep))
            t += minor
        p.end()

    # ---------- 마우스 ----------
    def _edge_at(self, x):
        if not self.sel or not self.selectable:
            return None
        if abs(x - self.x_of(self.sel[0])) <= EDGE_PX:
            return 0
        if abs(x - self.x_of(self.sel[1])) <= EDGE_PX:
            return 1
        return None

    def mousePressEvent(self, e):
        if self.mono is None or e.button() != Qt.MouseButton.LeftButton:
            return
        x = e.position().x()
        edge = self._edge_at(x)
        self._drag = {"edge": edge} if edge is not None else {"from": self.t_of(x), "moved": False}

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag is None:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._edge_at(x) is not None else Qt.CursorShape.IBeamCursor)
            return
        t = self.t_of(x)
        if self._drag.get("edge") is not None:
            s = list(self.sel)
            s[self._drag["edge"]] = t
            if s[0] > s[1]:
                self._drag["edge"] = 1 - self._drag["edge"]
            self.set_selection((s[0], s[1]))
        elif self.selectable:
            if abs(t - self._drag["from"]) > (self.view[1] - self.view[0]) / 400:
                self._drag["moved"] = True
                self.set_selection((self._drag["from"], t))

    def mouseReleaseEvent(self, e):
        if self._drag is not None and self._drag.get("edge") is None and not self._drag.get("moved"):
            self.playhead = self._drag["from"]
            self.update()
            self.playheadMoved.emit(self.playhead)
        self._drag = None

    def wheelEvent(self, e):
        if self.mono is None:
            return
        k = 0.7 if e.angleDelta().y() > 0 else 1 / 0.7
        self.zoom_at(self.t_of(e.position().x()), k)


class OverviewBar(QWidget):
    """전체 보기 띠: 파일 전체 미니 파형 + 현재 보기 범위 사각형."""

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view_widget = view
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        view.viewChanged.connect(lambda *_: self.update())
        view.selectionChanged.connect(lambda *_: self.update())

    def _move_to(self, x):
        v = self.view_widget
        if not v.duration:
            return
        t = x / max(1, self.width()) * v.duration
        half = (v.view[1] - v.view[0]) / 2
        v.set_view(t - half, t + half)

    def mousePressEvent(self, e):
        self._move_to(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._move_to(e.position().x())

    def paintEvent(self, _):
        v = self.view_widget
        p = QPainter(self)
        p.fillRect(self.rect(), WAVE["overview_bg"])
        p.setPen(QPen(WAVE["grid_mid"], 1))
        p.drawLine(0, 0, self.width(), 0)
        if v.mono is None or not v.duration:
            p.end()
            return
        w, h = self.width(), self.height()
        mid = h / 2
        X = lambda t: t / v.duration * w
        if v.sel:
            p.fillRect(QRectF(X(v.sel[0]), 0, X(v.sel[1]) - X(v.sel[0]), h), WAVE["sel"])
        pk = peaks(v.mono, v.rate, 0, v.duration, w)
        if pk is not None:
            lo, hi, valid = pk
            p.setPen(QPen(WAVE["wave"], 1))
            idx = np.where(valid)[0]
            y0 = mid - np.minimum(1.0, hi[idx] * 1.6) * mid * 0.9
            y1 = mid - np.maximum(-1.0, lo[idx] * 1.6) * mid * 0.9 + 1
            p.drawLines([QLineF(x + 0.5, a0, x + 0.5, a1) for x, a0, a1 in zip(idx.tolist(), y0.tolist(), y1.tolist())])
        if v.sel:
            p.setPen(QPen(WAVE["mark"], 1.5))
            for t in v.sel:
                p.drawLine(QPointF(X(t), 0), QPointF(X(t), h))
        p.setPen(QPen(WAVE["view_box"], 1.5))
        a, b = X(v.view[0]), X(v.view[1])
        p.drawRect(QRectF(a + 1, 1, max(3, b - a - 2), h - 2))
        if v.playhead is not None:
            p.setPen(QPen(WAVE["play"], 1))
            p.drawLine(QPointF(X(v.playhead), 0), QPointF(X(v.playhead), h))
        p.end()
