"""저장 폴더 결정, 파일 이름·번호 규칙, 원본 목록 (FR-01~03, FR-10~12, FR-16).

- 원본 음성인식 Text: {원본이름}.stt.txt     (미지정 시 원본\\full-stt)
- 분할 Wav:          {원본이름}.part-N.wav  (미지정 시 원본\\part-wav)
- 분할 음성인식 Text: {원본이름}.stt-N.txt   (미지정 시 원본\\part-stt)
"""
import re
from dataclasses import dataclass
from pathlib import Path

PART_WAV_RE = re.compile(r"^(?P<base>.+)\.part-(?P<n>\d+)\.wav$", re.IGNORECASE)
PART_TXT_RE = re.compile(r"^(?P<base>.+)\.stt-(?P<n>\d+)\.txt$", re.IGNORECASE)
DEFAULT_SUBDIRS = {"full": "full-stt", "wav": "part-wav", "part_txt": "part-stt"}


@dataclass
class Folders:
    src: str = ""
    full: str = ""      # 원본 음성인식 Text 폴더
    wav: str = ""       # 분할 Wav 폴더
    part_txt: str = ""  # 분할 음성인식 Text 폴더

    def resolve(self, key):
        """지정했으면 그 폴더, 비었으면 원본 아래 기본 폴더."""
        value = getattr(self, key).strip()
        if value:
            return Path(value)
        if not self.src.strip():
            raise ValueError("원본 음성 폴더가 지정되지 않았습니다.")
        return Path(self.src.strip()) / DEFAULT_SUBDIRS[key]

    @property
    def full_dir(self):
        return self.resolve("full")

    @property
    def wav_dir(self):
        return self.resolve("wav")

    @property
    def part_txt_dir(self):
        return self.resolve("part_txt")


def full_stt_path(folders, base):
    return folders.full_dir / f"{base}.stt.txt"


def meta_path(folders, base):
    """원본 인식 단어 시간과 분할 구간 기록: 원본 음성인식 Text 폴더의 {원본이름}.stt.json"""
    return folders.full_dir / f"{base}.stt.json"


def load_meta(folders, base):
    """{"words": [{"word","start","end"}], "parts": {"N": [시작, 끝]}} — 없거나 깨졌으면 빈 기록."""
    import json
    try:
        m = json.loads(Path(meta_path(folders, base)).read_text(encoding="utf-8"))
        if isinstance(m, dict):
            return {"words": m.get("words") or [], "parts": m.get("parts") or {}}
    except (OSError, ValueError):
        pass
    return {"words": [], "parts": {}}


def save_meta(folders, base, meta):
    import json
    write_text(meta_path(folders, base), json.dumps(meta, ensure_ascii=False, indent=1))


def part_wav_path(folders, base, n):
    return folders.wav_dir / f"{base}.part-{n}.wav"


def part_txt_path(folders, base, n):
    return folders.part_txt_dir / f"{base}.stt-{n}.txt"


def _numbers(directory, regex, base):
    nums = set()
    if directory.is_dir():
        for p in directory.glob(f"{glob_escape(base)}.*"):
            m = regex.match(p.name)
            if m and m.group("base").lower() == base.lower():
                nums.add(int(m.group("n")))
    return nums


def glob_escape(s):
    return re.sub(r"([\[\]\*\?])", r"[\1]", s)


def existing_numbers(folders, base):
    return _numbers(folders.wav_dir, PART_WAV_RE, base) | _numbers(folders.part_txt_dir, PART_TXT_RE, base)


def next_number(folders, base):
    """두 폴더의 기존 번호 중 최댓값 + 1 (FR-12)."""
    nums = existing_numbers(folders, base)
    return max(nums) + 1 if nums else 1


def list_sources(src):
    """원본 폴더의 .wav (분할 파일 제외), 이름순."""
    src = Path(src)
    if not src.is_dir():
        return []
    files = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() == ".wav" and not PART_WAV_RE.match(p.name)]
    return sorted(files, key=lambda p: p.name.lower())


def pair_counts(folders):
    """분할 Wav 폴더를 한 번만 훑어 원본이름 → 쌍 개수 (FR-03, NFR-03)."""
    counts = {}
    d = folders.wav_dir
    if d.is_dir():
        for p in d.iterdir():
            m = PART_WAV_RE.match(p.name)
            if m:
                key = m.group("base").lower()
                counts[key] = counts.get(key, 0) + 1
    return counts


def read_text(path):
    """UTF-8(BOM 허용)로 읽고, 실패하면 예전 한글 윈도우 형식(CP949)으로 읽는다. 없으면 None."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


def write_text(path, text):
    """UTF-8(BOM 없음)으로 저장 (NFR-08)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_text_new(path, text):
    """파일이 없을 때만 만든다. 이미 있으면(사용자가 먼저 저장함) False — 늦게 온 인식 결과가 덮어쓰지 않게."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return True
    except FileExistsError:
        return False
