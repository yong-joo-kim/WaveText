"""자동 분할 구간 목록: 쌍마다 저장 선택, 재생, 문장 수정."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemDelegate, QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTableWidget, QTableWidgetItem)

from wavetext.audio import segment
from wavetext.ui.theme import C

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
        same = " ".join(s.text.split()) == s.saved_text and not s.wav_stale
        parts.append("✓ 저장됨" if same else "✎ 수정됨")
    return " · ".join(parts) or "저장 전"


class CheckDelegate(QStyledItemDelegate):
    """저장 체크: 흰 네모 바탕에 파란 체크. 윈도우 기본(파란 바탕에 흰 체크)보다 잘 보인다.
    이미지 파일 없이 그린다(폐쇄망 배포판에 SVG 모듈을 넣지 않음). 누르는 영역은 기본 동작 그대로다."""

    def paint(self, p, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style()
        box = QRectF(style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, opt.widget))
        checkable = bool(opt.features & QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator)
        checked = opt.checkState == Qt.CheckState.Checked
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, p, opt.widget)  # 행 선택 바탕
        if not checkable:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(box.width(), box.height(), 18.0)
        r = QRectF(0, 0, side, side)
        r.moveCenter(box.center())
        blue = QColor(C["primary-50"])
        p.setPen(QPen(blue if checked else QColor(C["gray-50"]), 1.5))
        p.setBrush(QColor(C["gray-0"]))
        p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), 3, 3)
        if checked:
            pen = QPen(blue, 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            x, y, s = r.left(), r.top(), r.width()
            p.drawPolyline([QPointF(x + s * 0.24, y + s * 0.52), QPointF(x + s * 0.43, y + s * 0.70), QPointF(x + s * 0.76, y + s * 0.30)])
        p.restore()


class SegmentTable(QTableWidget):
    picked = Signal(int)        # 사용자가 행을 골랐다
    playClicked = Signal(int)
    edited = Signal()           # 저장 선택이나 문장이 바뀌었다

    def __init__(self, parent=None):
        super().__init__(0, len(HEADERS), parent)
        self.segs = []
        self.setHorizontalHeaderLabels(HEADERS)
        self.setItemDelegateForColumn(COL_SAVE, CheckDelegate(self))
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
        # 긴 문장은 "…"로 줄이지 않고 아래 줄로 이어 보여 준다. 창을 넓히면 문장 칸이 넓어진 만큼 옆으로 더 보인다.
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        head.sectionResized.connect(self._fit_rows)
        self.itemChanged.connect(self._item_changed)
        self.currentCellChanged.connect(self._cell_changed)
        self.cellClicked.connect(self._cell_clicked)

    def _fit_rows(self, col, _old, _new):
        if col == COL_TEXT:  # 문장 칸 폭이 바뀌면 줄 수가 달라지므로 행 높이를 다시 맞춘다
            self.resizeRowsToContents()

    def set_segs(self, segs):
        self.blockSignals(True)
        self.segs = segs
        self.setRowCount(len(segs))
        for row, s in enumerate(segs):
            self._fill_row(row, s)
        self.setCurrentCell(-1, -1)
        self.blockSignals(False)

    @staticmethod
    def _set_check(check, s):
        ro = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if segment.selection_ok(s.t0, s.t1):  # 30초를 넘는 구간은 저장할 수 없다 (FR-08)
            check.setFlags(ro | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if s.selected else Qt.CheckState.Unchecked)
            check.setToolTip("")
        else:
            check.setFlags(ro)
            check.setToolTip("30초를 넘어 저장할 수 없습니다")

    def _fill_row(self, row, s):
        ro = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        check = QTableWidgetItem()
        self._set_check(check, s)
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
        self.item(row, COL_SPAN).setToolTip("파형에서 이 구간의 \"시작\"/\"끝\"을 끌어 고칩니다")
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

    def update_row(self, row):
        """시작·끝을 고친 행의 구간·길이·상태·문장을 다시 쓴다."""
        s = self.segs[row]
        self.blockSignals(True)
        self._set_check(self.item(row, COL_SAVE), s)
        self.item(row, COL_SPAN).setText(f"{fmt_t(s.t0)} ~ {fmt_t(s.t1)}")
        self.item(row, COL_LEN).setText(f"{s.duration:.1f}초")
        self.item(row, COL_STATE).setText(seg_state(s))
        self.item(row, COL_TEXT).setText(s.text)
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
