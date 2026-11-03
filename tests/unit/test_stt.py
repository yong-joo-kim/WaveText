import json
import threading
import time

import pytest
import websocket

from wavetext import config
from wavetext.stt import client


class FakeWS:
    """서버 흉내: ready·PCM·finalize를 받고, finalize 후 결과와 finished를 돌려준다."""

    def __init__(self, replies, fail_status=None):
        self.sent = []
        self.replies = list(replies)
        self.finalized = threading.Event()
        self.fail_status = fail_status

    def send(self, data, opcode=None):
        self.sent.append(data)
        if isinstance(data, str) and json.loads(data).get("cmd") == "finalize":
            self.finalized.set()

    def recv(self):
        self.finalized.wait(2)
        return json.dumps(self.replies.pop(0)) if self.replies else ""

    def close(self, timeout=1):
        pass


def cfg():
    c = json.loads(json.dumps(config.STT_DEFAULT))
    c["server"]["host"] = "127.0.0.1"
    c["api_key"] = "SECRETKEY123"
    c["realtime_pacing"] = False
    return c


def test_protocol_and_merge():
    ws = FakeWS([{"transcript": "안녕하세요", "segments": [{"start": 0, "end": 1, "transcript": "안녕하세요"}]},
                 {"segments": [{"start": 1, "end": 2, "transcript": "반갑습니다"}]}, {"cmd": "finished"}])
    seen = {}

    def connect(url, header, timeout):
        seen["url"], seen["header"] = url, header
        return ws

    prog = []
    r = client.transcribe(b"\x00\x00" * 1000, cfg(), "model_v2", "t1", on_progress=prog.append, connect=connect)
    assert r["transcript"] == "안녕하세요 반갑습니다"
    assert seen["url"].endswith("/v1/streaming?model=model_v2")
    assert seen["header"] == ["X-API-Key: SECRETKEY123"]
    ready = json.loads(ws.sent[0])
    assert ready["cmd"] == "ready" and ready["audio_encoding"] == {"sample_rate_hz": 8000, "format": "pcm16"}
    assert sum(len(b) for b in ws.sent[1:-1]) == 2000 and all(len(b) <= 640 for b in ws.sent[1:-1])
    assert json.loads(ws.sent[-1]) == {"cmd": "finalize"}
    assert prog[-1] == 1.0


def test_cumulative_transcripts_take_last():
    assert client.merge_messages([{"transcript": "안녕"}, {"transcript": "안녕하세요"}, {"partial": True, "transcript": "x"}]) == "안녕하세요"


def test_connection_error_hides_key():
    def connect(url, header, timeout):
        raise OSError("refused SECRETKEY123")
    with pytest.raises(client.SttError) as e:
        client.transcribe(b"\x00\x00", cfg(), "m", "t", connect=connect)
    assert "SECRETKEY123" not in str(e.value)


def test_server_error_status():
    ws = FakeWS([{"status": 401}, {"cmd": "finished"}])
    with pytest.raises(client.SttError) as e:
        client.transcribe(b"\x00\x00" * 10, cfg(), "m", "t", connect=lambda *a, **k: ws)
    assert "401" in str(e.value) and not e.value.retry


def test_user_stop():
    stop = threading.Event()
    stop.set()
    ws = FakeWS([])
    with pytest.raises(client.SttError) as e:
        client.transcribe(b"\x00\x00" * 1000, cfg(), "m", "t", stop=stop, connect=lambda *a, **k: ws)
    assert "중지" in str(e.value)


