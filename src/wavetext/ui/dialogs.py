"""대화상자: 저장 폴더 설정(FR-01, FR-02), 음성인식 설정(FR-20), 단축키 안내(FR-21)."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QTreeView, QVBoxLayout)

from wavetext import config
from wavetext.paths import DEFAULT_SUBDIRS, FOLDER_KEYS, Folders
from wavetext.ui.workers import HealthWorker

SRC_HINT = "필수 입력입니다. 아래 폴더를 비워 두면 이 폴더 아래에 만듭니다."


def make_folder_dialog(parent, title, start):
    """폴더 선택 창. 윈도우 기본 창은 폴더만 보이므로 Qt 창을 써서 폴더 안 파일도 함께 보여 준다."""
    dlg = QFileDialog(parent, title, start)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, False)
    dlg.setViewMode(QFileDialog.ViewMode.Detail)
    for label, text in ((QFileDialog.DialogLabel.LookIn, "찾는 위치"), (QFileDialog.DialogLabel.FileName, "폴더"),
                        (QFileDialog.DialogLabel.FileType, "파일 형식"), (QFileDialog.DialogLabel.Accept, "선택"),
                        (QFileDialog.DialogLabel.Reject, "취소")):
        dlg.setLabelText(label, text)
    dlg.resize(960, 560)
    tree = dlg.findChild(QTreeView, "treeView")
    if tree is not None:  # 이름 열이 넓게 잡혀 크기·형식·날짜가 잘리지 않도록
        for col, width in enumerate((340, 100, 130, 190)):
            tree.header().resizeSection(col, width)
    return dlg


def pick_folder(parent, title, start):
    """폴더를 고르게 하고 경로를 돌려준다. 취소하면 빈 문자열."""
    dlg = make_folder_dialog(parent, title, start)
    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selectedFiles():
        return str(Path(dlg.selectedFiles()[0]))
    return ""


def _set_prop(w, name, value):
    w.setProperty(name, value)
    w.style().unpolish(w)
    w.style().polish(w)


class FolderDialog(QDialog):
    """저장 폴더 설정. 원본 음성 폴더는 필수, 나머지는 비우면 원본 아래 기본 폴더."""

    ROWS = [
        ("full", "원본 음성인식 Text", "원본 전체 음성인식 결과 <b>원본이름.stt.txt</b>, 단어 시간·구간 기록 <b>.stt.json</b>·<b>.split.json</b> (사용자·자동 분할 공용)"),
        ("wav", "사용자 분할 음성 Wav", "직접 선택해 자른 음성파일 <b>원본이름.part-1.wav</b>, part-2.wav …"),
        ("part_txt", "사용자 분할 음성인식 Text", "직접 선택해 자른 음성파일의 문장 <b>원본이름.stt-1.txt</b>, stt-2.txt …"),
        ("auto_wav", "자동 분할 음성 Wav", "자동분할생성으로 저장한 음성파일 <b>원본이름.part-1.wav</b>, part-2.wav …"),
        ("auto_txt", "자동 분할 음성인식 Text", "자동분할생성으로 저장한 문장 <b>원본이름.stt-1.txt</b>, stt-2.txt …"),
    ]

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.setWindowTitle("저장 폴더 설정")
        self.setMinimumWidth(920)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        g.setColumnMinimumWidth(0, 170)
        lab = QLabel('원본 음성 폴더 <span style="color:#bd2c0f">*</span>')
        self.src = QLineEdit(folders.src)
        self.src.setAccessibleName("원본 음성 폴더 (필수)")
        self.src_hint = QLabel(SRC_HINT)
        self.src_hint.setObjectName("hint")
        g.addWidget(lab, 0, 0)
        g.addWidget(self.src, 0, 1)
        g.addWidget(self._browse(self.src), 0, 2)
        g.addWidget(self.src_hint, 1, 1, 1, 2)
        self.edits = {}
        row = 2
        for key, label, hint in self.ROWS:
            e = QLineEdit(getattr(folders, key))
            self.edits[key] = e
            h = QLabel(hint)
            h.setObjectName("hint")
            g.addWidget(QLabel(label), row, 0)
            g.addWidget(e, row, 1)
            g.addWidget(self._browse(e), row, 2)
            g.addWidget(h, row + 1, 1, 1, 2)
            row += 2
        self.src.textChanged.connect(self._refresh)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("kind", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        title = QLabel("저장 폴더 설정")
        title.setStyleSheet("font-size:19px; font-weight:700;")
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 16)
        v.setSpacing(12)
        v.addWidget(title)
        v.addLayout(g)
        v.addWidget(buttons)
        self._refresh()

    def _browse(self, edit):
        b = QPushButton("찾아보기…")
        b.clicked.connect(lambda: self._pick(edit))
        return b

    def _pick(self, edit):
        start = edit.text() or self.src.text() or str(Path.home())
        d = pick_folder(self, "폴더 선택", start)
        if d:
            edit.setText(d)

    def _refresh(self):
        s = self.src.text().strip().rstrip("\\/") or "원본 음성 폴더"
        for key, e in self.edits.items():
            e.setPlaceholderText(f"비우면: {s}\\{DEFAULT_SUBDIRS[key]}")
        self._show_error(None)

    def _show_error(self, msg):
        _set_prop(self.src, "error", bool(msg))
        _set_prop(self.src_hint, "error", bool(msg))
        self.src_hint.setText(msg or SRC_HINT)

    def folders(self):
        return Folders(src=self.src.text().strip().rstrip("\\/"), **{k: self.edits[k].text().strip() for k in FOLDER_KEYS})

    def accept(self):
        src = self.src.text().strip()
        if not src:
            self._show_error("원본 음성 폴더를 입력하세요. (필수)")
            self.src.setFocus()
            return
        if not Path(src).is_dir():
            self._show_error("원본 음성 폴더가 없습니다. 경로를 확인하세요.")
            self.src.setFocus()
            return
        super().accept()


class SettingsDialog(QDialog):
    """음성인식 서버 설정 (FR-20): 서버 주소, 포트, 접속 방식(ws/wss), API 키(변경할 때만 입력), 모델, [연결 확인].
    값은 WaveText 설정(config.local.json)에만 저장한다."""

    MODELS = ["model_v2", "model_v1", "model_v3"]

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("음성인식 서버 설정")
        self.setMinimumWidth(640)
        self.cfg = cfg
        self._worker = None
        srv = config.server_of(cfg)
        self._cur_key = (cfg.get("stt") or {}).get("api_key", "")
        self.host = QLineEdit(srv.get("host", ""))
        self.host.setPlaceholderText("음성인식 서버 주소 (예: 10.0.0.10)")
        # QSpinBox는 PySide6 6.12에서 화살표를 없애고 스타일을 입히면 숫자가 가려지므로 숫자 입력칸을 쓴다
        self.port = QLineEdit(str(int(srv.get("port") or 8081)))
        self.port.setValidator(QIntValidator(1, 65535, self))
        self.scheme = QComboBox()
        self.scheme.addItems(["ws", "wss"])
        self.scheme.setCurrentText(srv.get("scheme") or "ws")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("변경할 때만 입력" if self._cur_key else "API 키를 입력하세요 (필수)")
        self.keyinfo = QLabel(f"({config.mask(self._cur_key)} 저장됨)" if self._cur_key else "(미설정)")
        self.keyinfo.setObjectName("hint")
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(list(dict.fromkeys(([cfg["model"]] if cfg.get("model") else []) + self.MODELS)))
        self.check = QPushButton("연결 확인")
        self.check.clicked.connect(self._health)
        self.status = QLabel("")
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        g.addWidget(QLabel("서버 주소"), 0, 0)
        g.addWidget(self.host, 0, 1)
        g.addWidget(QLabel("포트"), 1, 0)
        g.addWidget(self.port, 1, 1)
        g.addWidget(QLabel("접속 방식"), 2, 0)
        g.addWidget(self.scheme, 2, 1)
        kl = QHBoxLayout()
        kl.addWidget(QLabel("API 키"))
        kl.addWidget(self.keyinfo)
        kl.addStretch(1)
        g.addLayout(kl, 3, 0)
        g.addWidget(self.key, 3, 1)
        g.addWidget(QLabel("모델"), 4, 0)
        g.addWidget(self.model, 4, 1)
        g.setColumnStretch(1, 1)
        h = QHBoxLayout()
        h.addWidget(self.check)
        h.addWidget(self.status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("kind", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        title = QLabel("서버·모델")
        title.setStyleSheet("font-size:19px; font-weight:700;")
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 16)
        v.setSpacing(12)
        v.addWidget(title)
        v.addLayout(g)
        v.addLayout(h)
        v.addWidget(buttons)

    def server(self):
        return {"scheme": self.scheme.currentText(), "host": self.host.text().strip(), "port": int(self.port.text() or 8081)}

    def _health(self):
        if not self.host.text().strip():
            self.status.setText("✕ 서버 주소를 입력하세요")
            return
        self.check.setEnabled(False)
        self.status.setText("확인 중…")
        self._worker = HealthWorker(self.server(), self)
        self._worker.done.connect(self._health_done)
        self._worker.start()

    def _health_done(self, ok, msg):
        self.check.setEnabled(True)
        self.status.setText(("✓ 연결됨 " if ok else "✕ 연결 실패 ") + msg)

    def accept(self):
        if not self.host.text().strip():
            self.status.setText("✕ 서버 주소를 입력하세요")
            self.host.setFocus()
            return
        if self._worker is not None:
            self._worker.wait(6000)
        super().accept()

    def values(self):
        """저장할 값. API 키는 새로 입력했을 때만 바꾸고, 아니면 저장된 키를 유지한다."""
        key = self.key.text().strip() or self._cur_key
        pacing = (self.cfg.get("stt") or {}).get("realtime_pacing", False)  # 도구 모음에서 고른 값을 유지한다
        return {"stt": {"server": self.server(), "api_key": key, "realtime_pacing": pacing}, "model": self.model.currentText().strip()}


SHORTCUTS = [
    ("Space", "원본 재생 / 일시정지"), ("Shift+Space", "선택 구간 재생"), ("Ctrl+Space", "학습용 파일 재생"),
    ("[  /  ]", "재생 위치를 시작 / 끝 마크로"), ("← →  (Shift)", "재생 위치 0.1초 이동 (1초)"), ("Esc", "마크 해제"),
    ("Ctrl+A", "전체 선택"), ("Ctrl+Enter", "학습용 파일 생성하기"), ("F5 / Shift+F5", "원본 음성인식 / 선택 구간만 인식"), ("Ctrl+F5", "학습용 파일 다시 인식"),
    ("Ctrl+S", "편집 중인 텍스트 저장"), ("PgUp / PgDn", "이전 / 다음 파일"), ("Shift+↑ / ↓", "확대 / 축소"),
    ("Shift+S / Shift+A", "구간 맞춤 / 전체 보기"), ("Ctrl+↑ / ↓", "세로 확대 / 축소"), ("Del", "선택한 학습용 파일 쌍 삭제"),
    ("F6", "자동분할생성 창 열기"), ("Ctrl+Shift+↑ / ↓", "자동 분할 구간을 앞 / 뒤 구간과 합치기"), ("Ctrl+L", "파일 목록"), ("Ctrl+O", "저장 폴더 설정"), ("F1", "단축키 안내"),
]


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("단축키 안내")
        self.resize(460, 640)
        t = QTableWidget(len(SHORTCUTS), 2)
        t.setHorizontalHeaderLabels(["키", "동작"])
        t.verticalHeader().hide()
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        t.setColumnWidth(0, 170)
        for i, (k, d) in enumerate(SHORTCUTS):
            t.setItem(i, 0, QTableWidgetItem(k))
            t.setItem(i, 1, QTableWidgetItem(d))
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        v = QVBoxLayout(self)
        v.addWidget(t)
        v.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
