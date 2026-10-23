"""KRDS 표준형 스타일 토큰과 Qt 스타일시트 (NFR-06, 화면설계서 7장)."""
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase

from wavetext.config import app_root

C = {
    "primary-5": "#ecf2fe", "primary-10": "#d8e5fd", "primary-50": "#256ef4", "primary-60": "#0b50d0", "primary-70": "#083891",
    "secondary-5": "#eef2f7", "secondary-10": "#d6e0eb", "secondary-70": "#063a74",
    "gray-0": "#ffffff", "gray-5": "#f4f5f6", "gray-10": "#e6e8ea", "gray-20": "#cdd1d5", "gray-40": "#8a949e",
    "gray-50": "#6d7882", "gray-60": "#58616a", "gray-70": "#464c53", "gray-90": "#1e2124",
    "danger-5": "#fdefec", "danger-10": "#fcdfd9", "danger-50": "#de3412", "danger-60": "#bd2c0f",
    "warning-5": "#fff3db", "warning-10": "#ffdfa2", "warning-30": "#ffb114", "warning-60": "#8a5c00",
    "success-5": "#eaf6ec", "success-10": "#d8efdd", "success-60": "#267337",
    "info-5": "#e7f4fe", "info-10": "#d3ebfd", "info-60": "#096ab3",
}

WAVE = {
    "bg": QColor(C["gray-0"]), "grid": QColor(C["gray-10"]), "grid_mid": QColor(C["gray-20"]),
    "zero": QColor(C["gray-40"]), "label": QColor(C["gray-60"]), "wave": QColor(C["primary-50"]),
    "sel": QColor(37, 110, 244, 26), "mark": QColor(C["warning-30"]), "play": QColor(C["gray-90"]),
    "overview_bg": QColor(C["gray-5"]), "view_box": QColor(C["primary-50"]),
}

FONT_FAMILIES = ["Pretendard GOV", "Pretendard", "Malgun Gothic"]
MONO_FAMILIES = ["D2Coding", "Consolas"]


def load_fonts():
    """assets/fonts의 서체 파일(Pretendard GOV 등)을 등록하고 사용할 글꼴 이름을 돌려준다 (OI-03)."""
    for d in (app_root() / "assets" / "fonts", Path(__file__).resolve().parents[1] / "assets" / "fonts"):
        if d.is_dir():
            for f in list(d.glob("*.otf")) + list(d.glob("*.ttf")):
                QFontDatabase.addApplicationFont(str(f))
    families = set(QFontDatabase.families())
    ui = next((f for f in FONT_FAMILIES if f in families), "Malgun Gothic")
    mono = next((f for f in MONO_FAMILIES if f in families), "Consolas")
    return ui, mono


