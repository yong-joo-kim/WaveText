"""폐쇄망 현장 PC용 휴대용 배포판(zip) 생성 (NFR-02, P7).

사용: .venv/Scripts/python tools/build_portable.py
결과: dist/WaveText_portable_v{버전}.zip
  - PyInstaller 폴더 방식: WaveText.exe + 라이브러리 (설치·관리자 권한 불필요)
  - 포함하지 않음: config.local.json(API 키), 음성 데이터, 시험 파일
  - 음성인식 서버 설정(서버 주소·포트·API 키)은 실행 후 [설정] → [음성인식 서버 설정]에서 입력한다.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wavetext import __version__  # noqa: E402

NAME = "WaveText"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / NAME

# 쓰지 않는 Qt 모듈은 빼서 용량을 줄인다
EXCLUDES = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtPdf",
            "PySide6.QtPdfWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
            "PySide6.QtSvg", "tkinter", "matplotlib", "pytest"]  # QtNetwork·QtOpenGL은 QtMultimedia(재생)가 필요로 함

README = """WaveText {ver} — 음성학습용 Wave/Text 생성기 (폐쇄망 휴대용)
=========================================================

설치
  1) 이 zip을 원하는 폴더에 풉니다. (설치·관리자 권한 불필요, 인터넷 불필요)
  2) WaveText.exe 를 실행합니다.
  3) [저장 폴더] 창에서 원본 음성 폴더(필수)를 지정합니다.

음성인식 서버
  - [설정] > [음성인식 서버 설정]에서 서버 주소, 포트, 접속 방식, API 키, 모델을 입력하고
    [연결 확인]으로 점검합니다. 입력한 값은 WaveText.exe 옆 config.local.json 에 저장됩니다.

주의
  - API 키가 들어 있는 설정 파일은 다른 사람에게 복사하지 마세요.
  - 자세한 사용법: 사용설명서.md
"""


def main():
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(APP, ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
           "--name", NAME, "--paths", str(ROOT / "src"), "--distpath", str(DIST), "--workpath", str(BUILD),
           "--specpath", str(BUILD)]
    for m in EXCLUDES:
        cmd += ["--exclude-module", m]
    fonts = ROOT / "src" / "wavetext" / "assets" / "fonts"
    if fonts.is_dir() and any(fonts.iterdir()):
        cmd += ["--add-data", f"{fonts};wavetext/assets/fonts"]  # theme.load_fonts가 찾는 위치
    cmd.append(str(ROOT / "src" / "wavetext" / "__main__.py"))
    subprocess.run(cmd, check=True)
    (APP / "읽어보기.txt").write_text(README.format(ver=__version__), encoding="utf-8")
    manual = ROOT / "docs" / "06_manual" / "사용설명서.md"
    if manual.exists():
        shutil.copy(manual, APP / "사용설명서.md")
    for bad in ("config.local.json",):  # 키 파일이 섞이지 않게
        (APP / bad).unlink(missing_ok=True)
    out = DIST / f"{NAME}_portable_v{__version__}.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in APP.rglob("*"):
            z.write(f, Path(NAME) / f.relative_to(APP))
    print(f"완료: {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
