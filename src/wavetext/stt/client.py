"""ZIEUM 스트리밍 음성인식 클라이언트 (FR-09, FR-11).

입력은 8kHz·16비트·모노 PCM 바이트.
절차: ready(JSON) → PCM 바이너리(chunk_bytes 단위) → finalize → 서버 {"cmd":"finished"} 수신 시 종료.
"""
import json
import threading
import time
import urllib.request

import websocket

BYTES_PER_SEC = 16000  # 8kHz * 16bit * mono


class SttError(Exception):
    def __init__(self, msg, retry=True):
        super().__init__(msg)
        self.retry = retry


def _http_base(server):
    return f"{'https' if server['scheme'] == 'wss' else 'http'}://{server['host']}:{server['port']}"


def health(server, timeout=5):
    """GET /health → (ok, 메시지)."""
    try:
        with urllib.request.urlopen(_http_base(server) + "/health", timeout=timeout) as r:
            return r.status == 200, f"HTTP {r.status}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def build_ready(tr_id, cfg):
    return {
        "cmd": "ready",
        "tr_id": tr_id,
        "audio_encoding": {"sample_rate_hz": 8000, "format": "pcm16"},
        "epd_config": cfg["epd"],
        "result_config": cfg["result"],
        "call_info": cfg.get("call_info", {}),
    }


def merge_messages(msgs):
    """수신 JSON 메시지들에서 최종 인식 텍스트를 구성한다.
    - partial/is_final=false 표시가 있으면 제외
    - segments가 있으면 (start,end)로 중복 제거 후 시간순 연결
    - 없으면 transcript들을 연결하되, 누적형(앞 결과를 포함)이면 마지막 것만 사용"""
    finals = [m for m in msgs
              if isinstance(m, dict) and not m.get("partial") and m.get("is_final", True) is not False]
    segs = {}
    for m in finals:
        for sg in m.get("segments") or []:
            if isinstance(sg, dict) and sg.get("transcript"):
                segs[(sg.get("start"), sg.get("end"))] = sg
    if segs:
        ordered = sorted(segs.values(), key=lambda s: (s.get("start") or 0, s.get("end") or 0))
        return " ".join(s["transcript"].strip() for s in ordered)
    texts = [m.get("transcript", "") for m in finals if m.get("transcript")]
    if not texts:
        return ""
    out = [texts[0]]
    for t in texts[1:]:
        if t.startswith(out[-1]):
            out[-1] = t
        else:
            out.append(t)
    return " ".join(x.strip() for x in out)


def extract_words(msgs):
    """최종 인식 메시지의 단어별 시간 정보 [{"word","start","end"}] (result_config.word_alignment=true일 때).
    서버는 여러 메시지로 나눠 보내도 시간은 파일 처음 기준(초)이다. 중복은 (단어, 시작, 끝)으로 제거한다."""
    seen, out = set(), []
    for m in msgs:
        if not isinstance(m, dict) or m.get("partial") or m.get("is_final", True) is False or m.get("final") is False:
            continue
        for w in m.get("words") or []:
            if not isinstance(w, dict) or not str(w.get("word", "")).strip():
                continue
            try:
                item = {"word": str(w["word"]).strip(), "start": float(w["start"]), "end": float(w["end"])}
            except (KeyError, TypeError, ValueError):
                continue
            k = (item["word"], item["start"], item["end"])
            if k not in seen:
                seen.add(k)
                out.append(item)
    return sorted(out, key=lambda w: (w["start"], w["end"]))


def words_in(words, t0, t1, offset=0.0):
    """t0~t1(초) 구간에 들어가는 단어로 문장을 만든다. 단어 가운데 시각이 구간 안에 있으면 포함."""
    a, b = sorted((t0, t1))
    return " ".join(w["word"] for w in words if a <= (w["start"] + w["end"]) / 2 + offset <= b)


def word_spans(text, words, offset=0.0):
    """인식 결과 글자에서 단어별 위치를 찾는다 [(글자 시작, 글자 끝, 시작 초, 끝 초)].
    앞에서부터 차례로 찾으므로 같은 단어가 여러 번 나와도 순서대로 맞춰지고, 글을 고쳐 못 찾는 단어는 건너뛴다."""
    out, pos = [], 0
    for w in words:
        i = text.find(w["word"], pos)
        if i < 0:
            continue
        pos = i + len(w["word"])
        out.append((i, pos, w["start"] + offset, w["end"] + offset))
    return out


def span_at(spans, t):
    """t초에 해당하는 단어 번호: 말하는 중이면 그 단어, 단어 사이면 바로 앞 단어, 첫 단어 전이면 첫 단어."""
    if not spans:
        return None
    k = 0
    for i, s in enumerate(spans):
        if s[2] <= t:
            k = i
        else:
            break
    return k


def _scrub(text, key):
    return text.replace(key, "***") if key else text


ERROR_STATUS = (400, 401, 404, 500)


