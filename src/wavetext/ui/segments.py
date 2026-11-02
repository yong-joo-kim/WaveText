"""자동 분리 구간 목록: 쌍마다 저장 선택, 재생, 문장 수정."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemDelegate, QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from wavetext.audio import segment

COL_SAVE, COL_NO, COL_PLAY, COL_SPAN, COL_LEN, COL_STATE, COL_TEXT = range(7)
HEADERS = ["저장", "번호", "재생", "구간", "길이", "상태", "문장 (눌러서 수정)"]
WIDTHS = {COL_SAVE: 56, COL_NO: 56, COL_PLAY: 56, COL_SPAN: 170, COL_LEN: 72, COL_STATE: 150}
FLAG_NAMES = {"short": "⚠ 짧음", "long": "⚠ 김"}


def fmt_t(t):
    t = round(t, 1)
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def seg_state(s):
    """상태 글자: 저장 여부와 길이 경고를 기호와 함께 쓴다 (NFR-06)."""
    parts = [FLAG_NAMES[s.flag]] if s.flag else []
    if s.saved_text is not None:
        parts.append("✓ 저장됨" if " ".join(s.text.split()) == s.saved_text else "✎ 수정됨")
    return " · ".join(parts) or "저장 전"


class SegmentTable(QTableWidget):
    picked = Signal(int)        # 사용자가 행을 골랐다
    playClicked = Signal(int)
    edited = Signal()           # 저장 선택이나 문장이 바뀌었다

    def __init__(self, parent=None):
        super().__init__(0, len(HEADERS), parent)
        self.segs = []
        self.setHorizontalHeaderLabels(HEADERS)
        self.verticalHeader().hide()
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.CurrentChanged | QAbstractItemView.EditTrigger.SelectedClicked
                             | QAbstractItemView.EditTrigger.EditKeyPressed)
        head = self.horizontalHeader()
        for col, w in WIDTHS.items():
            self.setColumnWidth(col, w)
        head.setSectionResizeMode(COL_TEXT, QHeaderView.ResizeMode.Stretch)
        self.itemChanged.connect(self._item_changed)
        self.currentCellChanged.connect(self._cell_changed)
        self.cellClicked.connect(self._cell_clicked)

    def set_segs(self, segs):
        self.blockSignals(True)
        self.segs = segs
        self.setRowCount(len(segs))
        for row, s in enumerate(segs):
            self._fill_row(row, s)
        self.setCurrentCell(-1, -1)
        self.blockSignals(False)

    def _fill_row(self, row, s):
        ro = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        check = QTableWidgetItem()
        if segment.selection_ok(s.t0, s.t1):  # 30초를 넘는 구간은 저장할 수 없다 (FR-08)
            check.setFlags(ro | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if s.selected else Qt.CheckState.Unchecked)
        else:
            check.setFlags(ro)
            check.setToolTip("30초를 넘어 저장할 수 없습니다")
        self.setItem(row, COL_SAVE, check)
        cells = {COL_NO: str(s.n), COL_PLAY: "▶", COL_SPAN: f"{fmt_t(s.t0)} ~ {fmt_t(s.t1)}",
                 COL_LEN: f"{s.duration:.1f}초", COL_STATE: seg_state(s)}
        for col, text in cells.items():
            it = QTableWidgetItem(text)
            it.setFlags(ro)
            if col in (COL_NO, COL_PLAY):
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(row, col, it)
        self.item(row, COL_PLAY).setToolTip("이 구간 재생 (Ctrl+Space)")
        text = QTableWidgetItem(s.text)
        text.setFlags(ro | Qt.ItemFlag.ItemIsEditable)
        self.setItem(row, COL_TEXT, text)

    def refresh_states(self):
        self.blockSignals(True)
        for row, s in enumerate(self.segs):
            self.item(row, COL_STATE).setText(seg_state(s))
            check = self.item(row, COL_SAVE)
            if check.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                check.setCheckState(Qt.CheckState.Checked if s.selected else Qt.CheckState.Unchecked)
        self.blockSignals(False)

    def select_row(self, row):
        """코드에서 행을 고른다: 편집칸을 열지 않고 picked도 보내지 않는다."""
        self.commit()
        self.blockSignals(True)
        self.setCurrentCell(row, COL_NO)
        self.blockSignals(False)
        self.scrollToItem(self.item(row, COL_NO), QAbstractItemView.ScrollHint.PositionAtCenter)

    def set_playing(self, row):
        self.blockSignals(True)
        for i in range(self.rowCount()):
            self.item(i, COL_PLAY).setText("■" if i == row else "▶")
        self.blockSignals(False)

    def edit_row(self, row):
        self.setCurrentCell(row, COL_TEXT)

    def commit(self):
        """열려 있는 문장 편집칸의 내용을 확정한다."""
        if self.state() != QAbstractItemView.State.EditingState:
            return
        editor = self.indexWidget(self.currentIndex())
        if editor is not None:
            self.commitData(editor)
            self.closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)

    def _item_changed(self, it):
        s = self.segs[it.row()]
        if it.column() == COL_SAVE:
            s.selected = it.checkState() == Qt.CheckState.Checked
        elif it.column() == COL_TEXT:
            s.text = it.text().strip()
            self.blockSignals(True)
            self.item(it.row(), COL_STATE).setText(seg_state(s))
            self.blockSignals(False)
        else:
            return
        self.edited.emit()

    def _cell_changed(self, row, _col, prev_row, _prev_col):
        if row >= 0 and row != prev_row:
            self.picked.emit(row)

    def _cell_clicked(self, row, col):
        if col == COL_PLAY:
            self.playClicked.emit(row)
