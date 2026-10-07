#!/bin/bash
# =============================================================================
#  setup.sh —— Spark 语音助手交互式安装脚本
# =============================================================================
#  带你走完新用户必须提供的每一项：
#    * Python 环境 + 依赖
#    * 你自己的 openWakeWord .onnx 唤醒模型
#    * 语音转文字后端（whisper.cpp 模型，或 macOS 自带）
#    * 你的 OpenClaw 网关信息 + 自动生成的 hook 令牌
#    * 需要粘进 openclaw.json 的对应 hooks 配置块
#    * （可选）Apple 语音助手 sr_test 和 macOS LaunchAgent
#
#  可重复运行：绝不覆盖已存在的令牌，覆盖 spark_config.json 前会先问。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
CFG="$ROOT/spark_config.json"
TOKEN_FILE="$ROOT/.hook_token"
PY="${PYTHON:-python3}"

# ---------------------------------------------------------------- 辅助函数
c()   { printf '\033[%sm%s\033[0m' "$1" "$2"; }
hdr() { echo; c '1;36' "── $* ──────────────────────────────────────────"; echo; }
ok()  { c '1;32' "  ✓ $*"; echo; }
warn(){ c '1;33' "  ! $*"; echo; }
die() { c '1;31' "  ✗ $*"; echo; exit 1; }
ask() { # ask 变量 "提示" "默认值"
  local __v="$1" __p="$2" __d="${3:-}" __in
  if [ -n "$__d" ]; then printf '  %s [%s]: ' "$__p" "$__d"; else printf '  %s: ' "$__p"; fi
  read -r __in || true
  [ -z "$__in" ] && __in="$__d"
  printf -v "$__v" '%s' "$__in"
}
yesno() { # yesno "提示" [默认 y|n]
  local __p="$1" __d="${2:-n}" __in
  printf '  %s [%s]: ' "$__p" "$__d"; read -r __in || true
  [ -z "$__in" ] && __in="$__d"
  [[ "$__in" =~ ^[Yy] ]]
}

echo
c '1;36' "⚡ Spark 语音助手 —— 安装"
echo
echo "  本脚本会带你为这台机器配置 Spark。"
echo "  请准备好：(1) 你的 openWakeWord .onnx 模型，(2) 你的 OpenClaw 网关地址。"
echo

# ---------------------------------------------------------------- 0) 前置依赖
hdr "0. 检查前置依赖"
MISSING=0
for bin in "$PY" ffmpeg; do
  if command -v "$bin" >/dev/null 2>&1; then ok "$bin 已找到：$(command -v "$bin")"; else warn "$bin 未找到"; MISSING=1; fi
done
command -v whisper-cli >/dev/null 2>&1 && ok "whisper-cli 已找到" || warn "whisper-cli 未找到（仅当 stt_engine=whisper 时需要）"
command -v openclaw   >/dev/null 2>&1 && ok "openclaw 已找到"   || warn "openclaw CLI 不在 PATH 上（回复桥接路径需要它）"
[ "$MISSING" = 1 ] && warn "运行 Spark 前请先安装缺失的核心工具（brew install ffmpeg python）。"
"$PY" - <<'PYCHK' || die "需要 Python 3.10+。"
import sys
sys.exit(0 if sys.version_info >= (3,10) else 1)
PYCHK
ok "Python 版本正常（$($PY --version 2>&1)）"

# ---------------------------------------------------------------- 1) venv
hdr "1. Python 环境"
if [ -d "$ROOT/.venv" ]; then
  ok "已保留现有的 .venv"
else
  echo "  正在创建 .venv ..."
  "$PY" -m venv "$ROOT/.venv"
  ok ".venv 已创建"
fi
VPY="$ROOT/.venv/bin/python"
echo "  正在安装依赖（可能要一分钟）..."
"$VPY" -m pip install --upgrade pip -q
"$VPY" -m pip install -q -r "$ROOT/requirements.txt"
ok "依赖安装完成"

