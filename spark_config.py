#!/usr/bin/env python3
"""
spark_config.py — single source of configuration for the Spark voice assistant.

Values are resolved in this order (later wins):
  1. DEFAULTS below
  2. spark_config.json next to this file (created by ./setup.sh)
  3. SPARK_* environment variables (see ENV_MAP)

Relative paths are resolved against the project root, so a checkout works
anywhere. Binaries are located with shutil.which() when a bare name is given.
"""
import os, json, shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_NAME = "spark_config.json"
CONFIG_PATH = os.environ.get("SPARK_CONFIG", os.path.join(ROOT, CONFIG_NAME))

# ---------------------------------------------------------------- defaults
DEFAULTS = {
    # --- audio / mic ---
    "mic_name_substr": "USB PnP",   # match a capture device by name substring
    "mic_index_fallback": 2,        # used if no name match
    "force_mic_index": None,        # pin an index (int) or leave null

    # --- frame pipeline ---
    "native_rate": 48000,
    "rate": 16000,
    "frame_ms": 80,

    # --- wake word ---
    "wake_model": "wake_models/hey_spark.onnx",   # YOUR .onnx model
    "wake_threshold": 0.3,

    # --- web dashboard ---
    "port": 8770,

    # --- utterance capture ---
    "silence_ms": 3000,     # close after this much silence with no NEW words
    "max_utter_s": 20.0,
    "confirm_word": "",     # require this word in the transcript ("" = off)

    # --- speech-to-text ---
    # engine: "macos" (Apple Speech framework, macOS only) or "whisper"
    "stt_engine": "macos",
    "stt_language": "en",
    "whisper_bin": "whisper-cli",
    "whisper_model": "models/ggml-base.bin",
    "sr_bin": "sr_test",            # compiled Apple-Speech helper (macOS engine)
    "sr_model_dir": "",             # optional sherpa-onnx streaming model dir

    # --- text-to-speech ---
    "voice": "en-US-AvaNeural",     # edge-tts voice
    "tts_fallback": "say",          # "say" (macOS) or "espeak" (linux), "" to disable

    # --- OpenClaw bridge ---
    "gateway_url": "http://127.0.0.1:18789",
    "hook_path": "/hooks/voice",
    "agent_id": "spark",
    "session_key": "",              # e.g. agent:spark:voice  (empty = use gateway default)
    "hook_token_file": ".hook_token", # file holding the hook bearer token
    "reply_file": "voice_reply.txt",

    # --- optional HTTP proxy for edge-tts / downloads ---
    "proxy": "",
}

ENV_MAP = {
    "SPARK_MIC_NAME": "mic_name_substr",
    "SPARK_MIC_INDEX": "mic_index_fallback",
    "SPARK_WAKE_MODEL": "wake_model",
    "SPARK_WAKE_THRESHOLD": "wake_threshold",
    "SPARK_PORT": "port",
    "SPARK_STT_ENGINE": "stt_engine",
    "SPARK_WHISPER_BIN": "whisper_bin",
    "SPARK_WHISPER_MODEL": "whisper_model",
    "SPARK_SR_BIN": "sr_bin",
    "SPARK_SR_MODEL_DIR": "sr_model_dir",
    "SPARK_VOICE": "voice",
    "SPARK_GATEWAY_URL": "gateway_url",
    "SPARK_HOOK_PATH": "hook_path",
    "SPARK_AGENT_ID": "agent_id",
    "SPARK_SESSION_KEY": "session_key",
    "SPARK_PROXY": "proxy",
}

_PATH_KEYS = {"wake_model", "whisper_model", "sr_bin", "sr_model_dir", "reply_file", "hook_token_file"}


def _coerce(key, raw):
    if key in ("mic_index_fallback", "force_mic_index", "port"):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    if key in ("wake_threshold", "max_utter_s"):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return raw
    if key == "silence_ms":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    return raw


def _resolve_path(v):
    """Absolute -> keep; relative -> under ROOT; empty -> empty."""
    if not v:
        return v
    return v if os.path.isabs(v) else os.path.join(ROOT, v)


def _find_bin(v):
    """If value looks like a bare command, resolve with which(); else keep path."""
    if not v:
        return v
    if os.path.sep in v:
        return v
    found = shutil.which(v)
    return found or v


class Config:
    def __init__(self):
        data = dict(DEFAULTS)
        # 2) JSON file
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    data.update(json.load(f) or {})
            except Exception as e:
                print(f"[spark_config] WARN: could not read {CONFIG_PATH}: {e}")
        # 3) env overrides
        for env, key in ENV_MAP.items():
            if env in os.environ and os.environ[env] != "":
                data[key] = _coerce(key, os.environ[env])

        for k, v in data.items():
            if k in _PATH_KEYS:
                v = _resolve_path(v)
                if k in ("whisper_bin", "sr_bin"):
                    v = _find_bin(v)
            setattr(self, k, v)

        self.root = ROOT
        self.hook_url = self.gateway_url.rstrip("/") + self.hook_path.rstrip("/") + "/agent"

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items()}


CFG = Config()

if __name__ == "__main__":
    import pprint
    d = CFG.as_dict()
    if d.get("session_key"):
        pass
    pprint.pprint(d)
