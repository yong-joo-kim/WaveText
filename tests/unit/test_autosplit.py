import threading

import numpy as np
import pytest

from wavetext.audio import autosplit, denoise
from wavetext.audio.autosplit import Seg
from wavetext.stt import client


def W(word, start, end):
    return {"word": word, "start": start, "end": end}


def speech(n, step=0.5, start=0.0, tag="말"):
    """step초마다 단어 하나."""
    return [W(f"{tag}{i}", start + i * step, start + i * step + step * 0.8) for i in range(n)]


def covers_all(segs, words):
    assert " ".join(s.text for s in segs).split() == [w["word"] for w in sorted(words, key=lambda w: w["start"])]
    for a, b in zip(segs, segs[1:]):
        assert a.t1 <= b.t0 + 1e-9


def test_empty_and_single_word():
    assert autosplit.split([], 10.0) == []
    segs = autosplit.split([W("네", 2.0, 2.3)], 10.0)
    assert len(segs) == 1 and segs[0].flag == "short" and not segs[0].selected
    assert segs[0].t0 == pytest.approx(1.75) and segs[0].t1 == pytest.approx(2.55)


def test_long_run_is_split_within_limits():
    words = speech(100)  # 50초 연속 발화
    segs = autosplit.split(words, 51.0)
    covers_all(segs, words)
    assert len(segs) >= 4
    assert all(autosplit.MIN_S <= s.duration <= autosplit.MAX_S for s in segs)
    assert all(s.selected and s.flag == "" for s in segs)


def test_sentence_end_preferred_for_long_cut():
    words = speech(40)  # 20초
    words[19]["word"] = "그렇습니다"
    words[19]["end"] -= 0.15 
    words[20]["start"] += 0.1
    segs = autosplit.split(words, 21.0)
    assert any(s.text.endswith("그렇습니다") for s in segs)


def test_hard_silence_never_merged():
    a, b = speech(4), speech(4, start=10.0, tag="뒤")
    segs = autosplit.split(a + b, 14.0)
    assert len(segs) == 2 and all(s.flag == "short" for s in segs)
    assert segs[0].t1 < 3.0 and segs[1].t0 > 9.0   # 묵음은 어느 구간에도 들어가지 않음


def test_short_piece_merges_with_neighbor():
    a, b = speech(14), speech(4, start=8.0, tag="뒤")   # 간격 약 1초(쉼), 합쳐 10초 이내
    segs = autosplit.split(a + b, 12.0)
    assert len(segs) == 1 and segs[0].flag == ""


def test_zero_length_and_overflow_words():
    words = [W("가", 1.0, 1.0), W("나", 1.2, 1.2), W("다", 2.0, 99.0)]
    segs = autosplit.split(words, 3.0)
    covers_all(segs, words)
    assert segs[-1].t1 <= 3.0


def test_word_longer_than_limit_is_flagged():
    segs = autosplit.split([W("아", 1.0, 20.0)], 25.0)
    assert len(segs) == 1 and segs[0].flag == "long" and not segs[0].selected


def test_sentence_end_false_positives():
    assert autosplit.is_sentence_end("맞습니다") and autosplit.is_sentence_end("그렇죠") and autosplit.is_sentence_end("네?")
    assert not autosplit.is_sentence_end("찾아보니까")
    assert not autosplit.is_sentence_end("네")
    assert not autosplit.is_sentence_end("필요")
    assert not autosplit.is_sentence_end("요")


def test_energy_valley_required_for_pause():
    rate = 8000
    words = speech(10) + speech(10, start=6.0, tag="뒤")   # 앞뒤 사이 간격 1.4초
    loud = np.ones(rate * 12, np.float32) * 0.3             # 소리가 끊기지 않음 → 쉼 아님
    assert len(autosplit.split(words, 12.0, loud, rate)) == 1
    quiet = loud.copy()
    quiet[int(4.8 * rate):int(5.9 * rate)] = 0.0            # 소리도 끊김 → 쉼으로 인정
    segs = autosplit.split(words, 12.0, quiet, rate)
    assert len(segs) == 2 and segs[0].t1 <= 5.9


def test_words_in_matches_segments():
    words = speech(60)
    segs = autosplit.split(words, 31.0)
    for s in segs:
        assert client.words_in(words, s.t0, s.t1) == s.text


def test_render_and_stop():
    rate = 8000
    x = (np.random.default_rng(0).normal(size=(rate * 4, 1)) * 0.05).astype(np.float32)
    prof = denoise.estimate_profile(x[:, 0], rate)
    segs = [Seg(0.0, 1.0, "a"), Seg(1.0, 2.0, "b")]
    assert autosplit.render(x, rate, segs, 2, prof) is True
    assert all(len(s.audio) == rate for s in segs)
    stop = threading.Event()
    stop.set()
    fresh = [Seg(0.0, 1.0, "a")]
    autosplit.render(x, rate, fresh, 2, prof, stop)
    assert fresh[0].audio is None


def test_index_at():
    segs = [Seg(1.0, 2.0, "a"), Seg(5.0, 6.0, "b")]
    assert autosplit.index_at([], 1.0) is None
    assert [autosplit.index_at(segs, t) for t in (0.0, 1.5, 2.5, 4.5, 5.5, 9.0)] == [0, 0, 0, 1, 1, 1]


def test_overlapping_word_times_stay_ordered():
    """AS-11: 시각이 겹치거나 앞 단어에 포함되어도 음수 길이 구간이 없고 단어가 빠지지 않는다."""
    words = [W("가", 0.0, 3.0), W("나", 1.0, 1.5), W("다", 2.0, 2.5), W("라", 8.0, 9.0), W("마", 8.5, 8.6)]
    segs = autosplit.split(words, 10.0)
    covers_all(segs, words)
    assert all(s.t1 >= s.t0 for s in segs)


def test_many_unsplittable_words_do_not_overflow_stack():
    """AS-12: 분리 깊이가 단어 수만큼 깊어져도 끝까지 처리한다."""
    words = [W(f"말{i}", i * 20.0, i * 20.0 + 19.99) for i in range(3000)]
    segs = autosplit.split(words, 60000.0)
    assert len(segs) == 3000 and all(s.flag == "long" for s in segs)


def test_punctuated_word_is_sentence_end():
    assert autosplit.is_sentence_end("필요.")


def test_contained_word_is_not_cut():
    """AS-11 잔여: 한 단어가 다른 단어를 포함하면 겹친 곳에서 자르지 않는다(단어를 자르느니 긴 구간으로 둔다)."""
    words = [W("가", 1.0, 25.0), W("나", 18.0, 18.0)]
    segs = autosplit.split(words, 30.0)
    assert len(segs) == 1 and segs[0].t0 <= 1.0 and segs[0].t1 >= 25.0 and segs[0].flag == "long"
    covers_all(segs, words)


def test_zero_length_group_is_merged():
    """AS-11 잔여: 파일 끝에 붙은 0길이 단어가 0길이 구간으로 남지 않는다."""
    words = [W("가", 0.0, 20.0), W("나", 25.0, 25.0)]    # 파일(20초) 밖 단어 → 20~20초로 잘림
    segs = autosplit.split(words, 20.0)
    assert all(s.t1 - s.t0 >= 0.05 for s in segs)
    assert " ".join(s.text for s in segs) == "가 나"
