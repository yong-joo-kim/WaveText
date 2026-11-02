"""녹취 자동 분리: 단어 시간과 원본 에너지로 4~15초 구간을 만든다.

서버 단어에는 구두점이 거의 없어 문장 끝은 어말과 쉼(단어 간격 + 에너지 골짜기)으로 판단한다.
구간은 단어 묶음에서만 만들므로 단어가 없는 묵음은 어느 구간에도 들어가지 않는다.
"""
import bisect
import re
from dataclasses import dataclass

import numpy as np

from wavetext.audio import denoise, segment

MIN_S = 4.0
MAX_S = 15.0
SILENCE_HARD_S = 1.5    # 이 간격 이상은 합치지 않는 강제 경계
PAUSE_S = 0.7           # 쉼 경계 간격 (골짜기 필요)
SENT_GAP_S = 0.2        # 문장 끝 최소 간격 (골짜기 필요)
NO_AUDIO_VALLEY_S = 0.3  # 음성 없이 판단할 때 골짜기로 보는 간격
PAD_S = 0.25            # 구간 앞뒤 여유
FRAME_S = 0.02
SPEECH_PERCENTILE = 80
QUIET_DROP_DB = 20.0    # 말소리 음량보다 이만큼 작으면 골짜기
SENT_END_RE = re.compile(r".(요|다|죠|습니까|십시오)$")
NOT_END = {"필요", "중요", "주요", "수요", "보다", "바다", "마다"}  # 어말처럼 보이는 명사·조사

GRADE_NONE, GRADE_PAUSE, GRADE_SENT, GRADE_HARD = 0, 1, 2, 3


@dataclass
class Seg:
    t0: float
    t1: float
    text: str
    flag: str = ""              # "" / short(4초 미만) / long(15초 초과)
    n: int = 0                  # 저장 번호 (분리 시점에 정해 바꾸지 않는다)
    selected: bool = True
    audio: np.ndarray | None = None   # 저장될 소리 (노이즈 제거·페이드 후)
    saved_text: str | None = None     # 저장된 문장. 아직 저장 안 했으면 None

    @property
    def duration(self):
        return self.t1 - self.t0


def is_sentence_end(word):
    return word.endswith((".", "?", "!")) or (bool(SENT_END_RE.search(word)) and word not in NOT_END)


def _frame_db(mono, rate):
    n = max(1, int(rate * FRAME_S))
    frames = len(mono) // n
    if frames < 1:
        return None, n
    power = np.square(mono[:frames * n].astype(np.float64)).reshape(frames, n).mean(axis=1)
    return 10 * np.log10(power + 1e-12), n


def _boundaries(words, mono, rate):
    """단어 i와 i+1 사이마다 (등급, 간격, 문장 끝 여부, 골짜기 여부, 자를 시각)."""
    db, hop = _frame_db(mono, rate) if mono is not None and rate else (None, 0)
    quiet = None if db is None else np.percentile(db, SPEECH_PERCENTILE) - QUIET_DROP_DB
    out = []
    for w, nxt in zip(words, words[1:]):
        gap = nxt["start"] - w["end"]
        q = (w["end"] + nxt["start"]) / 2 if gap > 0 else nxt["start"]  # 시각이 겹치면 다음 단어 시작에서 자른다
        valley = gap >= NO_AUDIO_VALLEY_S
        if db is not None:
            i0, i1 = int(w["end"] * rate / hop), int(np.ceil(nxt["start"] * rate / hop))
            part = db[i0:i1]
            valley = gap > 0 and len(part) > 0 and part.min() < quiet
            if gap > 0 and len(part):
                q = min(nxt["start"], max(w["end"], (i0 + int(part.argmin()) + 0.5) * hop / rate))
        sent = is_sentence_end(w["word"])
        if gap >= SILENCE_HARD_S:
            grade = GRADE_HARD
        elif sent and gap >= SENT_GAP_S and valley:
            grade = GRADE_SENT
        elif gap >= PAUSE_S and valley:
            grade = GRADE_PAUSE
        else:
            grade = GRADE_NONE
        out.append((grade, gap, sent, valley, q))
    return out


