from pathlib import Path

import pytest

from wavetext import paths
from wavetext.paths import Folders


def test_default_subfolders_under_source(tmp_path):
    f = Folders(src=str(tmp_path))
    assert f.full_dir == tmp_path / "full-stt"
    assert f.wav_dir == tmp_path / "manual-part-wav"
    assert f.part_txt_dir == tmp_path / "manual-part-stt"
    assert f.auto_wav_dir == tmp_path / "auto-part-wav"
    assert f.auto_txt_dir == tmp_path / "auto-part-stt"


def test_each_folder_independent(tmp_path):
    f = Folders(src=str(tmp_path), wav=str(tmp_path / "W"), auto_txt=str(tmp_path / "AT"))
    assert f.wav_dir == tmp_path / "W"
    assert f.part_txt_dir == tmp_path / "manual-part-stt"  # Wav만 지정해도 Text는 각자 기본 폴더
    assert f.full_dir == tmp_path / "full-stt"
    assert f.auto_txt_dir == tmp_path / "AT" and f.auto_wav_dir == tmp_path / "auto-part-wav"


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


def test_auto_paths_numbers_and_remove(tmp_path):
    f = Folders(src=str(tmp_path), full="", wav=str(tmp_path / "w"), part_txt="")
    assert paths.auto_wav_path(f, "a", 3) == tmp_path / "auto-part-wav" / "a.part-3.wav"
    assert paths.auto_txt_path(f, "a", 3) == tmp_path / "auto-part-stt" / "a.stt-3.txt"
    assert paths.auto_next_number(f, "a") == 1
    paths.write_text(paths.auto_wav_path(f, "a", 2), "x")
    paths.write_text(paths.auto_txt_path(f, "a", 5), "x")
    assert paths.auto_numbers(f, "a") == {2, 5} and paths.auto_next_number(f, "a") == 6
    paths.auto_remove(f, "a", 2)
    assert paths.auto_numbers(f, "a") == {5}
    with pytest.raises(ValueError):
        Folders().auto_wav_dir
    g = Folders(src=str(tmp_path), auto_wav=str(tmp_path / "AW"))   # 직접 지정한 자동 분할 폴더
    assert paths.auto_wav_path(g, "a", 1) == tmp_path / "AW" / "a.part-1.wav"


def test_split_record_roundtrip(tmp_path):
    f = Folders(src=str(tmp_path))
    assert paths.load_split(f, "a") is None
    paths.write_text(paths.meta_path(f, "a"), '{"words": [], "parts": {"1": [0, 1]}}')
    before = paths.meta_path(f, "a").read_text(encoding="utf-8")
    paths.save_split(f, "a", {"duration": 9.5, "nr": 2, "segments": [{"n": 1, "text": "가"}]})
    got = paths.load_split(f, "a")
    assert got["duration"] == 9.5 and got["segments"][0]["text"] == "가"
    assert paths.meta_path(f, "a").read_text(encoding="utf-8") == before  # stt.json은 그대로
    paths.split_path(f, "a").write_text("{깨진", encoding="utf-8")
    assert paths.load_split(f, "a") is None


def test_pair_counts_include_auto_folder(tmp_path):
    f = Folders(src=str(tmp_path))
    paths.write_text(paths.part_wav_path(f, "a", 1), "x")
    paths.write_text(paths.auto_wav_path(f, "a", 1), "x")
    paths.write_text(paths.auto_wav_path(f, "a", 2), "x")
    paths.write_text(paths.auto_wav_path(f, "b", 1), "x")
    assert paths.pair_counts(f) == {"a": 3, "b": 1}
    assert paths.pair_count(f, "a") == 3 and paths.pair_count(f, "없음") == 0


def test_folder_conflict_between_manual_and_auto(tmp_path):
    """V3-2: 사용자 분할 폴더와 자동 분할 폴더가 같으면 알린다."""
    assert paths.folder_conflict(Folders(src=str(tmp_path))) is None                     # 기본 폴더는 모두 다름
    same = Folders(src=str(tmp_path), wav=str(tmp_path / "X"), auto_wav=str(tmp_path / "X"))
    assert paths.folder_conflict(same) == ("wav", "auto_wav")
    assert "사용자 분할 음성 Wav" in paths.folder_conflict_message(same)
    txt = Folders(src=str(tmp_path), part_txt=str(tmp_path / "auto-part-stt"))            # 자동 기본 폴더와 같음
    assert paths.folder_conflict(txt) == ("part_txt", "auto_txt")
    assert paths.folder_conflict(Folders()) is None                                        # 원본 미지정은 비교 안 함


def test_folders_from_dict_ignores_unknown_keys():
    """V3-3: 다른 버전 설정의 모르는 키·문자열 아닌 값은 무시한다."""
    f = Folders.from_dict({"src": "D:/a", "wav": 3, "bogus": "x", "auto_txt": "D:/t"})
    assert (f.src, f.wav, f.auto_txt) == ("D:/a", "", "D:/t")
    assert Folders.from_dict(None) == Folders()


def test_folders_from_dict_non_dict_uses_defaults():
    """V3R-3: folders 값이 사전이 아니어도 기본값으로 시작한다."""
    for bad in ("D:/a", ["x"], 3, True):
        assert Folders.from_dict(bad) == Folders()
