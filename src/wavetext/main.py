"""실행 진입점: python -m wavetext"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from wavetext import config
from wavetext.ui import theme
from wavetext.ui.main_window import MainWindow


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("WaveText")
    ui_font, mono = theme.load_fonts()
    app.setStyleSheet(theme.stylesheet(ui_font, mono))
    win = MainWindow(config.load(), ui_font, mono)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
