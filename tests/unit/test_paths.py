from pathlib import Path

import pytest

from wavetext import paths
from wavetext.paths import Folders


def test_default_subfolders_under_source(tmp_path):
    f = Folders(src=str(tmp_path))
    assert f.full_dir == tmp_path / "full-stt"
    assert f.wav_dir == tmp_path / "part-wav"
    assert f.part_txt_dir == tmp_path / "part-stt"


def test_each_folder_independent(tmp_path):
    f = Folders(src=str(tmp_path), wav=str(tmp_path / "W"))
    assert f.wav_dir == tmp_path / "W"
    assert f.part_txt_dir == tmp_path / "part-stt"  # Wav만 지정해도 Text는 각자 기본 폴더
    assert f.full_dir == tmp_path / "full-stt"


def test_source_required():
    with pytest.raises(ValueError):
        Folders().wav_dir


def test_names(tmp_path):
    f = Folders(src=str(tmp_path))
    assert paths.full_stt_path(f, "a").name == "a.stt.txt"
    assert paths.part_wav_path(f, "a", 3).name == "a.part-3.wav"
    assert paths.part_txt_path(f, "a", 3).name == "a.stt-3.txt"


def test_next_number_uses_both_folders(tmp_path):
    f = Folders(src=str(tmp_path))
    assert paths.next_number(f, "a") == 1
    f.wav_dir.mkdir(); f.part_txt_dir.mkdir()
    (f.wav_dir / "a.part-1.wav").write_bytes(b"")
    (f.part_txt_dir / "a.stt-4.txt").write_text("")
    (f.wav_dir / "ab.part-9.wav").write_bytes(b"")  # 다른 원본
    (f.wav_dir / "a.part-x.wav").write_bytes(b"")   # 규칙 밖
    assert paths.next_number(f, "a") == 5


def test_base_with_glob_chars(tmp_path):
    f = Folders(src=str(tmp_path))
    f.wav_dir.mkdir()
    (f.wav_dir / "a[1].part-2.wav").write_bytes(b"")
    assert paths.next_number(f, "a[1]") == 3


def test_list_sources_excludes_parts(tmp_path):
    for n in ["b.wav", "A.WAV", "a.part-1.wav", "x.txt"]:
        (tmp_path / n).write_bytes(b"")
    assert [p.name for p in paths.list_sources(tmp_path)] == ["A.WAV", "b.wav"]


def test_pair_counts(tmp_path):
    f = Folders(src=str(tmp_path))
    f.wav_dir.mkdir()
    for n in ["a.part-1.wav", "a.part-2.wav", "B.part-1.wav"]:
        (f.wav_dir / n).write_bytes(b"")
    assert paths.pair_counts(f) == {"a": 2, "b": 1}


def test_text_utf8_no_bom(tmp_path):
    p = tmp_path / "한글" / "t.txt"
    paths.write_text(p, "안녕하세요")
    assert p.read_bytes() == "안녕하세요".encode("utf-8")
    assert paths.read_text(p) == "안녕하세요"
    assert paths.read_text(tmp_path / "없음.txt") is None
