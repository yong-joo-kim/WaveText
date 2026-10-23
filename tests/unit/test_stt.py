import json
import threading

import pytest

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
