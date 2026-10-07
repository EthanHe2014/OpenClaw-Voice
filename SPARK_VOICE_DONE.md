# SPARK 语音助手 —— 完成

构建于 2026-09-30。完全本地。无外部应用。

> **2026-10-07 已迁移** —— 助手现在位于
> `/Users/Ethan/Desktop/Projects/Spark Voice/`（原为 `~/.openclaw/workspace/spark`）。
> 备份在 `./Backups/`；launchd 日志在 `~/Library/Logs/spark-voice/`。
> 智能体的 `./voice_reply.txt` 是指向本目录的符号链接，用于回复交接。
> LaunchAgent 现在执行真正的 Homebrew python（`/opt/homebrew/opt/python@3.14`），
> 并通过 `PYTHONPATH` 指向 `.venv/lib/python3.14/site-packages`，因为 macOS 会阻止
> launchd 执行放在桌面上的 venv 符号链接。

## 循环
USB 麦克风（索引 2 "USB PnP"）-> openWakeWord "hey_jarvis" -> 录到静音
-> whisper.cpp STT -> POST 本地 hook -> 真正的 Spark 智能体（持久会话）
-> 从会话轨迹读取回复 -> edge-tts 朗读。

## 文件
- spark_web.py      : 网页仪表盘 + 完整流水线（主程序）。
                      提供 http://127.0.0.1:8770（实时视图）并运行循环。
- spark_listener.py : 无界面监听器（无网页 UI）—— 备用入口。
- voice_bridge.py   : 文件队列大脑（旧的单次路径；已被 hook 取代）。
- ask_mac.sh        : macOS 原生带选项弹窗（dialog/list/notify）。
- .hook_token       : hook 的 bearer 令牌（权限 600，绝不回显）。
- models/ggml-base.bin : whisper STT 模型。
- ai.openclaw.spark.web.plist  : 运行 spark_web.py 的 LaunchAgent（已安装）。
- ai.openclaw.spark.voice.plist: 无界面监听器的 LaunchAgent（待用；当网页版占用麦克风时不用）。

## HOOK 配置（已应用到 ~/.openclaw/openclaw.json）
hooks.enabled = true
hooks.path    = "/hooks/voice"
hooks.token   = <密钥>              # 存在配置里（已隐去）
hooks.allowedAgentIds = ["spark"]   # 仅限 Spark
hooks.allowRequestSessionKey = true
hooks.allowedSessionKeyPrefixes = ["agent:spark:"]  # 只有语音会话可达
备份：~/.openclaw/openclaw.json.pre-hooks.bak

使用的端点：POST http://127.0.0.1:18789/hooks/voice/agent
  body：{message, agentId:"spark", sessionKey:"agent:spark:voice",
         sessionMode:"persistent", waitForCompletion:true}
  -> {ok:true, runId, completion:{status:"ok"}}

## 读取回复
hook 响应**不包含**回复文本。要从会话里读：
  openclaw sessions export-trajectory --agent spark --session-key agent:spark:voice --json
  -> 解析 <outputDir>/session-branch.json
  -> entries[]，其中 type=="message" 且 entry.message.role=="assistant"
  -> entry.message.content[] .text   （空的 content 数组跳过）
注意：message 对象**嵌套**在 entry.message 下（不是 entry.role/entry.content）。

## 安全说明
- 语音输入在到达智能体之前，会被网关包装成 EXTERNAL/UNTRUSTED 内容。这是正确的：
  麦克风无法证明说话人是谁。语音无法冒充主人或发出危险命令。
- hook 令牌只授予入口权限；把调用方当作不可信。
- 直接 sqlite 读取状态目录是被有意阻止的 —— 请用 openclaw 命令。

## 学到的关键坑
- whisper-cli 旗标是 -np（不是 --no-prints）。模型必须是 ggml-*.bin。
- USB 麦克风的 PyAudio 设备索引是 2（ffmpeg 的编号不同）。
- 后台进程**无法**在 exec shell 结束后存活 —— 用 LaunchAgent。
- 从 exec 调用 openclaw agent --session-key 会被**阻止**（会话间归属问题）。
  受支持的路径是 hooks 入口，不是 CLI 注入。
- 网关在 web 路径上提供 Control UI HTML；没有原始的 /sessions JSON API。
- Hook 映射路由：只有内置的 /hooks/<path>/agent 有效；自定义 mapping 的
  action:"agent" 会坍缩到它。请用内置的 /agent 端点。

## 已验证
- STT：从样本转写出 "Hey Jarvis."。
- Hook：HTTP 200，{ok:true}。
- 真实智能体回复：返回 "WIRED"、"PONG"、"FINAL_OK"。
- ask_spark() 端到端：返回精确的回复文本。

## 待办 / 可选
- 首次真实采集会触发 macOS 麦克风权限弹窗（在屏幕上批准）。
- 稍后决定：保留网页仪表盘作为常驻运行者，还是换成无界面监听器以降低开销
  （同一时间只能有一个占用麦克风）。
