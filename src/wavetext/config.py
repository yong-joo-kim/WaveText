"""WaveText 설정과 음성인식 서버 설정 (FR-20, NFR-01).

모든 설정은 실행 폴더의 config.local.json 하나에만 저장한다(저장소·배포판에서 제외).
음성인식 서버(서버 주소·포트·접속 방식·API 키·모델)는 [설정] → [음성인식 서버 설정]에서 입력한다.
다른 프로그램의 설정 파일은 읽지 않는다.
"""
import copy
import json
import sys
from pathlib import Path


def app_root():
    if getattr(sys, "frozen", False):  # PyInstaller 배포판
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


CONFIG_PATH = app_root() / "config.local.json"

# 음성인식 서버 기본값 (서버 주소와 API 키는 기본값 없이 처음 한 번 입력한다)
DEFAULT_SERVER = {"scheme": "ws", "host": "", "port": 8081}  # 서버 주소는 PC마다 [음성인식 서버 설정]에서 입력
DEFAULT_MODEL = "model_v2"

DEFAULT = {
    "model": DEFAULT_MODEL,
    "stt": {"server": dict(DEFAULT_SERVER), "api_key": ""},
    "nr_level": 2,        # 노이즈 제거: 0 사용 안 함, 1 약, 2 중, 3 강
    "folders": {"src": "", "full": "", "wav": "", "part_txt": ""},
}

# ZIEUM 스트리밍 음성인식 요청에 쓰는 고정 항목
STT_DEFAULT = {
    "server": dict(DEFAULT_SERVER),
    "api_key": "",
    "auth_header": "X-API-Key",
    "chunk_bytes": 640,
    "call_info": {"call_id": "wavetext", "customer_phone": "", "in_out_bound": "IB", "consultant_id": "wavetext"},
    "models": [DEFAULT_MODEL],
    "realtime_pacing": True,
    "epd": {"auto_epd": True, "speech_start_timeout": 5000.0, "speech_end_silence_length": 1000.0, "max_speech_length": 600000.0},
    "result": {"partial_results": False, "word_alignment": False},
    "retry": {"count": 2, "timeout_s": 30, "settle_s": 1.5},
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None):
    cfg = copy.deepcopy(DEFAULT)
    p = Path(path or CONFIG_PATH)
    if p.exists():
        try:
            _merge(cfg, json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass  # 깨진 설정은 기본값으로 시작
    cfg.pop("cer_config", None)  # 예전 버전 설정 항목 정리
    return cfg


def save(cfg, path=None):
    """쓰기 금지 폴더(읽기 전용 USB 등)에 풀어 실행해도 작업은 계속되도록 실패를 돌려주기만 한다."""
    try:
        Path(path or CONFIG_PATH).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


class SttConfigError(Exception):
    pass


def server_of(cfg):
    """설정의 서버 값. 빈 항목은 기본값으로 채운다."""
    srv = dict(DEFAULT_SERVER)
    for k, v in ((cfg.get("stt") or {}).get("server") or {}).items():
        if v:
            srv[k] = v
    return srv


def load_stt(cfg):
    """음성인식 요청 설정을 만든다. 서버 주소나 API 키가 없으면 SttConfigError."""
    stt = copy.deepcopy(STT_DEFAULT)
    stt["server"] = server_of(cfg)
    stt["api_key"] = (cfg.get("stt") or {}).get("api_key", "")
    if cfg.get("model"):
        stt["models"] = [cfg["model"]]
    if not stt["server"].get("host"):
        raise SttConfigError("음성인식 서버 주소가 없습니다. [설정] → [음성인식 서버 설정]에서 입력하세요.")
    if not stt["api_key"]:
        raise SttConfigError("음성인식 API 키가 없습니다. [설정] → [음성인식 서버 설정]에서 입력하세요.")
    stt["source"] = "WaveText 설정"
    return stt


def mask(key):
    """화면·로그용 마스킹 (NFR-01): 앞 3자 + ********. 8자 이하는 모두 가린다."""
    if not key:
        return ""
    return key[:3] + "********" if len(key) > 8 else "********"


def server_label(stt):
    s = stt["server"]
    return f"{s['scheme']}://{s['host']}:{s['port']}"
