"""OpenMemo 配置管理 —— 双层配置：运行时设置（settings.json）覆盖 .env。

层级（高 → 低）：
1. data/settings.json（App/CLI 可改，持久化，热生效）
2. .env 环境变量（部署默认值）
3. 代码内置默认（仅 SERVER_HOST/PORT/TTS_VOICE 有）

这样用户不需要进 .env 就能改模型、地址、语音等。
"""
import json
import os
from pathlib import Path

# Load .env if python-dotenv is available (optional — not required)
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Project root = the Spark Voice dir (this package lives at <root>/sparktasks)
BASE_DIR = Path(__file__).parent.parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

# Data Configuration — Spark-owned, overridable via SPARKTASKS_DATA_DIR
DATA_DIR = Path(os.environ.get("SPARKTASKS_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "openmemo.db"
AUDIO_DIR = DATA_DIR / "audio"
SETTINGS_PATH = DATA_DIR / "settings.json"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

# ── 兼容层：旧代码直接 import 的模块级常量（值取运行时配置）────────
# 注意：这些是导入时的快照；需要热更新的代码请用下方函数。
AI_BASE_URL = os.getenv("AI_BASE_URL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY", "")
SEARCH_BASE_URL = os.getenv("SEARCH_BASE_URL", "")
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "18890"))
TTS_VOICE = os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural")

# ── 可配置项清单（App/CLI 可改）──────────────────────────────
# key: (env 变量名, 默认值, 说明, 是否敏感)
CONFIG_KEYS = {
    "ai_model":       ("AI_MODEL", "", "AI 模型 id（如 deepseek-v4-flash / kimi-k2.5）", False),
    "ai_base_url":    ("AI_BASE_URL", "", "AI 接口地址（OpenAI 兼容）", False),
    "ai_api_key":     ("AI_API_KEY", "", "AI API 密钥", True),
    "search_provider": ("SEARCH_PROVIDER", "", "搜索提供商（tavily/brave/serper/custom）", False),
    "search_api_key": ("SEARCH_API_KEY", "", "搜索 API 密钥", True),
    "search_base_url": ("SEARCH_BASE_URL", "", "搜索接口地址", False),
    "tts_voice":      ("TTS_VOICE", "zh-CN-XiaoxiaoNeural", "语音角色（Edge TTS）", False),
    "fallback_ai_base_url": ("FALLBACK_AI_BASE_URL", "http://127.0.0.1:18080/v1", "AI 主服务失败时的降级地址（本地模型）", False),
    "fallback_ai_model":   ("FALLBACK_AI_MODEL", "claude-3-opus", "降级模型名（本地 llama-server 的模型别名）", False),
}

# 只读配置（不支持运行时改，展示用）
READONLY_KEYS = {
    "server_host": ("SERVER_HOST", "0.0.0.0", "监听地址", False),
    "server_port": ("SERVER_PORT", "18890", "监听端口", False),
}


def _load_settings() -> dict:
    """读取 settings.json（不存在 → 空 dict）。"""
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[config] settings.json 读取失败，按空处理：{e}")
    return {}


def _save_settings(data: dict):
    """原子写 settings.json。"""
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(SETTINGS_PATH)


def set_setting(key: str, value) -> bool:
    """写入运行时配置并持久化。返回是否成功。"""
    if key not in CONFIG_KEYS:
        return False
    settings = _load_settings()
    if value is None or str(value).strip() == "":
        settings.pop(key, None)      # 空值 = 回退到 .env
    else:
        settings[key] = str(value).strip()
    _save_settings(settings)
    return True


def get_setting(key: str):
    """读取运行时配置（settings.json 覆盖 .env）。"""
    if key not in CONFIG_KEYS:
        return None
    settings = _load_settings()
    if key in settings and settings[key] not in (None, ""):
        return settings[key]
    env_name, default, _, _ = CONFIG_KEYS[key]
    return os.environ.get(env_name, default)


def get_all_settings() -> dict:
    """返回全部配置（App 设置页用）。敏感字段用掩码。"""
    result = {}
    for key, (env_name, default, desc, sensitive) in CONFIG_KEYS.items():
        val = get_setting(key)
        if sensitive and val:
            val = mask_secret(val)
        result[key] = {"value": val, "description": desc, "sensitive": sensitive, "env": env_name}
    for key, (env_name, default, desc, sensitive) in READONLY_KEYS.items():
        result[key] = {"value": os.getenv(env_name, default), "description": desc, "sensitive": False, "env": env_name, "readonly": True}
    return result


def mask_secret(secret: str) -> str:
    """密钥脱敏：只留前 4 后 4。"""
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}…{secret[-4:]}"


# ── 便捷读取（供各模块动态调用，避免 import 时快照）────────────
def ai_model():
    return get_setting("ai_model") or ""


def ai_base_url():
    return get_setting("ai_base_url") or ""


def ai_api_key():
    return get_setting("ai_api_key") or ""


def tts_voice():
    return get_setting("tts_voice") or "zh-CN-XiaoxiaoNeural"


def search_provider():
    return get_setting("search_provider") or ""


def search_api_key():
    return get_setting("search_api_key") or ""


def search_base_url():
    return get_setting("search_base_url") or ""