def test_retry_then_success():
    calls = []

    def connect(url, header, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("일시 오류")
        return FakeWS([{"transcript": "됨"}, {"cmd": "finished"}])
    r = client.transcribe_with_retry(b"\x00\x00" * 10, cfg(), "m", "t", connect=connect)
    assert r["transcript"] == "됨" and len(calls) == 2


def test_load_stt_from_own_settings():
    """음성인식 설정은 WaveText 설정만 쓴다. 빈 접속 방식·포트는 기본값(ws / 8081)."""
    cfg = {"model": "model_v1", "stt": {"server": {"scheme": "", "host": "10.0.0.10", "port": 0}, "api_key": "abcdefghijk"}}
    stt = config.load_stt(cfg)
    assert stt["server"] == {"scheme": "ws", "host": "10.0.0.10", "port": 8081}
    assert stt["api_key"] == "abcdefghijk" and stt["models"] == ["model_v1"]
    assert stt["epd"]["auto_epd"] is True and stt["chunk_bytes"] == 640
    assert config.mask("abcdef") == "********"  # 짧은 키는 앞부분도 숨김
    assert config.mask("sk-1234567890") == "sk-********"


def test_default_config_has_no_key_and_no_other_product(tmp_path):
    cfg = config.load(tmp_path / "없음.json")
    assert cfg["stt"]["server"] == {"scheme": "ws", "host": "", "port": 8081}   # 서버 주소 기본값 없음
    with pytest.raises(config.SttConfigError, match="서버 주소"):
        config.load_stt(dict(cfg, stt={"server": cfg["stt"]["server"], "api_key": "k" * 12}))
    assert cfg["stt"]["api_key"] == "" and cfg["model"] == "model_v2" and "cer_config" not in cfg


def test_load_stt_missing_key_or_host():
    with pytest.raises(config.SttConfigError, match="API 키"):
        config.load_stt({"stt": {"server": {"host": "h"}, "api_key": ""}})


def test_own_settings_used_as_is():
    cfg = {"stt": {"server": {"scheme": "wss", "host": "my.server", "port": 81}, "api_key": "MYKEY0000000"}}
    stt = config.load_stt(cfg)
    assert stt["server"] == {"scheme": "wss", "host": "my.server", "port": 81} and stt["api_key"] == "MYKEY0000000"


def test_old_cer_config_key_ignored(tmp_path):
    """예전 버전 설정의 cer_config 항목은 읽지 않고 버린다."""
    import json as _json
    p = tmp_path / "c.json"
    p.write_text(_json.dumps({"cer_config": "D:/x/config.local.json"}), encoding="utf-8")
    assert "cer_config" not in config.load(p)


def test_extract_words_and_words_in():
    """실서버 응답 형식(여러 recognize 메시지, 파일 처음 기준 시간)에서 단어 시간을 모으고 구간 문장을 만든다."""
    msgs = [{"cmd": "create"}, {"cmd": "ready"},
            {"cmd": "recognize", "final": True, "transcript": " 반갑습니다 정입니다",
             "words": [{"word": "반갑습니다", "start": 0.88, "end": 1.4, "score": -0.1}, {"word": "정입니다", "start": 2.2, "end": 2.76}]},
            {"cmd": "recognize", "final": True, "transcript": " 여섯 시까지",
             "words": [{"word": "여섯", "start": 6.72, "end": 6.9}, {"word": "시까지", "start": 7.0, "end": 7.4}]},
            {"cmd": "recognize", "final": True, "words": []}, {"cmd": "finished"}]
    words = client.extract_words(msgs)
    assert [w["word"] for w in words] == ["반갑습니다", "정입니다", "여섯", "시까지"]
    assert client.words_in(words, 2.0, 7.1) == "정입니다 여섯"       # 시까지(가운데 7.2초)는 구간 밖
    assert client.words_in(words, 7.1, 2.0) == "정입니다 여섯"       # 역순 구간
    assert client.words_in(words, 3.0, 6.0) == ""
    assert client.merge_messages(msgs) == "반갑습니다 정입니다 여섯 시까지"


def test_word_spans_and_span_at():
    words = [{"word": "안녕", "start": 0.0, "end": 0.4}, {"word": "하세요", "start": 0.5, "end": 0.9},
             {"word": "안녕", "start": 1.2, "end": 1.5}, {"word": "없는말", "start": 2.0, "end": 2.2}]
    text = "안녕 하세요. 안녕"
    sp = client.word_spans(text, words)
    assert [text[a:b] for a, b, *_ in sp] == ["안녕", "하세요", "안녕"]   # 같은 단어도 순서대로, 못 찾으면 건너뜀
    assert sp[2][0] == 8
    assert client.span_at(sp, 0.6) == 1          # 말하는 중
    assert client.span_at(sp, 1.0) == 1          # 단어 사이 → 앞 단어
    assert client.span_at(sp, 5.0) == 2
    assert client.span_at(sp, -1.0) == 0
    assert client.span_at([], 1.0) is None
    assert client.word_spans(text, words, offset=10.0)[0][2:] == (10.0, 10.4)  # 선택 구간 인식 시간 보정


class SlowWS(FakeWS):
    """긴 발화처럼 응답 사이가 비는 서버: steps의 값 None은 수신 시간제한, 숫자는 그만큼 쉰 뒤 다음 응답."""

    def __init__(self, replies, steps):
        super().__init__(replies)
        self.steps = list(steps)

    def recv(self):
        if self.steps:
            step = self.steps.pop(0)
            if step is None:
                raise websocket.WebSocketTimeoutException("timed out")
            time.sleep(step)
        return super().recv()


def words_reply(word, t):
    return {"transcript": word, "words": [{"word": word, "start": t, "end": t + 0.5}]}


def test_receive_timeout_during_long_utterance_keeps_reading():
    """소켓 수신 시간제한(긴 발화로 응답이 없음)이 나도 뒤 결과를 버리지 않는다."""
    ws = SlowWS([words_reply("앞", 1.0), words_reply("뒤", 200.0), {"cmd": "finished"}], [0, None, None])
    r = client.transcribe(b"\x00\x00" * 10, cfg(), "m", "t", connect=lambda *a, **k: ws)
    assert [w["word"] for w in r["words"]] == ["앞", "뒤"]


def test_gap_after_finalize_waits_for_finished():
    """응답 사이가 2초 벌어져도 finished까지 받는다."""
    ws = SlowWS([words_reply("앞", 1.0), words_reply("뒤", 200.0), {"cmd": "finished"}], [0, 2.0])
    r = client.transcribe(b"\x00\x00" * 10, cfg(), "m", "t", connect=lambda *a, **k: ws)
    assert [w["word"] for w in r["words"]] == ["앞", "뒤"]


def test_missing_finished_is_an_error():
    """끝까지 받지 못한 결과는 성공으로 돌려주지 않는다."""
    ws = FakeWS([words_reply("앞", 1.0)])      # finished 없이 연결 종료
    with pytest.raises(client.SttError) as e:
        client.transcribe(b"\x00\x00" * 10, cfg(), "m", "t", connect=lambda *a, **k: ws)
    assert "끝까지" in str(e.value)


def test_pacing_off_waits_longer_than_timeout_for_long_audio():
    """페이싱을 끄면 긴 발화의 응답이 시간제한보다 늦어도 기다린다 (음성 2초 → 1초 공백 허용)."""
    c = cfg()
    c["retry"]["timeout_s"] = 0.5
    ws = SlowWS([words_reply("앞", 1.0), words_reply("뒤", 200.0), {"cmd": "finished"}], [0, 1.0])
    r = client.transcribe(b"\x00\x00" * 16000, c, "m", "t", connect=lambda *a, **k: ws)
    assert [w["word"] for w in r["words"]] == ["앞", "뒤"]


def test_wait_ends_when_server_stays_silent():
    """대기에는 끝이 있다: 응답이 끊기면 음성 길이 + 시간제한 뒤 실패한다."""
    c = cfg()
    c["retry"]["timeout_s"] = 0.3
    ws = SlowWS([words_reply("앞", 1.0)], [0, 5.0])
    t = time.perf_counter()
    with pytest.raises(client.SttError):
        client.transcribe(b"\x00\x00" * 160, c, "m", "t", connect=lambda *a, **k: ws)
    assert time.perf_counter() - t < 4


class StuckWS(FakeWS):
    """수신이 잠금을 쥔 채 막히는 소켓: close()는 수신이 풀릴 때까지(3초) 막히고, shutdown()은 바로 끊는다."""

    def __init__(self, replies=(), first_now=False):
        super().__init__(replies)
        self.released = threading.Event()
        self.first_now = first_now
        self.shut = False

    def recv(self):
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline:
            if self.replies and (self.first_now or self.finalized.is_set()):  # first_now: 전송 시작 때 바로 온다
                return json.dumps(self.replies.pop(0))
            if self.released.wait(0.02):
                raise OSError("socket closed")
        return ""

    def close(self, timeout=1):
        self.released.wait(3)

    def shutdown(self):
        self.shut = True
        self.released.set()


def test_abnormal_end_does_not_wait_for_receive_lock():
    """PX-5: 무응답으로 끝나면 close의 잠금 대기 없이 소켓을 바로 끊는다."""
    c = cfg()
    c["retry"]["timeout_s"] = 0.3
    ws = StuckWS()
    t = time.perf_counter()
    with pytest.raises(client.SttError):
        client.transcribe(b"\x00\x00" * 160, c, "m", "t", connect=lambda *a, **k: ws)
    assert ws.shut and time.perf_counter() - t < 2


def test_error_status_ends_wait_immediately():
    """PX-5: 서버가 오류 status만 보내고 닫지 않아도 바로 오류로 끝난다."""
    c = cfg()
    c["retry"]["timeout_s"] = 5
    ws = StuckWS([{"status": 500, "message": "busy"}])
    t = time.perf_counter()
    with pytest.raises(client.SttError) as e:
        client.transcribe(b"\x00\x00" * 160, c, "m", "t", connect=lambda *a, **k: ws)
    assert "500" in str(e.value) and time.perf_counter() - t < 2


def test_sending_stops_when_server_reports_error():
    """PX-8: 페이싱 켬에서 전송 중 오류 status가 오면 남은 음성을 보내지 않는다."""
    c = cfg()
    c["realtime_pacing"] = True
    ws = StuckWS([{"status": 401, "message": "bad key"}], first_now=True)
    pcm = b"\x00\x00" * 16000 * 3                       # 6초 음성 (8kHz·16비트는 1초에 16000바이트)
    t = time.perf_counter()
    with pytest.raises(client.SttError) as e:
        client.transcribe(pcm, c, "m", "t", connect=lambda *a, **k: ws)
    assert "401" in str(e.value) and time.perf_counter() - t < 1.5
    assert sum(len(b) for b in ws.sent if isinstance(b, bytes)) < len(pcm) // 2


class FakeSock:
    def __init__(self, fail=False):
        self.opts, self.ioctls, self.fail = [], [], fail

    def setsockopt(self, *a):
        if self.fail:
            raise OSError("not supported")
        self.opts.append(a)

    def ioctl(self, *a):
        self.ioctls.append(a)


def test_keepalive_enabled_on_connection():
    """C-8: 연결에 TCP keepalive를 켜서 신호 없이 끊긴 연결을 운영체제가 알아채게 한다."""
    import socket
    ws = FakeWS([words_reply("앞", 1.0), {"cmd": "finished"}])
    ws.sock = FakeSock()
    client.transcribe(b"\x00\x00" * 10, cfg(), "m", "t", connect=lambda *a, **k: ws)
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in ws.sock.opts
    if hasattr(socket, "SIO_KEEPALIVE_VALS"):
        assert ws.sock.ioctls == [(socket.SIO_KEEPALIVE_VALS, (1, 10000, 2000))]
    assert client._enable_keepalive(FakeWS([])) is False          # 소켓이 없으면 그냥 넘어간다
    bad = FakeWS([])
    bad.sock = FakeSock(fail=True)
    assert client._enable_keepalive(bad) is False                  # 지원하지 않아도 인식은 계속한다


def test_keepalive_on_real_socket():
    """실제 소켓에도 옵션이 적용된다."""
    import socket

    class Holder:
        pass

    h = Holder()
    h.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        assert client._enable_keepalive(h) is True
        assert h.sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
    finally:
        h.sock.close()