def split(words, total, mono=None, rate=0):
    """words [{"word","start","end"}] → 시간순으로 겹치지 않는 Seg 목록."""
    ws = sorted(({"word": w["word"], "start": max(0.0, min(total, w["start"])), "end": max(0.0, min(total, w["end"]))}
                 for w in words), key=lambda w: (w["start"], w["end"]))
    if not ws:
        return []
    reach = 0.0
    for w in ws:  # 앞 단어에 포함되는 단어도 끝 시각이 뒤로 가지 않게 한다
        reach = w["end"] = max(w["start"], w["end"], reach)
    bounds = _boundaries(ws, mono, rate)

    def edge0(a):
        return max(0.0, ws[a]["start"] - PAD_S) if a == 0 else max(ws[a]["start"] - PAD_S, bounds[a - 1][4])

    def edge1(b):
        return min(total, ws[b]["end"] + PAD_S) if b == len(ws) - 1 else min(ws[b]["end"] + PAD_S, bounds[b][4])

    def length(a, b):
        return edge1(b) - edge0(a)

    def cut_long(a, b):
        """15초를 넘는 묶음을 단어 사이에서 나눈다. 양쪽이 4초 이상인 곳을 먼저 찾는다."""
        out, stack = [], [(a, b)]
        while stack:
            a, b = stack.pop()
            if length(a, b) <= MAX_S or a == b:
                out.append((a, b))
                continue
            cands = [i for i in range(a, b) if length(a, i) >= MIN_S and length(i + 1, b) >= MIN_S] or list(range(a, b))
            mid = (edge0(a) + edge1(b)) / 2
            i = max(cands, key=lambda i: (bounds[i][2] and bounds[i][3], bounds[i][3], bounds[i][1], -abs(bounds[i][4] - mid)))
            stack += [(i + 1, b), (a, i)]
        return out

    groups, a = [], 0
    for i, b in enumerate(bounds):
        if b[0] >= GRADE_PAUSE:
            groups += cut_long(a, i)
            a = i + 1
    groups += cut_long(a, len(ws) - 1)

    def merge_target(k):
        """k번째 묶음을 합칠 이웃 번호. 낮은 등급의 경계, 같으면 좁은 간격을 고른다."""
        cands = []
        for j in (k - 1, k + 1):
            if 0 <= j < len(groups):
                left, right = sorted((k, j))
                g, gap = bounds[groups[left][1]][:2]
                if g < GRADE_HARD and length(groups[left][0], groups[right][1]) <= MAX_S:
                    cands.append((g, gap, j))
        return min(cands)[2] if cands else None

    while True:  # 가장 짧은 조각부터 합친다
        shorts = sorted((length(*g), k) for k, g in enumerate(groups) if length(*g) < MIN_S)
        pick = next(((k, j) for _, k in shorts if (j := merge_target(k)) is not None), None)
        if pick is None:
            break
        left, right = sorted(pick)
        groups[left:right + 1] = [(groups[left][0], groups[right][1])]

    segs = []
    for a, b in groups:
        t0, t1 = edge0(a), edge1(b)
        flag = "long" if t1 - t0 > MAX_S else "short" if t1 - t0 < MIN_S else ""
        segs.append(Seg(t0, t1, " ".join(w["word"] for w in ws[a:b + 1]), flag, selected=not flag))
    return segs


def render(samples, rate, segs, level, profile, stop=None):
    """각 구간의 저장될 소리를 만든다(FR-10과 같은 노이즈 제거 → 페이드). 강도를 다 적용했으면 True."""
    full = True
    for s in segs:
        if stop is not None and stop.is_set():
            break
        raw = segment.cut(samples, rate, s.t0, s.t1)
        applied = level if (level and profile is not None and len(raw) >= profile.nperseg) else 0
        full = full and applied == level
        s.audio = segment.fade(denoise.reduce(raw, rate, profile, applied), rate)
    return full


def index_at(segs, t):
    """t초가 들어 있는 구간 번호. 구간 사이면 가까운 구간, 목록이 비었으면 None."""
    if not segs:
        return None
    i = max(0, bisect.bisect_right([s.t0 for s in segs], t) - 1)
    if t <= segs[i].t1 or i + 1 == len(segs):
        return i
    return i if t - segs[i].t1 <= segs[i + 1].t0 - t else i + 1