def _close(ws, graceful):
    """정상 종료면 닫기 인사를 하고 닫는다. 비정상(중지·무응답·오류)이면 수신 중인 스레드를 기다리지 않도록
    소켓을 바로 끊는다 — close()는 수신 스레드가 쥔 잠금 때문에 다음 수신 시간제한까지 막힐 수 있다 (PX-5)."""
    try:
        if not graceful and hasattr(ws, "shutdown"):
            ws.shutdown()
        else:
            ws.close(timeout=1)
    except Exception:
        pass


def transcribe(pcm, cfg, model, tr_id, on_progress=None, stop=None, connect=None):
    """PCM(8kHz·16비트·모노) 1건을 인식. dict(transcript, messages) 반환.

    connect: 시험용 웹소켓 생성 함수 주입 (기본 websocket.create_connection).
    """
    server = cfg["server"]
    key = cfg.get("api_key", "")
    url = f"{server['scheme']}://{server['host']}:{server['port']}/v1/streaming?model={model}"
    timeout = cfg["retry"]["timeout_s"]
    connect = connect or websocket.create_connection
    try:
        hname = cfg.get("auth_header", "X-API-Key")
        hval = f"Bearer {key}" if hname.lower() == "authorization" else key
        ws = connect(url, header=[f"{hname}: {hval}"], timeout=timeout)
    except websocket.WebSocketBadStatusException as e:
        code = getattr(e, "status_code", None)
        raise SttError(f"서버 거부({code})", retry=code not in (400, 401, 404))
    except Exception as e:
        raise SttError(_scrub(f"연결 실패: {type(e).__name__}: {e}", key))

    msgs = []
    st = {"err": None, "done": False, "finished": False, "failed": False}

    def reader():
        try:
            while True:
                try:
                    data = ws.recv()
                except websocket.WebSocketTimeoutException:  # 긴 발화는 끝날 때까지 응답이 없다. 끝났는지는 아래에서 판단한다
                    if st["done"]:
                        break
                    continue
                if data == "" or data is None:
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                msgs.append((time.perf_counter(), obj))
                if isinstance(obj, dict) and obj.get("cmd") == "finished":
                    st["finished"] = True
                    break
                if isinstance(obj, dict) and obj.get("status") in ERROR_STATUS:  # 오류 status면 더 기다리지 않는다 (PX-5)
                    st["failed"] = True
                    break
        except Exception as e:
            if not st["done"]:
                st["err"] = e
        finally:
            st["done"] = True

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    try:
        ws.send(json.dumps(build_ready(tr_id, cfg)))
        total = max(1, len(pcm))
        chunk = int(cfg.get("chunk_bytes", 640))
        for pos in range(0, len(pcm), chunk):
            if stop is not None and stop.is_set():
                raise SttError("사용자 중지", retry=False)
            if st["done"]:  # 서버가 오류를 보냈거나 연결을 끊었으면 남은 음성을 보내지 않는다 (PX-8)
                break
            data = pcm[pos:pos + chunk]
            ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
            if on_progress:
                on_progress(min(1.0, (pos + len(data)) / total))
            if cfg.get("realtime_pacing", False):
                time.sleep(len(data) / BYTES_PER_SEC)
        if not st["done"]:
            ws.send(json.dumps({"cmd": "finalize"}))
        t_end = time.perf_counter()
        # 페이싱을 끄면 전송이 먼저 끝나고 서버는 음성을 차례로 처리한다. 긴 발화나 묵음은 끝나야 응답이 오므로 음성 길이만큼 더 기다린다
        wait_s = timeout if cfg.get("realtime_pacing", False) else timeout + len(pcm) / BYTES_PER_SEC
        while not st["done"]:
            if stop is not None and stop.is_set():
                raise SttError("사용자 중지", retry=False)
            if st["finished"]:
                break
            if time.perf_counter() - max(t_end, msgs[-1][0] if msgs else t_end) > wait_s:
                break
            time.sleep(0.05)
    except SttError:
        raise
    except Exception as e:
        raise SttError(_scrub(f"전송 실패: {e}", key))
    finally:
        st["done"] = True
        _close(ws, graceful=st["finished"])
    objs = [m for _, m in msgs]
    for m in objs:
        if isinstance(m, dict) and m.get("status") in ERROR_STATUS:
            raise SttError(f"서버 오류 {m.get('status')}", retry=m.get("status") == 500)
    if not objs:
        raise SttError("응답 없음" + (f" ({_scrub(str(st['err']), key)})" if st["err"] else ""))
    if not st["finished"]:  # 일부만 받은 결과를 학습 데이터로 저장하지 않는다
        raise SttError("서버 결과를 끝까지 받지 못했습니다" + (f" ({_scrub(str(st['err']), key)})" if st["err"] else ""))
    return {"transcript": merge_messages(objs), "words": extract_words(objs), "messages": objs}


def transcribe_with_retry(pcm, cfg, model, tr_id, on_progress=None, stop=None, connect=None):
    """retry.count 만큼 재시도 (사용자 중지·인증 오류는 즉시 실패)."""
    tries = int(cfg.get("retry", {}).get("count", 0)) + 1
    last = None
    for i in range(tries):
        try:
            return transcribe(pcm, cfg, model, f"{tr_id}-{i}" if i else tr_id, on_progress, stop, connect)
        except SttError as e:
            last = e
            if not e.retry or (stop is not None and stop.is_set()):
                break
    raise last