def mono_font(family, px):
    f = QFont(family)
    f.setPixelSize(px)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def stylesheet(ui_font, mono):
    c = C
    return f"""
* {{ font-family: "{ui_font}"; font-size: 15px; color: {c['gray-90']}; }}
QMainWindow, #central {{ background: {c['gray-5']}; }}
QMenuBar {{ background: {c['gray-0']}; border-bottom: 1px solid {c['gray-20']}; padding: 2px 8px; }}
QMenuBar::item {{ padding: 6px 12px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {c['primary-5']}; color: {c['primary-60']}; }}
QMenu {{ background: {c['gray-0']}; border: 1px solid {c['gray-20']}; border-radius: 6px; padding: 4px 0; }}
QMenu::item {{ padding: 6px 24px 6px 16px; }}
QMenu::item:selected {{ background: {c['primary-5']}; color: {c['gray-90']}; }}
QMenu::item:disabled {{ color: {c['gray-40']}; }}
QMenu::separator {{ height: 1px; background: {c['gray-10']}; margin: 4px 0; }}

#toolbar {{ background: {c['gray-0']}; border-bottom: 1px solid {c['gray-20']}; }}
QToolButton#tb {{ border: 1px solid transparent; border-radius: 6px; padding: 2px 6px; min-width: 52px; min-height: 44px; font-size: 13px; color: {c['gray-70']}; }}
QToolButton#tb:hover {{ background: {c['gray-5']}; border-color: {c['gray-10']}; }}
QToolButton#tb:pressed {{ background: {c['gray-10']}; }}
QToolButton#tb:disabled {{ color: {c['gray-40']}; }}
#lcd {{ background: {c['secondary-5']}; color: {c['secondary-70']}; border: 1px solid {c['secondary-10']}; border-radius: 6px; padding: 2px 12px; font-family: "{mono}"; font-size: 22px; font-weight: 700; }}
#sep {{ background: {c['gray-20']}; min-width: 1px; max-width: 1px; margin: 6px 4px; }}

QPushButton {{ min-height: 30px; padding: 0 12px; border: 1px solid {c['gray-50']}; border-radius: 6px; background: {c['gray-0']}; }}
QPushButton:hover {{ background: {c['gray-5']}; }}
QPushButton:pressed {{ background: {c['gray-10']}; }}
QPushButton:disabled {{ background: {c['gray-10']}; color: {c['gray-50']}; border-color: {c['gray-10']}; }}
QPushButton[kind="primary"] {{ background: {c['primary-50']}; border-color: {c['primary-50']}; color: #ffffff; font-weight: 700; }}
QPushButton[kind="primary"]:hover {{ background: {c['primary-60']}; border-color: {c['primary-60']}; }}
QPushButton[kind="primary"]:pressed {{ background: {c['primary-70']}; }}
QPushButton[kind="primary"]:disabled {{ background: {c['gray-10']}; color: {c['gray-50']}; border-color: {c['gray-10']}; }}
QPushButton#create {{ min-height: 40px; padding: 0 16px; font-size: 17px; }}
QPushButton[kind="toggle"] {{ border-radius: 0; padding: 0 12px; font-size: 13px; }}
QPushButton[kind="toggle"]:checked {{ background: {c['primary-50']}; border-color: {c['primary-50']}; color: #ffffff; font-weight: 700; }}
QPushButton[kind="chip"] {{ min-height: 26px; border-radius: 4px; font-size: 13px; padding: 0 12px; }}
QPushButton[kind="chip"]:checked {{ background: {c['primary-50']}; border-color: {c['primary-50']}; color: #ffffff; font-weight: 700; }}
QPushButton:focus, QToolButton:focus {{ outline: none; border: 2px solid {c['primary-50']}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 30px; border: 1px solid {c['gray-50']}; border-radius: 6px; padding: 0 8px; background: {c['gray-0']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {c['primary-50']}; }}
QLineEdit[error="true"] {{ border: 2px solid {c['danger-50']}; }}
QLineEdit:read-only {{ background: {c['gray-5']}; }}

#pane {{ background: {c['gray-0']}; border: 1px solid {c['gray-20']}; border-radius: 10px; }}
#paneHead {{ border-bottom: 1px solid {c['gray-10']}; }}
#paneFoot {{ background: {c['secondary-5']}; border-top: 1px solid {c['gray-10']}; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }}
#paneFoot QLabel {{ font-size: 13px; color: {c['gray-70']}; }}
#paneNo {{ background: {c['primary-50']}; color: #ffffff; border-radius: 11px; font-size: 13px; font-weight: 700; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }}
#paneTitle {{ font-size: 17px; font-weight: 700; }}
#paneSub {{ font-size: 13px; color: {c['gray-60']}; }}
#chips {{ border-bottom: 1px solid {c['gray-10']}; }}
#empty {{ color: {c['gray-60']}; font-size: 15px; }}
#hint {{ color: {c['gray-60']}; font-size: 13px; }}
#hint[error="true"] {{ color: {c['danger-60']}; font-weight: 700; }}
#req {{ color: {c['danger-60']}; font-weight: 700; }}

QPlainTextEdit#stt {{ border: 0; padding: 12px; font-size: 17px; background: {c['gray-0']}; }}
QPlainTextEdit#stt:focus {{ border: 2px solid {c['primary-50']}; }}
QPlainTextEdit#stt:disabled {{ background: {c['gray-5']}; color: {c['gray-50']}; }}
QProgressBar {{ border: 0; background: {c['gray-10']}; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {c['primary-50']}; }}

QLabel[badge="none"] {{ font-size: 13px; padding: 0 8px; border-radius: 2px; border: 1px solid {c['gray-20']}; background: {c['gray-5']}; color: {c['gray-70']}; }}
QLabel[badge="ok"] {{ font-size: 13px; padding: 0 8px; border-radius: 2px; border: 1px solid {c['success-10']}; background: {c['success-5']}; color: {c['success-60']}; }}
QLabel[badge="warn"] {{ font-size: 13px; padding: 0 8px; border-radius: 2px; border: 1px solid {c['warning-10']}; background: {c['warning-5']}; color: {c['warning-60']}; }}
QLabel[badge="edit"] {{ font-size: 13px; padding: 0 8px; border-radius: 2px; border: 1px solid {c['danger-10']}; background: {c['danger-5']}; color: {c['danger-60']}; }}
QLabel[badge="err"] {{ font-size: 13px; padding: 0 8px; border-radius: 2px; border: 1px solid {c['danger-10']}; background: {c['danger-5']}; color: {c['danger-60']}; font-weight: 700; }}
QLabel#over {{ color: {c['danger-60']}; font-weight: 700; }}

#statusBox {{ background: {c['gray-0']}; border-top: 1px solid {c['gray-20']}; }}
#statusBox QLabel {{ font-size: 13px; color: {c['gray-70']}; padding: 0 10px; border-right: 1px solid {c['gray-10']}; }}
#statusBox QLabel#fmt {{ font-weight: 700; color: {c['gray-90']}; }}
#statusBox QLabel#num {{ font-family: "{mono}"; }}

#drawer {{ background: {c['gray-0']}; border: 1px solid {c['gray-20']}; border-radius: 10px; }}
QListWidget {{ border: 0; font-size: 15px; }}
QListWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {c['gray-5']}; }}
QListWidget::item:selected {{ background: {c['primary-10']}; color: {c['gray-90']}; }}
QListWidget::item:hover {{ background: {c['primary-5']}; }}

QDialog {{ background: {c['gray-0']}; }}
QTableWidget {{ border: 0; gridline-color: {c['gray-10']}; font-size: 15px; }}
QHeaderView::section {{ background: {c['secondary-5']}; border: 0; border-bottom: 1px solid {c['gray-20']}; padding: 6px; font-weight: 700; }}
QToolTip {{ background: {c['gray-90']}; color: #ffffff; border: 0; padding: 6px 8px; font-size: 13px; }}
"""
