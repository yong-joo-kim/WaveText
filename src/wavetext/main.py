"""실행 진입점: python -m wavetext"""
import sys

from PySide6.QtCore import QLibraryInfo, Qt, QTranslator
from PySide6.QtWidgets import QApplication

from wavetext import config
from wavetext.ui import theme
from wavetext.ui.main_window import MainWindow


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("WaveText")
    # Qt 기본 문구(파일 선택 창의 열 제목 등)를 한국어로. 번역 파일이 없으면 영어 그대로 둔다.
    translator = QTranslator(app)
    if translator.load("qtbase_ko", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    ui_font, mono = theme.load_fonts()
    app.setStyleSheet(theme.stylesheet(ui_font, mono))
    win = MainWindow(config.load(), ui_font, mono)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
