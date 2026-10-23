"""WaveText 메인 창: 4분할(① 원본 파형, ② 원본 인식 결과, ③ 학습용 Wave, ④ 학습용 Text) + 파일 목록 서랍 + 상태줄."""
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QDoubleValidator, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap,
                           QShortcut)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from wavetext import __version__, config, paths
from wavetext.audio import convert, denoise, segment, wavio
from wavetext.paths import Folders
from wavetext.stt import client
from wavetext.ui.dialogs import FolderDialog, HelpDialog, SettingsDialog
from wavetext.ui.player import Player
from wavetext.ui.waveform import OverviewBar, WaveformView
from wavetext.ui.workers import SttWorker, TaskWorker


class TimeEdit(QLineEdit):
    """초 단위 숫자 입력칸(0.001초). QDoubleSpinBox는 PySide6 6.12에서 화살표를 없애고 스타일을 입히면
    글자 영역이 1px로 줄어 숫자가 가려지므로, 숫자만 받는 입력칸으로 대신한다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lo, self._hi = 0.0, 0.0
        self._validator = QDoubleValidator(0.0, 0.0, 3, self)
        self._validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.setValidator(self._validator)
        self.setValue(0.0)

    def setRange(self, lo, hi):
        self._lo, self._hi = float(lo), float(hi)
        self._validator.setRange(self._lo, self._hi, 3)

    def value(self):
        try:
            return max(self._lo, min(self._hi, float(self.text())))
        except ValueError:
            return self._lo

    def setValue(self, v):
        self.setText(f"{float(v):.3f}")


def fmt_lcd(t):
    t = max(0.0, t or 0.0)
    return f"{int(t // 3600):02d}:{int(t // 60) % 60:02d}:{t % 60:04.1f}"


def set_badge(label, kind, text):
    """배지: 색과 함께 기호를 붙여 상태를 표시한다 (NFR-06)."""
    sym = {"ok": "✓ ", "warn": "● ", "edit": "✎ ", "err": "✕ "}.get(kind, "")
    label.setText(sym + text)
    label.setProperty("badge", kind)
    label.style().unpolish(label)
    label.style().polish(label)


@dataclass
class Pair:
    n: int
    wav: Path
    txt: Path
    after: np.ndarray            # 저장된(노이즈 제거 후) 샘플
    before: np.ndarray | None    # 같은 구간 원음 (이번 작업에서 만든 쌍만)
    rate: int
    t0: float | None = None
    t1: float | None = None
    text: str = ""
    state: str = "ok"            # recognizing / ok / err
    error: str = ""
    nr: int = 2
    deleted: bool = False
    rerun: tuple | None = None   # 다시 인식 중이면 (이전 텍스트, 상태, 오류)
    worker: object = field(default=None, repr=False)

    @property
    def duration(self):
        return len(self.after) / self.rate


class MainWindow(QMainWindow):
    def __init__(self, cfg, ui_font="Malgun Gothic", mono="Consolas", stt_factory=None):
        super().__init__()
        self.cfg = cfg
        self.mono_family = mono
        self.stt_factory = stt_factory or SttWorker   # 시험에서 가짜 작업자 주입
        self.folders = Folders(**cfg.get("folders", {}))
        self.sources = []
        self.infos = {}
        self.counts = {}
        self.cur = -1
        self.audio = None
        self.profile = None
        self.pairs = []
        self.meta = {"words": [], "parts": {}}  # 현재 원본의 단어 시간·분할 구간 기록 (stt.json)
        self.cur_pair = -1
        self._src_dirty = False
        self._out_dirty = False
        self.show_before = False
        self.src_worker = None
        self._workers = set()
        self._pending = {}      # 인식 중인 쌍: str(wav 경로) → Pair (파일을 바꿨다 돌아와도 같은 객체를 쓴다)
        self._creating = False  # 노이즈 제거·저장 작업 중
        self.player = Player(self)
        self.player.position.connect(self._on_play_pos)
        self.player.stopped.connect(self._on_play_stop)
        self.player.error.connect(lambda m: self.message(m, error=True))
        self.setWindowTitle(f"WaveText {__version__} — 음성학습용 Wave/Text 생성기")
        self.resize(1600, 960)
        self.setMinimumSize(1360, 720)  # 1440×900 화면(작업 표시줄 제외 약 850px)에 들어가도록 (M-4)
        self._build()
        self._load_stt_label()
        self._update_controls()
        QTimer.singleShot(0, self._startup)

    # 수정 상태: 바뀔 때마다 ②④ [저장] 버튼을 켜고 끈다 (편집하거나 문장을 가져오면 활성화)
    @property
    def src_dirty(self):
        return self._src_dirty

    @src_dirty.setter
    def src_dirty(self, v):
        self._src_dirty = bool(v)
        if hasattr(self, "btn_src_save"):
            self.btn_src_save.setEnabled(self._src_dirty)

    @property
    def out_dirty(self):
        return self._out_dirty

    @out_dirty.setter
    def out_dirty(self, v):
        self._out_dirty = bool(v)
        if hasattr(self, "btn_out_save"):
            self.btn_out_save.setEnabled(self._out_dirty)

    # ================= 화면 구성 =================
    def _build(self):
        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._build_menu()
        root.addWidget(self._build_toolbar())
        self.work = QWidget()
        grid = QGridLayout(self.work)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)
        grid.addWidget(self._build_src_pane(), 0, 0)
        grid.addWidget(self._build_stt_pane(), 0, 1)
        grid.addWidget(self._build_out_pane(), 1, 0)
        grid.addWidget(self._build_txt_pane(), 1, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 2)
        root.addWidget(self.work, 1)
        root.addWidget(self._build_status())
        self._build_drawer()

    def _act(self, menu, text, slot, key=None):
        a = QAction(text, self)
        if key:
            a.setShortcut(QKeySequence(key))
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        a.triggered.connect(slot)
        menu.addAction(a)
        self.addAction(a)
        return a

    def _build_menu(self):
        mb = self.menuBar()
        m = mb.addMenu("파일(&F)")
        self._act(m, "음성 폴더 열기…", self.choose_source)
        self._act(m, "저장 폴더 설정…", self.open_folder_dialog, "Ctrl+O")
        m.addSeparator()
        self._act(m, "이전 파일", lambda: self.step_file(-1), "PgUp")
        self._act(m, "다음 파일", lambda: self.step_file(1), "PgDown")
        self._act(m, "파일 목록 보기", self.toggle_drawer, "Ctrl+L")
        m.addSeparator()
        self._act(m, "끝내기", self.close, "Alt+F4")
        m = mb.addMenu("편집(&E)")
        self._act(m, "시작 마크", self.mark_start, "[")
        self._act(m, "끝 마크", self.mark_end, "]")
        self._act(m, "마크 해제", lambda: self.wave.set_selection(None), "Esc")
        self._act(m, "전체 선택", lambda: self.wave.set_selection((0, self.wave.duration)), "Ctrl+A")
        m.addSeparator()
        self.act_create = self._act(m, "학습용 파일 생성하기", self.create_pair, "Ctrl+Return")
        self._act(m, "선택한 학습용 파일 쌍 삭제", self.delete_pair, "Del")
        m = mb.addMenu("보기(&V)")
        self._act(m, "확대", lambda: self._zoom(0.6), "Shift+Up")
        self._act(m, "축소", lambda: self._zoom(1 / 0.6), "Shift+Down")
        self._act(m, "선택 구간 맞춤", lambda: self.wave.zoom_selection(), "Shift+S")
        self._act(m, "전체 보기", lambda: self.wave.zoom_all(), "Shift+A")
        m.addSeparator()
        self._act(m, "세로 확대", lambda: self.wave.set_vzoom(self.wave.vzoom * 1.5), "Ctrl+Up")
        self._act(m, "세로 축소", lambda: self.wave.set_vzoom(self.wave.vzoom / 1.5), "Ctrl+Down")
        m = mb.addMenu("재생(&P)")
        self._act(m, "원본 재생 / 일시정지", self.toggle_src_play, "Space")
        self._act(m, "선택 구간 재생", self.play_selection, "Shift+Space")
        self._act(m, "학습용 파일 재생", self.play_out, "Ctrl+Space")
        self._act(m, "정지", self.player.stop)
        self._act(m, "재생 위치 0.1초 뒤로", lambda: self._nudge(-0.1), "Left")
        self._act(m, "재생 위치 0.1초 앞으로", lambda: self._nudge(0.1), "Right")
        self._act(m, "재생 위치 1초 뒤로", lambda: self._nudge(-1), "Shift+Left")
        self._act(m, "재생 위치 1초 앞으로", lambda: self._nudge(1), "Shift+Right")
        m = mb.addMenu("음성인식(&R)")
        self._act(m, "원본 음성인식 실행", lambda: self.run_src_stt(False), "F5")
        self._act(m, "선택 구간만 인식", lambda: self.run_src_stt(True), "Shift+F5")
        self._act(m, "인식 중지", self.stop_src_stt)
        m.addSeparator()
        self._act(m, "학습용 파일 다시 인식", self.run_out_stt, "Ctrl+F5")
        m = mb.addMenu("설정(&O)")
        self._act(m, "음성인식 서버 설정…", self.open_settings)  # 저장 폴더 설정은 파일 메뉴에만 둔다
        m = mb.addMenu("도움말(&H)")
        self._act(m, "단축키 안내", lambda: HelpDialog(self).exec(), "F1")
        self._act(m, "WaveText 정보", lambda: QMessageBox.information(self, "WaveText 정보", f"WaveText {__version__}\n음성학습용 Wave/Text 생성기"))
        # Ctrl+S: 숨긴 메뉴 항목은 비활성화되어 단축키가 동작하지 않으므로 QShortcut을 쓴다 (M-1)
        self.sc_save = QShortcut(QKeySequence.StandardKey.Save, self)
        self.sc_save.setContext(Qt.ShortcutContext.WindowShortcut)
        self.sc_save.activated.connect(lambda: self.save_text(None))
        # 메뉴줄 오른쪽: 이전/현재/다음
        nav = self._nav = QWidget(mb)  # 참조를 유지해야 Python이 위젯을 지우지 않는다
        h = QHBoxLayout(nav)
        h.setContentsMargins(0, 2, 8, 2)
        h.setSpacing(8)
        self.btn_prev = QPushButton("◀ 이전")
        self.btn_prev.setToolTip("이전 파일 (PgUp)")
        self.btn_prev.clicked.connect(lambda: self.step_file(-1))
        self.lbl_cur = QLabel("파일 없음")
        self.lbl_cur.setMinimumWidth(360)
        self.lbl_cur.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cur.setStyleSheet("font-weight:700; padding:0 12px; background:#f4f5f6; border:1px solid #e6e8ea; border-radius:6px;")
        self.btn_next = QPushButton("다음 ▶")
        self.btn_next.setToolTip("다음 파일 (PgDn)")
        self.btn_next.clicked.connect(lambda: self.step_file(1))
        for w in (self.btn_prev, self.lbl_cur, self.btn_next):
            h.addWidget(w)
        mb.setCornerWidget(nav, Qt.Corner.TopRightCorner)

    def _icon(self, sym, color="#464c53"):
        """기호 글자를 22px 아이콘으로 그린다 (이미지 파일 없이 폐쇄망 배포)."""
        pm = QPixmap(QSize(28, 28) * self.devicePixelRatioF())
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        f = p.font()
        f.setPixelSize(20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(QRect(0, 0, 28, 28), Qt.AlignmentFlag.AlignCenter, sym)
        p.end()
        return QIcon(pm)

    def _tb(self, sym, label, slot, tip, color="#464c53"):
        b = QToolButton(objectName="tb")
        b.setIcon(self._icon(sym, color))
        b.setIconSize(QSize(24, 24))
        b.setText(label)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(slot)
        return b

    def _sep(self):
        s = QFrame(objectName="sep")
        s.setFixedHeight(32)
        return s

    def _build_toolbar(self):
        bar = QWidget(objectName="toolbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 6, 16, 6)
        h.setSpacing(4)
        h.addWidget(self._tb("📂", "음성 폴더", self.choose_source, "원본 음성 폴더 열기"))
        h.addWidget(self._tb("📁", "저장 폴더", self.open_folder_dialog, "원본 음성 폴더와 저장 폴더 설정 (Ctrl+O)"))
        h.addWidget(self._tb("☰", "파일 목록", self.toggle_drawer, "파일 목록 (Ctrl+L)"))
        h.addWidget(self._sep())
        h.addWidget(self._tb("▶", "재생", self.toggle_src_play, "원본 재생 / 일시정지 (Space)", "#256ef4"))
        h.addWidget(self._tb("▷", "구간 재생", self.play_selection, "선택 구간 재생 (Shift+Space)", "#256ef4"))
        h.addWidget(self._tb("■", "정지", self.player.stop, "정지"))
        self.lcd = QLabel("00:00:00.0", objectName="lcd")
        h.addWidget(self.lcd)
        h.addWidget(self._sep())
        h.addWidget(self._tb("[", "시작 마크", self.mark_start, "재생 위치를 시작으로 ( [ )", "#8a5c00"))
        h.addWidget(self._tb("]", "끝 마크", self.mark_end, "재생 위치를 끝으로 ( ] )", "#8a5c00"))
        h.addWidget(self._tb("✕", "마크 해제", lambda: self.wave.set_selection(None), "마크 해제 (Esc)"))
        h.addWidget(self._sep())
        h.addWidget(self._tb("＋", "확대", lambda: self._zoom(0.6), "확대 (Shift+↑)"))
        h.addWidget(self._tb("－", "축소", lambda: self._zoom(1 / 0.6), "축소 (Shift+↓)"))
        h.addWidget(self._tb("⤢", "구간 맞춤", lambda: self.wave.zoom_selection(), "선택 구간 맞춤 (Shift+S)"))
        h.addWidget(self._tb("⇔", "전체", lambda: self.wave.zoom_all(), "전체 보기 (Shift+A)"))
        h.addWidget(self._sep())
        nr = QVBoxLayout()
        nr.setSpacing(2)
        lab = QLabel("노이즈 제거")
        lab.setStyleSheet("font-size:13px; color:#464c53;")
        self.nr = QComboBox()
        self.nr.addItems([denoise.LEVEL_NAMES[i] for i in range(4)])
        self.nr.setCurrentIndex(int(self.cfg.get("nr_level", 2)))
        self.nr.currentIndexChanged.connect(self._nr_changed)
        nr.addWidget(lab)
        nr.addWidget(self.nr)
        h.addLayout(nr)
        self.btn_create = QPushButton("학습용 파일 생성하기", objectName="create")
        self.btn_create.setProperty("kind", "primary")
        self.btn_create.clicked.connect(self.create_pair)
        h.addSpacing(8)
        h.addWidget(self.btn_create)
        h.addStretch(1)
        return bar

    def _pane(self, no, title):
        f = QFrame(objectName="pane")
        v = QVBoxLayout(f)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(0)
        head = QWidget(objectName="paneHead")
        hh = QHBoxLayout(head)
        hh.setContentsMargins(12, 6, 12, 6)
        hh.setSpacing(8)
        n = QLabel(str(no), objectName="paneNo")
        n.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hh.addWidget(n)
        hh.addWidget(QLabel(title, objectName="paneTitle"))
        v.addWidget(head)
        foot = QWidget(objectName="paneFoot")
        foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        fh = QHBoxLayout(foot)
        fh.setContentsMargins(12, 4, 12, 4)
        fh.setSpacing(12)
        return f, v, hh, foot, fh

    def _sub(self, text=""):
        lab = QLabel(text, objectName="paneSub")
        lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return lab

    def _build_src_pane(self):
        f, v, hh, foot, fh = self._pane(1, "원본 파형")
        self.src_sub = self._sub()
        hh.addWidget(self.src_sub, 1)
        self.view_badge = QLabel()
        set_badge(self.view_badge, "none", "보기 -")
        hh.addWidget(self.view_badge)
        self.btn_src_play = QPushButton("▶ 원본 재생")
        self.btn_src_play.setProperty("kind", "primary")
        self.btn_src_play.setToolTip("원본 재생 / 일시정지 (Space)")
        self.btn_src_play.clicked.connect(self.toggle_src_play)
        b2 = QPushButton("구간 재생")
        b2.setToolTip("선택 구간만 재생 (Shift+Space)")
        b2.clicked.connect(self.play_selection)
        b3 = QPushButton("■ 정지")
        b3.clicked.connect(self.player.stop)
        for b in (self.btn_src_play, b2, b3):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            hh.addWidget(b)
        self.wave = WaveformView(mono_family=self.mono_family)
        self.wave.selectionChanged.connect(self._on_selection)
        self.wave.playheadMoved.connect(lambda t: self.lcd.setText(fmt_lcd(t)))
        self.wave.viewChanged.connect(lambda a, b: set_badge(self.view_badge, "none", f"보기 {a:.3f} ~ {b:.3f}초"))
        v.addWidget(self.wave, 1)
        v.addWidget(OverviewBar(self.wave))
        fh.addWidget(QLabel("선택 구간"))
        self.sp_start = self._spin()
        self.sp_end = self._spin()
        fh.addWidget(QLabel("시작"))
        fh.addWidget(self.sp_start)
        fh.addWidget(QLabel("초  끝"))
        fh.addWidget(self.sp_end)
        fh.addWidget(QLabel("초"))
        self.lbl_len = QLabel("길이 -")
        fh.addWidget(self.lbl_len)
        fh.addStretch(1)
        hint = QLabel("끌어서 구간 선택 · 노란 경계선을 끌어 미세 조정 · 휠로 확대/축소")
        fh.addWidget(hint)
        v.addWidget(foot)
        return f

    def _spin(self):
        s = TimeEdit()
        s.setFixedWidth(104)
        s.editingFinished.connect(self._spin_changed)
        return s

    def _build_stt_pane(self):
        f, v, hh, foot, fh = self._pane(2, "원본 음성인식 결과")
        self.src_badge = QLabel()
        set_badge(self.src_badge, "none", "미실행")
        hh.addWidget(self.src_badge)
        hh.addStretch(1)
        self.btn_recog = QPushButton("음성인식 실행")
        self.btn_recog.setProperty("kind", "primary")
        self.btn_recog.setToolTip("음성인식 서버로 원본 전체 인식, 결과 자동 저장 (F5)")
        self.btn_recog.clicked.connect(lambda: self.run_src_stt(False))
        self.btn_src_save = QPushButton("저장")
        self.btn_src_save.setProperty("kind", "primary")
        self.btn_src_save.setToolTip("수정한 원본 인식 결과 저장 (Ctrl+S). 인식 결과는 자동 저장됩니다")
        self.btn_src_save.clicked.connect(lambda: self.save_text("src"))
        self.btn_src_save.setEnabled(False)
        hh.addWidget(self.btn_recog)
        hh.addWidget(self.btn_src_save)
        self.src_prog = QProgressBar(textVisible=False)
        self.src_prog.setRange(0, 1000)
        v.addWidget(self.src_prog)
        self.src_text = QPlainTextEdit(objectName="stt")
        self.src_text.setPlaceholderText("[음성인식 실행]을 누르면 음성인식 서버의 인식 결과가 여기에 표시되고 자동 저장됩니다. 직접 수정하고 저장할 수 있습니다.")
        self.src_text.textChanged.connect(self._src_edited)
        v.addWidget(self.src_text, 1)
        self.lbl_server = QLabel()
        self.lbl_src_info = QLabel()
        self.lbl_full_dir = QLabel()
        self.lbl_full_dir.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        fh.addWidget(self.lbl_server)
        fh.addWidget(self.lbl_src_info)
        fh.addWidget(self.lbl_full_dir, 1)
        v.addWidget(foot)
        return f

    def _build_out_pane(self):
        f, v, hh, foot, fh = self._pane(3, "생성된 학습용 Wave")
        self.out_sub = self._sub("아직 생성된 파일이 없습니다")
        hh.addWidget(self.out_sub, 1)
        self.tg_after = QPushButton("노이즈 제거 후")
        self.tg_before = QPushButton("원음")
        self.tg_after.setToolTip("저장된 파일(노이즈 제거 후)의 파형과 소리")
        self.tg_before.setToolTip("같은 구간을 노이즈 제거 없이 자른 원음 (파일로 저장하지 않음)")
        grp = QButtonGroup(self)
        for b in (self.tg_after, self.tg_before):
            b.setCheckable(True)
            b.setProperty("kind", "toggle")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            grp.addButton(b)
        self.tg_after.setChecked(True)
        self.tg_after.toggled.connect(self._toggle_before)
        tg = QHBoxLayout()
        tg.setSpacing(0)
        tg.addWidget(self.tg_after)
        tg.addWidget(self.tg_before)
        hh.addLayout(tg)
        self.btn_out_play = QPushButton("▶ 재생")
        self.btn_out_play.setToolTip("학습용 파일 재생 (Ctrl+Space)")
        self.btn_out_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_out_play.clicked.connect(self.play_out)
        hh.addWidget(self.btn_out_play)
        chips = QWidget(objectName="chips")
        self.chip_box = QHBoxLayout(chips)
        self.chip_box.setContentsMargins(12, 6, 12, 6)
        self.chip_box.setSpacing(8)
        self.chip_box.addStretch(1)
        sc = QScrollArea()
        sc.setWidget(chips)
        sc.setWidgetResizable(True)
        sc.setFixedHeight(46)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.addWidget(sc)
        self.chip_group = QButtonGroup(self)
        self.out_stack = QStackedWidget()
        empty = QLabel("①에서 음성이 명료한 구간을 선택하고\n[학습용 파일 생성하기]를 누르세요", objectName="empty")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.out_wave = WaveformView(selectable=False, mono_family=self.mono_family)
        self.out_stack.addWidget(empty)
        self.out_stack.addWidget(self.out_wave)
        v.addWidget(self.out_stack, 1)
        self.lbl_out_path = QLabel()
        self.lbl_out_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_out_fmt = QLabel()
        fh.addWidget(self.lbl_out_path, 1)
        fh.addWidget(self.lbl_out_fmt)
        v.addWidget(foot)
        return f

    def _build_txt_pane(self):
        f, v, hh, foot, fh = self._pane(4, "학습용 Text")
        self.out_badge = QLabel()
        set_badge(self.out_badge, "none", "-")
        hh.addWidget(self.out_badge)
        hh.addStretch(1)
        self.btn_out_recog = b0 = QPushButton("음성인식 실행")
        b0.setProperty("kind", "primary")
        b0.setToolTip("선택한 분할 음성파일(part-N.wav)을 다시 인식해 stt-N.txt에 자동 저장 (Ctrl+F5)")
        b0.clicked.connect(self.run_out_stt)
        b1 = QPushButton("문장 전체 가져오기")
        b1.setToolTip("② 원본 음성인식 결과 전체를 가져옵니다 (가져온 뒤 [저장])")
        b1.clicked.connect(self.copy_from_src)
        self.btn_out_part = bp = QPushButton("선택 구간 문장 가져오기")
        bp.setToolTip("③에서 선택한 학습용 Wave 구간에 해당하는 ② 문장만 가져옵니다 (가져온 뒤 [저장])")
        bp.clicked.connect(self.copy_part_from_src)
        self.btn_out_save = b2 = QPushButton("저장")
        b2.setProperty("kind", "primary")
        b2.setToolTip("수정한 학습용 텍스트 저장 (Ctrl+S)")
        b2.clicked.connect(lambda: self.save_text("out"))
        b2.setEnabled(False)
        hh.addWidget(b1)
        hh.addWidget(bp)
        hh.addWidget(b0)
        hh.addWidget(b2)
        self.out_prog = QProgressBar(textVisible=False)
        self.out_prog.setRange(0, 1000)
        v.addWidget(self.out_prog)
        self.out_text = QPlainTextEdit(objectName="stt")
        self.out_text.setPlaceholderText("학습용 파일을 생성하면 잘라낸 음성을 자동으로 음성인식하여 여기에 표시합니다. 들어보고 틀린 부분을 고친 뒤 저장하세요.")
        self.out_text.setEnabled(False)
        self.out_text.textChanged.connect(self._out_edited)
        v.addWidget(self.out_text, 1)
        self.lbl_txt_path = QLabel("-")
        self.lbl_txt_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        fh.addWidget(self.lbl_txt_path, 1)
        fh.addWidget(QLabel("UTF-8 · 한 줄 전사"))
        v.addWidget(foot)
        return f

    def _build_status(self):
        box = QWidget(objectName="statusBox")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        def row(*specs):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 3, 0, 3)
            h.setSpacing(0)
            out = []
            for width, name in specs:
                lab = QLabel()
                if name:
                    lab.setObjectName(name)
                if width:
                    lab.setFixedWidth(width)
                else:
                    lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                h.addWidget(lab, 0 if width else 1)
                out.append(lab)
            v.addWidget(w)
            return out

        self.st_ch, self.st_dur1, self.st_sel, self.st_msg = row((80, None), (90, "num"), (260, "num"), (0, None))
        self.st_orig, self.st_dur2, self.st_fmt, self.st_prog = row((80, None), (90, "num"), (440, "fmt"), (0, None))
        self.st_orig.setText("원본")
        self.st_dur1.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.st_dur2.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return box

    def _build_drawer(self):
        d = QFrame(self.work, objectName="drawer")
        d.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        v = QVBoxLayout(d)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 8, 12, 8)
        head.addWidget(QLabel("파일 목록", objectName="paneTitle"))
        self.drawer_dir = self._sub()
        head.addWidget(self.drawer_dir, 1)
        close = QPushButton("닫기")
        close.clicked.connect(self.toggle_drawer)
        head.addWidget(close)
        v.addLayout(head)
        flt = QHBoxLayout()
        flt.setContentsMargins(12, 0, 12, 8)
        self.flt_text = QLineEdit(placeholderText="파일 이름 검색")
        self.flt_state = QComboBox()
        self.flt_state.addItems(["전체", "미처리", "생성 완료"])
        self.flt_text.textChanged.connect(self._filter_list)
        self.flt_state.currentIndexChanged.connect(self._filter_list)
        flt.addWidget(self.flt_text, 1)
        flt.addWidget(self.flt_state)
        v.addLayout(flt)
        self.file_list = QListWidget()
        self.file_list.itemActivated.connect(lambda it: self._open_from_list(it))
        self.file_list.itemClicked.connect(lambda it: self._open_from_list(it))
        v.addWidget(self.file_list, 1)
        self.drawer_sum = QLabel(objectName="hint")
        self.drawer_sum.setContentsMargins(12, 6, 12, 6)
        v.addWidget(self.drawer_sum)
        d.hide()
        self.drawer = d

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.drawer.setGeometry(8, 8, 420, max(200, self.work.height() - 16))

    # ================= 폴더·파일 =================
    def _startup(self):
        if self.folders.src and Path(self.folders.src).is_dir():
            self.open_folders(self.folders)
        else:
            self.message("[저장 폴더]에서 원본 음성 폴더를 지정하세요.")
            self.open_folder_dialog()

    def choose_source(self):
        d = QFileDialog.getExistingDirectory(self, "원본 음성 폴더 선택", self.folders.src or str(Path.home()))
        if d:
            f = Folders(str(Path(d)), self.folders.full, self.folders.wav, self.folders.part_txt)
            self.open_folders(f)

    def open_folder_dialog(self):
        dlg = FolderDialog(self.folders, self)
        if dlg.exec():
            self.open_folders(dlg.folders())

    def open_folders(self, folders):
        if not self.confirm_unsaved():
            return
        self.folders = folders
        self.cfg["folders"] = {"src": folders.src, "full": folders.full, "wav": folders.wav, "part_txt": folders.part_txt}
        self._save_cfg()
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.sources = paths.list_sources(folders.src)
            self.infos = {}
            for p in self.sources:  # 헤더만 읽는다 (NFR-03)
                try:
                    self.infos[p] = wavio.read_info(p)
                except (wavio.UnsupportedWav, OSError) as e:
                    self.infos[p] = str(e)
            self.counts = paths.pair_counts(folders)
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.drawer_dir.setText(folders.src)
        self._fill_list()
        self.cur = -1
        if not self.sources:
            self._clear_file()
            self.message(f"원본 음성 폴더에 .wav 파일이 없습니다: {folders.src}", error=True)
            return
        readable = [i for i, p in enumerate(self.sources) if isinstance(self.infos.get(p), wavio.WavInfo)]
        first = next((i for i in readable if not self.counts.get(self.sources[i].stem.lower())), readable[0] if readable else 0)
        self.load_index(first, ask=False)

    def _fill_list(self):
        self.file_list.clear()
        for i, p in enumerate(self.sources):
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.file_list.addItem(it)
            self._list_text(i)
        self._filter_list()

    def _list_text(self, i):
        it = self.file_list.item(i)
        if it is None:
            return
        p = self.sources[i]
        n = self.counts.get(p.stem.lower(), 0)
        info = self.infos.get(p)
        ok = isinstance(info, wavio.WavInfo)
        dur = f"{info.duration:.1f}초" if ok else "읽기 불가"
        it.setText(f"{'✔' if n else '○' if ok else '✕'}  {p.name}    {f'{n}쌍 · ' if n else ''}{dur}")
        it.setToolTip(wavio.describe(info) if ok else f"건너뜀: {info}")
        it.setData(Qt.ItemDataRole.UserRole + 1, n)

    def _filter_list(self):
        q = self.flt_text.text().strip().lower()
        mode = self.flt_state.currentIndex()
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            p = self.sources[i]
            n = it.data(Qt.ItemDataRole.UserRole + 1) or 0
            hide = (q and q not in p.name.lower()) or (mode == 1 and n) or (mode == 2 and not n)
            it.setHidden(bool(hide))
        done = sum(1 for p in self.sources if self.counts.get(p.stem.lower()))
        self.drawer_sum.setText(f"전체 {len(self.sources):,}개 · 생성 완료 {done:,}개 · 미처리 {len(self.sources) - done:,}개")

    def _open_from_list(self, it):
        i = it.data(Qt.ItemDataRole.UserRole)
        if i != self.cur:
            self.load_index(i)

    def toggle_drawer(self):
        self.drawer.setVisible(not self.drawer.isVisible())
        if self.drawer.isVisible():
            self.drawer.raise_()
            if 0 <= self.cur < self.file_list.count():
                self.file_list.setCurrentRow(self.cur)
                self.file_list.scrollToItem(self.file_list.item(self.cur))
            self.flt_text.setFocus()

    def step_file(self, d):
        """이전/다음 파일. 읽을 수 없는 파일은 건너뛴다 (NFR-05)."""
        n = len(self.sources)
        for k in range(1, n + 1):
            i = (self.cur + d * k) % n
            if isinstance(self.infos.get(self.sources[i]), wavio.WavInfo):
                if i != self.cur:
                    self.load_index(i)
                return
        self.message("읽을 수 있는 다른 파일이 없습니다.")

    def confirm_unsaved(self):
        if not (self.src_dirty or self.out_dirty):
            return True
        which = "②원본 인식 결과" if self.src_dirty else ""
        if self.out_dirty:
            which += (", " if which else "") + "④학습용 Text"
        r = QMessageBox.question(self, "저장하지 않은 텍스트", f"{which}를 수정했습니다. 저장할까요?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            if self.src_dirty and not self.save_text("src"):
                return False
            if self.out_dirty and not self.save_text("out"):
                return False
        self.src_dirty = self.out_dirty = False
        return True

    def _clear_file(self):
        """읽을 수 없는 파일·빈 폴더: 이전 파일 내용이 남지 않도록 ②~④를 모두 비운다 (m-1)."""
        self.audio = None
        self.wave.set_audio(None, 8000)
        self._spin_range()
        self.pairs = []
        self.meta = {"words": [], "parts": {}}
        self.cur_pair = -1
        self._render_chips()
        self._show_pair()
        self._set_src_text("")
        set_badge(self.src_badge, "none", "-")
        self.src_prog.setValue(0)
        self.lbl_src_info.setText("")
        self.src_dirty = self.out_dirty = False
        self.lbl_cur.setText("파일 없음")
        self._update_controls()

    def load_index(self, i, ask=True):
        if ask and not self.confirm_unsaved():
            return
        self.player.stop()
        self.stop_src_stt()
        self.cur = i
        p = self.sources[i]
        self.lbl_cur.setText(f"{p.name}  ({i + 1} / {len(self.sources):,})")
        self.setWindowTitle(f"WaveText {__version__} — {p}")
        self.src_sub.setText(p.name)
        self.file_list.setCurrentRow(i)
        try:
            self.audio = wavio.read(p)
        except (wavio.UnsupportedWav, OSError) as e:
            self._clear_file()
            self.lbl_cur.setText(f"{p.name}  ({i + 1} / {len(self.sources):,})")
            self.message(f"{p.name}: 읽을 수 없는 파일입니다 ({e}). 다음 파일로 넘어가세요.", error=True)
            self._update_status()
            return
        self.profile = None
        self.wave.set_audio(self.audio.mono(), self.audio.rate)
        self.lcd.setText(fmt_lcd(0))
        self._spin_range()
        # ② 기존 원본 인식 결과
        text = paths.read_text(paths.full_stt_path(self.folders, p.stem))
        self._set_src_text(text or "")
        set_badge(self.src_badge, "ok" if text is not None else "none", "저장됨" if text is not None else "미실행")
        self.src_prog.setValue(0)
        self.lbl_src_info.setText("")
        self.meta = paths.load_meta(self.folders, p.stem)  # 원본 인식 단어 시간 + 분할 구간
        # ③④ 기존 학습용 쌍
        self.pairs = []
        for n in sorted(paths.existing_numbers(self.folders, p.stem)):
            pair = self._load_pair(p.stem, n)
            if pair:
                self.pairs.append(pair)
        self.cur_pair = len(self.pairs) - 1
        self._render_chips()
        self._show_pair()
        self._update_status()
        self._update_controls()
        self.message(f"{p.name} 불러옴" + (f" · 기존 학습용 파일 {len(self.pairs)}쌍" if self.pairs else ""))

    def _load_pair(self, base, n):
        wav = paths.part_wav_path(self.folders, base, n)
        txt = paths.part_txt_path(self.folders, base, n)
        if str(wav) in self._pending:  # 아직 인식 중인 쌍은 같은 객체를 다시 쓴다 (M-2)
            return self._pending[str(wav)]
        samples, rate = np.zeros((0, 1), np.float32), self.audio.rate
        if wav.exists():
            try:
                a = wavio.read(wav)
                samples, rate = a.samples, a.rate
            except (wavio.UnsupportedWav, OSError):
                pass
        text = paths.read_text(txt)
        span = self.meta.get("parts", {}).get(str(n))
        t0, t1, before = None, None, None
        if span and len(span) == 2:  # 구간 기록이 있으면 원본 구간 표시·원음 비교를 되살린다
            t0, t1 = float(span[0]), float(span[1])
            before = segment.fade(segment.cut(self.audio.samples, self.audio.rate, t0, t1), self.audio.rate)
        return Pair(n, wav, txt, samples, before, rate, t0, t1, text=text or "", state="ok" if text is not None else "err",
                    error="" if text is not None else "텍스트 파일 없음")

    # ================= 선택 구간 =================
    def _spin_range(self):
        d = self.wave.duration
        for s in (self.sp_start, self.sp_end):
            s.blockSignals(True)
            s.setRange(0, d)
            s.setValue(0)
            s.blockSignals(False)

    def _on_selection(self, sel):
        for s, v in ((self.sp_start, sel[0] if sel else 0), (self.sp_end, sel[1] if sel else 0)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        if sel:
            ln = sel[1] - sel[0]
            over = ln > segment.MAX_SEG_S
            self.lbl_len.setText(f"길이 {ln:.3f}초{' — 30초 초과' if over else ''} (최대 30초)")
            self.lbl_len.setObjectName("over" if over else "")
            self.lbl_len.style().unpolish(self.lbl_len)
            self.lbl_len.style().polish(self.lbl_len)
        else:
            self.lbl_len.setText("길이 - (최대 30초)")
            self.lbl_len.setObjectName("")
            self.lbl_len.style().unpolish(self.lbl_len)
            self.lbl_len.style().polish(self.lbl_len)
        self._update_status()
        self._update_controls()

    def _spin_changed(self):
        a, b = self.sp_start.value(), self.sp_end.value()
        if b > a:
            self.wave.set_selection((a, b))
        elif self.sp_start.hasFocus() or self.sp_end.hasFocus() or b != 0:
            self._on_selection(self.wave.sel)  # 입력칸을 실제 선택 값으로 되돌린다 (m-6)
            self.message("끝은 시작보다 커야 합니다.", error=True)

    def _cur_pos(self):
        if self.player.is_playing("src"):
            return self.player.current()
        return self.wave.playhead or 0.0

    def mark_start(self):
        if self.audio is None:
            return
        t = self._cur_pos()
        if t >= self.wave.duration - segment.MIN_SEG_S:  # 파일 끝에서는 앞 1초를 선택한다 (m-5)
            self.wave.set_selection((max(0.0, self.wave.duration - 1.0), self.wave.duration))
            return
        end = self.wave.sel[1] if self.wave.sel and self.wave.sel[1] > t + 0.05 else min(self.wave.duration, t + 1.0)
        self.wave.set_selection((t, end))

    def mark_end(self):
        if self.audio is None:
            return
        t = self._cur_pos()
        if t <= segment.MIN_SEG_S:  # 파일 처음에서는 뒤 1초를 선택한다
            self.wave.set_selection((0.0, min(self.wave.duration, 1.0)))
            return
        start = self.wave.sel[0] if self.wave.sel and self.wave.sel[0] < t - 0.05 else max(0.0, t - 1.0)
        self.wave.set_selection((start, t))

    def _zoom(self, k):
        if self.audio is None:
            return
        c = (self.wave.sel[0] + self.wave.sel[1]) / 2 if self.wave.sel and k < 1 else (self.wave.view[0] + self.wave.view[1]) / 2
        self.wave.zoom_at(c, k)

    def _nudge(self, d):
        if self.audio is None:
            return
        t = max(0.0, min(self.wave.duration, (self.wave.playhead or 0.0) + d))
        self.wave.set_playhead(t)
        self.lcd.setText(fmt_lcd(t))

    # ================= 재생 =================
    def toggle_src_play(self):
        if self.audio is None:
            return
        if self.player.is_playing("src"):
            self.player.stop()  # 일시정지: 재생 위치 유지
            return
        start = self.wave.playhead or 0.0
        if start >= self.wave.duration - 0.05:
            start = 0.0
        if self.player.play("src", self.audio.samples, self.audio.rate, start):
            self.btn_src_play.setText("❚❚ 일시정지")
            self.message("원본 재생 중")

    def play_selection(self):
        if self.audio is None or not self.wave.sel:
            self.message("선택 구간이 없습니다.")
            return
        if self.player.play("sel", self.audio.samples, self.audio.rate, *self.wave.sel):
            self.message("원본 선택 구간 재생 중")

    def play_out(self):
        p = self._pair()
        if p is None or not len(p.after):
            self.message("생성된 학습용 파일이 없습니다.")
            return
        before = self.show_before and p.before is not None
        if self.player.play("out", p.before if before else p.after, p.rate):
            self.message("분할 음성 재생 중 " + ("(노이즈 제거 전 원음)" if before else "(노이즈 제거 후, 저장본)"))

    def _on_play_pos(self, tag, t):
        if tag in ("src", "sel"):
            self.wave.set_playhead(t)
            self.lcd.setText(fmt_lcd(t))
        elif tag == "out":
            self.out_wave.set_playhead(t)

    def _on_play_stop(self, tag, t):
        if tag in ("src", "sel"):
            self.btn_src_play.setText("▶ 원본 재생")
            if t is not None:
                self.wave.set_playhead(t)
                self.lcd.setText(fmt_lcd(t))
        elif tag == "out":
            self.out_wave.set_playhead(None)
        self.message("정지")

    # ================= 음성인식 =================
    def _stt_config(self):
        try:
            stt = config.load_stt(self.cfg)
        except config.SttConfigError as e:
            return None, str(e)
        model = self.cfg.get("model") or (stt.get("models") or ["model_v2"])[0]
        return (stt, model), ""

    def _load_stt_label(self):
        got, err = self._stt_config()
        if got:
            stt, model = got
            self.lbl_server.setText(f"서버 {config.server_label(stt)} · {model}")
            self.lbl_server.setToolTip(f"설정 출처: {stt['source']} · API 키 {config.mask(stt.get('api_key', '')) or '없음'}")
        else:
            self.lbl_server.setText("✕ 음성인식 API 키 미설정 ([설정] → [음성인식 서버 설정])" if "API 키" in err
                                    else "✕ 음성인식 서버 미설정 ([설정] → [음성인식 서버 설정])")
            self.lbl_server.setToolTip(err)

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self.cfg.update(dlg.values())
            self._save_cfg()
            self._load_stt_label()
            self._refresh_src_badge()  # 설정 전에 붙은 "✕ 서버 미설정" 배지를 현재 상태로 바꾼다
            self.message(f"음성인식 서버 설정 저장: {self.lbl_server.text()}")

    def _refresh_src_badge(self):
        """② 배지를 현재 상태로 다시 표시: 인식 중 / 수정됨 / 저장된 결과 있음 / 미실행."""
        if self.src_worker is not None:
            return
        if self.src_dirty:
            set_badge(self.src_badge, "edit", "수정됨 (저장 안 됨)")
        elif self.audio is not None and paths.full_stt_path(self.folders, self.sources[self.cur].stem).exists():
            set_badge(self.src_badge, "ok", "저장됨")
        else:
            set_badge(self.src_badge, "none", "미실행")

    def run_src_stt(self, selection_only):
        if self.src_worker is not None:
            self.stop_src_stt()
            return
        if self.audio is None:
            return
        got, err = self._stt_config()
        if not got:
            self.message(err, error=True)
            set_badge(self.src_badge, "err", "서버 미설정")
            return
        if selection_only and not self.wave.sel:
            self.message("선택 구간이 없습니다.")
            return
        x = segment.cut(self.audio.samples, self.audio.rate, *self.wave.sel) if selection_only else self.audio.samples
        self._src_scope = f"선택 구간 {len(x) / self.audio.rate:.3f}초" if selection_only else f"{len(x) / self.audio.rate:.3f}초 음성"
        self._src_full = not selection_only
        stt = dict(got[0])
        stt["result"] = dict(stt.get("result") or {}, word_alignment=True)  # 단어별 시간 → 선택 구간 문장 가져오기
        self.src_worker = self._start_stt(convert.to_stt_pcm(x, self.audio.rate), (stt, got[1]), "src")
        self.btn_recog.setText("인식 중지")
        set_badge(self.src_badge, "warn", "인식 중…")
        self.src_prog.setValue(0)
        self.src_text.setReadOnly(True)  # 인식 중 입력한 내용이 결과로 덮이지 않게 (m-3)

    def _start_stt(self, pcm, got, tag):
        w = self.stt_factory(pcm, got[0], got[1], self)
        w.tag = tag
        w.progressed.connect(self._on_stt_progress)
        w.result.connect(self._on_stt_result)
        self._workers.add(w)
        w.start()
        return w

    def _on_stt_progress(self, w, r):
        if w.tag == "src" and w is self.src_worker:
            self.src_prog.setValue(int(r * 1000))
        elif isinstance(w.tag, Pair) and self._pair() is w.tag:
            self.out_prog.setValue(int(r * 1000))

    def _on_stt_result(self, w, r, err):
        self._workers.discard(w)
        w.wait(2000)
        w.deleteLater()
        if isinstance(r, dict):
            text, words = r.get("transcript", ""), r.get("words") or []
        else:
            text, words = r, []
        if w.tag == "src":
            if w is self.src_worker:  # 중지했거나 파일을 바꾼 뒤 도착한 결과는 버린다
                self._src_stt_ok(text, words) if text is not None else self._src_stt_fail(err)
        elif isinstance(w.tag, Pair):
            self._pair_stt_done(w.tag, text, err)

    def stop_src_stt(self):
        if self.src_worker is not None:
            self.src_worker.cancel()
            self.src_worker = None
            self.btn_recog.setText("음성인식 실행")
            set_badge(self.src_badge, "warn", "중지됨")
            self.src_prog.setValue(0)
            self.src_text.setReadOnly(False)

    def _src_stt_ok(self, text, words=()):
        self.src_worker = None
        self.src_text.setReadOnly(False)
        self.btn_recog.setText("음성인식 실행")
        self._set_src_text(text)
        self.src_prog.setValue(1000)
        self.lbl_src_info.setText(self._src_scope)
        if self._src_full:  # 원본 전체 인식 결과는 바로 저장한다(기존 파일 덮어쓰기)
            base = self.sources[self.cur].stem
            self.meta["words"] = list(words)  # 단어별 시간: 학습용 파일 생성 시 선택 구간 문장을 가져온다
            try:
                paths.save_meta(self.folders, base, self.meta)
            except OSError as e:
                self.message(f"단어 시간 기록 저장 실패: {e}", error=True)
            if self.save_text("src", quiet=True):
                set_badge(self.src_badge, "ok", "자동 저장됨")
                note = "" if words else " · 단어 시간 정보 없음(선택 구간 문장 가져오기 불가)"
                self.message(f"원본 음성인식 완료 · {paths.full_stt_path(self.folders, base)} 자동 저장{note}", error=not words)
            return
        self.src_dirty = True  # 선택 구간만 인식한 결과는 전체 결과를 덮어쓰지 않도록 자동 저장하지 않는다
        set_badge(self.src_badge, "edit", "선택 구간 인식 (저장 안 됨)")
        self.message("선택 구간 인식 완료. 원본 전체 결과로 저장하려면 [저장]을 누르세요.")

    def _src_stt_fail(self, err):
        self.src_worker = None
        self.src_text.setReadOnly(False)
        self.btn_recog.setText("음성인식 실행")
        set_badge(self.src_badge, "err", "인식 실패")
        self.src_prog.setValue(0)
        self.message(f"원본 음성인식 실패: {err}", error=True)

    # ================= 학습용 파일 생성 =================
    def create_pair(self):
        if self.audio is None:
            return
        sel = self.wave.sel
        if not sel or not segment.selection_ok(*sel):
            self.message("0.05초 이상 30초 이하의 구간을 선택하세요.", error=True)
            return
        if self._creating:
            self.message("이전 학습용 파일을 저장하는 중입니다. 잠시 후 다시 누르세요.")
            return
        if self.out_dirty and not self.confirm_unsaved():
            return
        a = self.audio
        level = self.nr.currentIndex()
        base = self.sources[self.cur].stem
        folders, profile, rate, sampwidth = self.folders, self.profile, a.rate, a.info.sampwidth
        raw = segment.cut(a.samples, rate, *sel)
        mono = a.mono() if level and profile is None else None

        def job():  # 작업 스레드: 잡음 추정 → 노이즈 제거 → 페이드 → 번호 정해 저장 (NFR-04)
            prof = profile if mono is None else denoise.estimate_profile(mono, rate)
            applied = level if (level and prof is not None and len(raw) >= prof.nperseg) else 0
            after = segment.fade(denoise.reduce(raw, rate, prof, applied), rate)
            before = segment.fade(raw, rate)
            n = paths.next_number(folders, base)
            for _ in range(100):  # 다른 프로그램이 같은 번호를 먼저 만든 경우 다음 번호
                wav = paths.part_wav_path(folders, base, n)
                try:
                    wavio.write(wav, after, rate, sampwidth)
                    return {"n": n, "wav": wav, "after": after, "before": before, "profile": prof, "applied": applied}
                except FileExistsError:
                    n += 1
            raise OSError("저장할 번호를 정하지 못했습니다.")

        self._creating = True
        self.out_text.setReadOnly(True)  # 만드는 동안 ④에 입력한 내용이 새 쌍 표시로 사라지지 않게 (n-1)
        self._update_controls()
        self.message("학습용 파일 만드는 중… (노이즈 제거·저장)")
        w = TaskWorker(job, self)
        w.tag = {"base": base, "folders": folders, "sel": sel, "level": level, "rate": rate, "audio": a}
        w.result.connect(self._on_created)
        self._workers.add(w)
        w.start()

    def _on_created(self, w, r, err):
        self._workers.discard(w)
        w.wait(2000)
        w.deleteLater()
        self._creating = False
        self.out_text.setReadOnly(False)
        self._update_controls()
        if r is None:
            self.message(f"학습용 파일 생성 실패: {err}", error=True)
            return
        t = w.tag
        base, folders, sel, level = t["base"], t["folders"], t["sel"], t["level"]
        here = self.audio is t["audio"]  # 생성하는 동안 다른 파일로 넘어가지 않았는지
        if here and self.profile is None:
            self.profile = r["profile"]
        # 분할 음성을 다시 인식하지 않고, ② 원본 인식의 단어 시간으로 선택 구간 문장만 가져와 자동 저장한다
        meta = self.meta if here else paths.load_meta(folders, base)
        words = meta.get("words") or []
        text = client.words_in(words, *sel) if words else ""
        pair = Pair(r["n"], r["wav"], paths.part_txt_path(folders, base, r["n"]), r["after"], r["before"], t["rate"],
                    sel[0], sel[1], text=text, nr=r["applied"])
        if words:
            pair.state = "ok"
        else:
            pair.state, pair.error = "err", "원본 음성인식 결과(단어 시간)가 없어 문장을 가져오지 못했습니다"
        meta.setdefault("parts", {})[str(pair.n)] = [round(sel[0], 3), round(sel[1], 3)]
        try:
            paths.write_text(pair.txt, text)
            paths.save_meta(folders, base, meta)
        except OSError as e:
            pair.state, pair.error = "err", f"텍스트 저장 실패: {e}"
        self.counts[base.lower()] = self.counts.get(base.lower(), 0) + 1
        idx = next((i for i, p in enumerate(self.sources) if p.stem == base), None)
        if idx is not None:
            self._list_text(idx)
        self._filter_list()
        if here:
            self.pairs.append(pair)
            self.cur_pair = len(self.pairs) - 1
            self._render_chips()
            self._show_pair()
            self._update_status()
        note = denoise.LEVEL_NAMES[level] if r["applied"] == level else "적용 못 함(원본이 짧거나 무음 구간 부족)"
        if pair.state == "ok":
            self.message(f"생성: {pair.wav.name} / {pair.txt.name} ({pair.duration:.3f}초, 노이즈 제거 {note}) · 선택 구간 문장 자동 저장",
                         error=r["applied"] != level)
        else:
            self.message(f"생성: {pair.wav.name} · {pair.error}. ② [음성인식 실행] 후 ④ [선택 구간 문장 가져오기]를 누르거나 "
                         f"④ [음성인식 실행]으로 분할 음성을 인식하세요.", error=True)

    def _pair_stt_done(self, pair, text, err):
        """다른 파일로 넘어간 뒤 결과가 와도 텍스트 파일은 저장한다.
        - 쌍을 삭제했거나 Wav가 없으면 저장하지 않는다.
        - 사용자가 먼저 텍스트를 저장했으면 덮어쓰지 않는다 (M-2)."""
        pair.worker = None
        if self._pending.get(str(pair.wav)) is pair:  # 지운 뒤 같은 번호로 새로 만든 쌍의 표시는 건드리지 않는다 (n-2)
            del self._pending[str(pair.wav)]
        if pair.deleted or not pair.wav.exists():
            return
        if pair.rerun is not None:  # [음성인식 실행]으로 다시 인식: 성공하면 덮어쓰고, 실패하면 기존 텍스트 유지
            prev, pair.rerun = pair.rerun, None
            if text is not None:
                try:
                    paths.write_text(pair.txt, text)
                    pair.text, pair.state, pair.error = text, "ok", ""
                    self.message(f"{pair.wav.name} 다시 인식 · {pair.txt.name} 자동 저장")
                except OSError as e:
                    pair.text, pair.state, pair.error = prev[0], prev[1], f"텍스트 저장 실패: {e}"
                    self.message(f"{pair.txt.name} 저장 실패: {e}", error=True)
            else:
                pair.text, pair.state, pair.error = prev[0], prev[1], prev[2]
                self.message(f"{pair.wav.name} 음성인식 실패: {err} · 기존 텍스트를 유지합니다.", error=True)
            if self._pair() is pair:
                self._show_pair()
            return
        try:
            if not paths.write_text_new(pair.txt, text or ""):  # 실패해도 빈 텍스트 파일을 만든다 (FR-11)
                pair.text = paths.read_text(pair.txt) or ""
                pair.state, pair.error = "ok", ""
                if self._pair() is pair and not self.out_dirty:
                    self._show_pair()
                self.message(f"{pair.txt.name}: 사용자가 저장한 텍스트를 유지했습니다 (늦게 온 인식 결과는 쓰지 않음).")
                return
        except OSError as e:
            err = f"{err} / 텍스트 저장 실패: {e}" if err else f"텍스트 저장 실패: {e}"
        pair.text = text or ""
        pair.state = "ok" if text is not None and not err else "err"
        pair.error = err
        if self._pair() is pair:
            self._show_pair()
        if pair.state == "ok":
            self.message(f"{pair.wav.name} / {pair.txt.name} 생성 완료")
        else:
            self.message(f"{pair.wav.name} 생성, 음성인식 실패: {err} · ④에 직접 입력 후 저장하세요.", error=True)

    def run_out_stt(self):
        """④ [음성인식 실행]: 선택한 분할 음성파일을 다시 인식해 stt-N.txt에 자동 저장(덮어쓰기)."""
        p = self._pair()
        if p is None or not len(p.after):
            self.message("인식할 학습용 파일이 없습니다.")
            return
        if p.state == "recognizing" or self._creating:
            self.message("음성인식이 진행 중입니다. 끝난 뒤 다시 누르세요.")
            return
        if self.out_dirty:
            r = QMessageBox.question(self, "다시 인식", "④에서 고친 내용이 인식 결과로 바뀝니다. 계속할까요?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        got, err = self._stt_config()
        if not got:
            self.message(err, error=True)
            return
        p.rerun = (p.text, p.state, p.error)
        p.state = "recognizing"
        self._pending[str(p.wav)] = p
        self._show_pair()
        self.message(f"{p.wav.name} 음성인식 중…")
        p.worker = self._start_stt(convert.to_stt_pcm(p.after, p.rate), got, p)

    def _pair(self):
        return self.pairs[self.cur_pair] if 0 <= self.cur_pair < len(self.pairs) else None

    def _render_chips(self):
        while self.chip_box.count() > 1:
            it = self.chip_box.takeAt(0)
            w = it.widget()
            if w:
                if isinstance(w, QPushButton):
                    self.chip_group.removeButton(w)
                w.deleteLater()
        if not self.pairs:
            lab = QLabel("이 원본에서 만든 학습용 파일 쌍이 여기에 표시됩니다", objectName="hint")
            self.chip_box.insertWidget(0, lab)
            return
        for i, p in enumerate(self.pairs):
            span = f"  {p.t0:.3f}~{p.t1:.3f}" if p.t0 is not None else f"  {p.duration:.3f}초"
            b = QPushButton(f"part-{p.n}{span}")
            b.setProperty("kind", "chip")
            bf = b.font()
            bf.setPixelSize(13)
            bf.setBold(True)
            b.setMinimumWidth(QFontMetrics(bf).horizontalAdvance(b.text()) + 32)
            b.setCheckable(True)
            b.setChecked(i == self.cur_pair)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(f"{p.wav}\n{p.txt}\n클릭: 보기 · 더블클릭: ①에 구간 다시 표시")
            b.clicked.connect(lambda _=False, i=i: self.select_pair(i))
            b.mouseDoubleClickEvent = lambda e, p=p: self._reselect(p)
            self.chip_group.addButton(b)
            self.chip_box.insertWidget(i, b)

    def _reselect(self, p):
        if p.t0 is not None:
            self.wave.set_selection((p.t0, p.t1))
            self.wave.zoom_selection()

    def select_pair(self, i):
        if i == self.cur_pair:
            return
        if self.out_dirty and not self.confirm_unsaved():
            self._render_chips()
            return
        self.cur_pair = i
        self._render_chips()
        self._show_pair()

    def _show_pair(self):
        p = self._pair()
        self.out_text.blockSignals(True)
        if p is None:
            self.out_stack.setCurrentIndex(0)
            self.out_wave.set_audio(None, 8000)
            self.out_sub.setText("아직 생성된 파일이 없습니다")
            self.out_text.setPlainText("")
            self.out_text.setEnabled(False)
            set_badge(self.out_badge, "none", "-")
            self.lbl_out_path.setText(self._dir_note("wav"))
            self.lbl_out_fmt.setText("")
            self.lbl_txt_path.setText(self._dir_note("part_txt"))
        else:
            self.out_stack.setCurrentIndex(1)
            use_before = self.show_before and p.before is not None
            src = p.before if use_before else p.after
            self.out_wave.set_audio(src.mean(axis=1) if len(src) else np.zeros(1, np.float32), p.rate)
            span = f" · 원본 {p.t0:.3f}~{p.t1:.3f}초 · 노이즈 제거 {denoise.LEVEL_NAMES[p.nr]}" if p.t0 is not None else ""
            self.out_sub.setText(f"{p.wav.name} · {p.duration:.3f}초{span}")
            self.lbl_out_path.setText(str(p.wav))
            self.lbl_out_path.setToolTip(str(p.wav))
            self.lbl_out_fmt.setText(f"{wavio.describe(wavio.WavInfo(p.rate, p.after.shape[1], self.audio.info.sampwidth if self.audio else 2, len(p.after))).split(' · ', 1)[1]} · 10ms 페이드")
            self.lbl_txt_path.setText(str(p.txt))
            self.lbl_txt_path.setToolTip(str(p.txt))
            self.out_text.setPlainText(p.text)
            if p.state == "recognizing":
                self.out_text.setEnabled(False)
                set_badge(self.out_badge, "warn", "인식 중…")
                self.out_prog.setValue(0)
            else:
                self.out_text.setEnabled(True)
                err_label = "문장 없음 · 가져오기 필요" if "단어 시간" in p.error else "인식 실패 · 직접 입력"
                set_badge(self.out_badge, "ok" if p.state == "ok" else "err", "저장됨" if p.state == "ok" else err_label)
                self.out_prog.setValue(1000 if p.state == "ok" else 0)
            self.tg_before.setEnabled(p.before is not None)
            self.tg_before.setToolTip("같은 구간을 노이즈 제거 없이 자른 원음 (파일로 저장하지 않음)" if p.before is not None
                                      else "원음 비교는 이번 작업에서 생성한 파일만 가능합니다")
        self.out_text.blockSignals(False)
        self.out_dirty = False
        self._update_controls()

    def _toggle_before(self, after_checked):
        self.show_before = not after_checked
        self.player.stop() if self.player.is_playing("out") else None
        self._show_pair_wave_only()

    def _show_pair_wave_only(self):
        p = self._pair()
        if p is None:
            return
        src = p.before if self.show_before and p.before is not None else p.after
        self.out_wave.set_audio(src.mean(axis=1) if len(src) else np.zeros(1, np.float32), p.rate)

    def delete_pair(self):
        p = self._pair()
        if p is None:
            return
        r = QMessageBox.question(self, "학습용 파일 쌍 삭제", f"다음 두 파일을 삭제할까요? 되돌릴 수 없습니다.\n\n{p.wav}\n{p.txt}")
        if r != QMessageBox.StandardButton.Yes:
            return
        p.deleted = True
        if p.worker is not None:
            p.worker.cancel()
        self.player.stop() if self.player.is_playing("out") else None
        for f in (p.wav, p.txt):
            try:
                f.unlink(missing_ok=True)
            except OSError as e:
                self.message(f"삭제 실패: {f} ({e})", error=True)
                return
        self.pairs.remove(p)
        if self.meta.get("parts", {}).pop(str(p.n), None) is not None:  # 분할 구간 기록도 지운다
            try:
                paths.save_meta(self.folders, self.sources[self.cur].stem, self.meta)
            except OSError:
                pass
        base = self.sources[self.cur].stem.lower()
        self.counts[base] = max(0, self.counts.get(base, 1) - 1)
        self._list_text(self.cur)
        self._filter_list()
        self.cur_pair = min(self.cur_pair, len(self.pairs) - 1)
        self._render_chips()
        self._show_pair()
        self._update_status()
        self.message(f"{p.wav.name} / {p.txt.name} 삭제")

    # ================= 텍스트 =================
    def _set_src_text(self, text):
        self.src_text.blockSignals(True)
        self.src_text.setPlainText(text)
        self.src_text.blockSignals(False)
        self.src_dirty = False

    def _src_edited(self):
        self.src_dirty = True
        set_badge(self.src_badge, "edit", "수정됨 (저장 안 됨)")

    def _out_edited(self):
        if self._pair() is not None:
            self.out_dirty = True
            set_badge(self.out_badge, "edit", "수정됨 (저장 안 됨)")

    def copy_from_src(self):
        if self._creating:  # 생성 중에는 ④가 곧 새 쌍으로 바뀌므로 막는다 (n-4)
            self.message("학습용 파일을 만드는 중입니다. 잠시 후 다시 누르세요.")
            return
        if self._pair() is None or not self.out_text.isEnabled():
            self.message("생성된 학습용 파일이 없습니다.")
            return
        text = self.src_text.toPlainText().strip()
        if not text:
            self.message("②에 인식 결과가 없습니다.")
            return
        self.out_text.setPlainText(text)  # textChanged → 수정됨 표시
        self.message("② 문장 전체를 가져왔습니다. 필요한 부분만 남기고 [저장]을 누르세요.")

    def copy_part_from_src(self):
        """③에서 선택한 학습용 Wave의 원본 구간에 해당하는 ② 문장만 가져온다(단어 시간 사용)."""
        if self._creating:
            self.message("학습용 파일을 만드는 중입니다. 잠시 후 다시 누르세요.")
            return
        p = self._pair()
        if p is None or not self.out_text.isEnabled():
            self.message("생성된 학습용 파일이 없습니다.")
            return
        if p.t0 is None:
            self.message("이 학습용 파일은 원본 구간 기록이 없어 구간 문장을 가져올 수 없습니다. [문장 전체 가져오기]를 쓰세요.", error=True)
            return
        words = self.meta.get("words") or []
        if not words:
            self.message("② 원본 음성인식을 먼저 실행하세요(단어 시간 정보가 필요합니다).", error=True)
            return
        text = client.words_in(words, p.t0, p.t1)
        if not text:
            self.message(f"원본 {p.t0:.3f}~{p.t1:.3f}초 구간에 인식된 문장이 없습니다.", error=True)
            return
        self.out_text.setPlainText(text)  # textChanged → 수정됨 표시
        self.message(f"원본 {p.t0:.3f}~{p.t1:.3f}초 구간 문장을 가져왔습니다. [저장]을 누르면 저장됩니다.")

    def save_text(self, which=None, quiet=False):
        """②(원본 인식 결과) 또는 ④(학습용 Text)를 저장한다. 기존 파일이 있으면 덮어쓴다."""
        if which is None:
            which = "src" if self.src_text.hasFocus() else "out"
            if which == "out" and not self.out_dirty and self.src_dirty:
                which = "src"
        if self.audio is None:
            return False
        base = self.sources[self.cur].stem
        try:
            if which == "src":
                path = paths.full_stt_path(self.folders, base)
                paths.write_text(path, self.src_text.toPlainText().strip())
                self.src_dirty = False
                set_badge(self.src_badge, "ok", "저장됨")
            else:
                p = self._pair()
                if p is None:
                    self.message("저장할 학습용 텍스트가 없습니다.")
                    return False
                if p.state == "recognizing":  # 인식이 끝난 뒤 저장한다 (m-2)
                    self.message("음성인식이 끝난 뒤 저장하세요.")
                    return False
                path = p.txt
                p.text = self.out_text.toPlainText().strip()
                paths.write_text(path, p.text)
                p.state, p.error = "ok", ""
                self.out_dirty = False
                set_badge(self.out_badge, "ok", "저장됨")
        except OSError as e:
            if which == "src":
                self.src_dirty = True  # 자동 저장에 실패해도 [저장]으로 다시 시도할 수 있게
                set_badge(self.src_badge, "err", "저장 실패")
            self.message(f"저장 실패: {e}", error=True)
            return False
        if not quiet:
            self.message(f"{path} 저장")
        return True

    # ================= 상태 =================
    def _dir_note(self, key):
        try:
            d = self.folders.resolve(key)
        except ValueError:
            return "-"
        return f"{d}{'' if getattr(self.folders, key) else ' (기본값)'}"

    def _update_status(self):
        a = self.audio
        if a is None:
            self.st_ch.setText("-")
            self.st_dur1.setText("-")
            self.st_dur2.setText("-")
            self.st_fmt.setText("-")
            self.st_sel.setText("-")
        else:
            self.st_ch.setText(wavio.channel_name(a.info.channels).capitalize())
            self.st_dur1.setText(f"{a.duration:.3f}")
            self.st_dur2.setText(f"{a.duration:.3f}")
            self.st_fmt.setText(wavio.describe(a.info))
            s = self.wave.sel
            self.st_sel.setText(f"{s[0]:.3f} to {s[1]:.3f} ({s[1] - s[0]:.3f})" if s else f"0.000 to {a.duration:.3f} (전체)")
        done = sum(1 for p in self.sources if self.counts.get(p.stem.lower()))
        try:
            dirs = f"원본 음성인식 Text → {self.folders.full_dir} · 분할 Wav → {self.folders.wav_dir} · 분할 음성인식 Text → {self.folders.part_txt_dir}"
        except ValueError:
            dirs = "저장 폴더 미설정"
        self.st_prog.setText(f"진행: 생성 완료 {done:,}개 / 전체 {len(self.sources):,}개 · {dirs}")
        self.st_prog.setToolTip(dirs.replace(" · ", "\n"))
        self.lbl_full_dir.setText(f"원본 음성인식 Text: {self._dir_note('full')}")
        if self._pair() is None:
            self.lbl_out_path.setText(f"분할 Wav: {self._dir_note('wav')}")
            self.lbl_txt_path.setText(f"분할 음성인식 Text: {self._dir_note('part_txt')}")

    def _update_controls(self):
        ok = self.audio is not None and bool(self.wave.sel) and segment.selection_ok(*self.wave.sel) and not self._creating
        self.btn_create.setEnabled(ok)
        self.act_create.setEnabled(ok)
        over = self.wave.sel and (self.wave.sel[1] - self.wave.sel[0]) > segment.MAX_SEG_S
        self.btn_create.setToolTip("선택 구간이 30초를 넘습니다. 구간을 줄여 주세요." if over
                                   else "선택 구간(30초 이내)으로 학습용 Wave/Text 생성 (Ctrl+Enter)")
        has = self.audio is not None
        for b in (self.btn_prev, self.btn_next):
            b.setEnabled(len(self.sources) > 1)
        self.btn_recog.setEnabled(has)
        self.btn_out_play.setEnabled(self._pair() is not None)
        self.btn_out_recog.setEnabled(self._pair() is not None and not self._creating)

    def _nr_changed(self, i):
        self.cfg["nr_level"] = i
        self._save_cfg()

    def _save_cfg(self):
        if not config.save(self.cfg):
            self.message("설정을 저장하지 못했습니다(쓰기 금지 폴더). 작업은 계속할 수 있습니다.", error=True)

    def message(self, text, error=False):
        self.st_msg.setText(("✕ " if error else "") + text)
        self.st_msg.setStyleSheet("color:#bd2c0f; font-weight:700;" if error else "")
        self.st_msg.setToolTip(text)

    def closeEvent(self, e):
        if not self.confirm_unsaved():
            e.ignore()
            return
        self.player.stop()
        self.hide()  # 남은 작업을 기다리는 동안 창은 바로 닫힌 것처럼 보이게
        for w in list(self._workers):
            w.cancel()
        deadline = time.monotonic() + 35  # 모든 작업자를 합쳐 최대 35초 (연결 대기 30초 + 여유) (C-6, n-3)
        for w in list(self._workers):
            w.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        e.accept()