# ---------------------------------------------------------------- 2) 麦克风
hdr "2. 麦克风"
echo "  可用的输入设备："
"$VPY" - <<'PYMIC' || true
try:
    import pyaudio
    pa = pyaudio.PyAudio()
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d.get("maxInputChannels", 0) > 0:
            print(f"    [{i}] {d.get('name')}")
    pa.terminate()
except Exception as e:
    print("    （无法枚举设备：", e, "）")
PYMIC
echo
echo "  输入你麦克风名称的一个子串（例如 'USB PnP'、'MacBook'）。"
echo "  留空表示接受任意输入设备。"
ask MIC_NAME "麦克风名称子串" "USB PnP"

# ---------------------------------------------------------------- 3) 唤醒模型
hdr "3. 唤醒词模型（.onnx）"
echo "  提供你训练好的 openWakeWord 模型（例如 hey_spark.onnx）。"
echo "  相对路径会基于本工程目录解析。"
while :; do
  ask WAKE "你的 .onnx 模型路径" "wake_models/hey_spark.onnx"
  [ -f "$ROOT/$WAKE" ] || [ -f "$WAKE" ] && break
  warn "找不到文件：$WAKE  （放好后重试，或换一个路径）"
done
ask WAKE_THRESH "唤醒阈值（0-1，越低越灵敏）" "0.3"

# ---------------------------------------------------------------- 4) 语音识别
hdr "4. 语音转文字"
echo "  引擎："
echo "    macos   = Apple 语音框架（仅 macOS，无需额外模型，此处最准）"
echo "    whisper = whisper.cpp（跨平台，需要 ggml 模型）"
ask STT_ENGINE "STT 引擎（macos/whisper）" "macos"
WHISPER_BIN_V="whisper-cli"; WHISPER_MODEL_V=""
if [ "$STT_ENGINE" = "whisper" ]; then
  ask WHISPER_BIN_V "whisper-cli 可执行文件" "$(command -v whisper-cli || echo whisper-cli)"
  while :; do
    ask WHISPER_MODEL_V "ggml 模型路径（例如 models/ggml-base.bin）" "models/ggml-base.bin"
    [ -f "$ROOT/$WHISPER_MODEL_V" ] || [ -f "$WHISPER_MODEL_V" ] && break
    warn "找不到文件：$WHISPER_MODEL_V"
  done
else
  echo "  （macOS 引擎需要编译好的 sr_test 助手 —— 第 7 步可以构建。）"
fi

# ---------------------------------------------------------------- 5) OpenClaw
hdr "5. OpenClaw 网关"
ask GW_URL   "网关基础地址" "http://127.0.0.1:18789"
ask HOOK_PATH "hook 路径" "/hooks/voice"
ask AGENT_ID "允许的智能体 id" "spark"
echo "  会话键：语音回复所在的持久会话。"
echo "  留空则让网关使用它配置好的默认值。"
ask SESSION_KEY "会话键（可选）" ""

# ---------------------------------------------------------------- 6) 令牌
hdr "6. Hook 令牌"
if [ -f "$TOKEN_FILE" ]; then
  ok "已保留现有的 .hook_token（未覆盖）"
else
  if command -v openssl >/dev/null 2>&1; then
    printf '%s' "$(openssl rand -hex 24)" > "$TOKEN_FILE"
  else
    printf '%s' "$("$VPY" -c 'import secrets;print(secrets.token_hex(24))')" > "$TOKEN_FILE"
  fi
  chmod 600 "$TOKEN_FILE"
  ok "已生成 .hook_token（权限 600）"
fi

# ---------------------------------------------------------------- 写配置
hdr "7. 写入 spark_config.json"
"$VPY" - "$CFG" "$MIC_NAME" "$WAKE" "$WAKE_THRESH" "$STT_ENGINE" \
        "$WHISPER_BIN_V" "$WHISPER_MODEL_V" "$GW_URL" "$HOOK_PATH" \
        "$AGENT_ID" "$SESSION_KEY" <<'PYCFG'
