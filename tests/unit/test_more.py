"""검증보고서 반영 시험: 형식 제한(m-8), 인코딩(m-12), 덮어쓰기 방지(M-2), 배포·설정(NFR-02), 대량 파일(NFR-03)."""
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from wavetext import config, paths
from wavetext.audio import wavio
from tests.conftest import synth_speech

ROOT = Path(__file__).resolve().parents[2]


def test_reject_more_than_two_channels(tmp_path):
    p = tmp_path / "c3.wav"
    wavio.write(p, np.zeros((800, 3), np.float32), 8000, 2)
    with pytest.raises(wavio.UnsupportedWav, match="3채널"):
        wavio.read_info(p)


def test_read_text_cp949_fallback(tmp_path):
    p = tmp_path / "old.txt"
    p.write_bytes("예전 파일".encode("cp949"))
    assert paths.read_text(p) == "예전 파일"


def test_write_text_new_does_not_overwrite(tmp_path):
    p = tmp_path / "a.stt-1.txt"
    assert paths.write_text_new(p, "인식 결과")
    paths.write_text(p, "사용자 수정")
    assert not paths.write_text_new(p, "늦게 온 결과")
    assert paths.read_text(p) == "사용자 수정"


def test_save_config_readonly_folder_does_not_raise(tmp_path):
    assert config.save({"a": 1}, tmp_path / "없는폴더" / "c.json") is False
    assert config.save({"a": 1}, tmp_path / "c.json") is True


def test_frozen_app_root(monkeypatch, tmp_path):
    """배포판(PyInstaller)에서는 exe 폴더가 기준이고 설정 파일도 exe 옆 config.local.json이다 (NFR-02)."""
    exe = tmp_path / "WaveText" / "WaveText.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert config.app_root() == exe.parent


def test_key_files_ignored_by_git():
    """API 키 파일은 저장소에 올라가지 않는다 (NFR-01, m-11)."""
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*config.local.json" in text


def test_build_script_excludes_key_files():
    src = (ROOT / "tools" / "build_portable.py").read_text(encoding="utf-8")
    assert 'for bad in ("config.local.json",)' in src and "--windowed" in src


def test_open_3000_files_headers_fast(tmp_path):
    """3,000개 폴더를 헤더만 읽어 5초 안에 목록을 만든다 (NFR-03)."""
    src = tmp_path / "녹취"
    src.mkdir()
    one = tmp_path / "one.wav"
    wavio.write(one, synth_speech(0.5), 8000, 2)
    data = one.read_bytes()
    for i in range(3000):
        (src / f"f{i:05d}.wav").write_bytes(data)
    t = time.perf_counter()
    files = paths.list_sources(src)
    infos = [wavio.read_info(p) for p in files]
    paths.pair_counts(paths.Folders(src=str(src)))
    assert len(infos) == 3000
    assert time.perf_counter() - t < 5.0