import json, sys
(out, mic, wake, thr, engine, wbin, wmodel, gw, hook, agent, sess) = sys.argv[1:12]
cfg = {
  "mic_name_substr": mic or "USB PnP",
  "wake_model": wake,
  "wake_threshold": float(thr),
  "stt_engine": engine,
  "whisper_bin": wbin,
  "whisper_model": wmodel or "models/ggml-base.bin",
  "gateway_url": gw,
  "hook_path": hook,
  "agent_id": agent,
  "session_key": sess,
}
json.dump(cfg, open(out, "w"), indent=2, ensure_ascii=False)
print("  已写入", out)
PYCFG
ok "配置已写入"

# ---------------------------------------------------------------- 构建 sr 助手
if [ "$STT_ENGINE" = "macos" ] && [ ! -x "$ROOT/sr_test" ]; then
  hdr "8. 构建 Apple 语音助手（sr_test）"
  if command -v swiftc >/dev/null 2>&1 && [ -f "$ROOT/sr_test.swift" ]; then
    if swiftc -O "$ROOT/sr_test.swift" -o "$ROOT/sr_test" 2>/dev/null; then
      ok "已构建 sr_test"
    else
      warn "swiftc 失败 —— 如果配置了 whisper，Spark 会回退到 whisper"
    fi
  else
    warn "缺少 swiftc 或 sr_test.swift —— 跳过（装 Xcode 命令行工具后可用 macOS 引擎）"
  fi
fi

# ---------------------------------------------------------------- hooks 片段
hdr "9. 把下面这段加进 ~/.openclaw/openclaw.json 的 \"hooks\""
cat <<JSON
{
  "enabled": true,
  "path": "$HOOK_PATH",
  "token": "<在此粘贴 .hook_token 的内容>",
  "allowedAgentIds": ["$AGENT_ID"],
  "allowRequestSessionKey": true,
  ${SESSION_KEY:+"defaultSessionKey": "$SESSION_KEY",}
  "mappings": [
    {
      "id": "voice-$AGENT_ID",
      "match": { "path": "$HOOK_PATH" },
      "action": "agent",
      "name": "Voice to $AGENT_ID",
      "agentId": "$AGENT_ID",
      ${SESSION_KEY:+"sessionKey": "$SESSION_KEY",}
      "sessionMode": "persistent",
      "deliver": true
    }
  ],
  "allowedSessionKeyPrefixes": ["agent:${AGENT_ID}:"]
}
JSON
echo
warn "请保管好令牌 —— 它授予进入你智能体的入口权限。"
echo "  提示：语音智能体会把回复写到 ./voice_reply.txt。"

# ---------------------------------------------------------------- 可选启动项
if yesno "是否安装 macOS LaunchAgent 以自动启动仪表盘 + 监听器？" "n"; then
  hdr "10. LaunchAgent"
  VPY_ABS="$ROOT/.venv/bin/python"
  LA="$HOME/Library/LaunchAgents"
  mkdir -p "$LA"
  LOGDIR="$HOME/Library/Logs/spark-voice"; mkdir -p "$LOGDIR"
  cat > "$LA/ai.openclaw.spark.web.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.openclaw.spark.web</string>
  <key>ProgramArguments</key><array>
    <string>$VPY_ABS</string><string>-u</string><string>$ROOT/spark_web.py</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOGDIR/web.out</string>
  <key>StandardErrorPath</key><string>$LOGDIR/web.err</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/ai.openclaw.spark.web" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LA/ai.openclaw.spark.web.plist" 2>/dev/null \
    || launchctl load -w "$LA/ai.openclaw.spark.web.plist" 2>/dev/null || true
  ok "LaunchAgent 已安装并启动（日志在 $LOGDIR）"
  warn "macOS：如果程序放在 ~/Desktop 上，launchd 会被阻止执行它 ——"
  echo "     请把工程放在桌面/文稿/下载之外，或使用非桌面的解释器。"
else
  hdr "10. 运行"
  echo "  手动启动仪表盘："
  echo "      $ROOT/.venv/bin/python $ROOT/spark_web.py"
  echo "  然后打开   http://127.0.0.1:<端口>   （默认 8770）。"
fi

echo
ok "安装完成。⚡"
echo
