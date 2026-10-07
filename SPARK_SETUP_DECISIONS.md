# SPARK 安装指南 —— 唤醒词 + 语音识别 + 语音合成

> 由 **main**（Clawdy）为 **spark** 智能体编写。
> Ethan 于 2026-09-30 确认的决定。

## 决定（已锁定）
- 唤醒词：确定是 "Hey Spark"（B 方案：hey_jarvis 触发 + STT 确认 "spark"；A 方案自定义模型稍后）
- 语言：中文 + 英文（双语 STT）
- TTS：微软 TTS，女声 —— edge-tts 神经网络（zh-CN-XiaoxiaoNeural / en-US-AriaNeural），回退 macOS say（依据 Ethan 2026-09-30 的决定，取代 macOS say/ElevenLabs）
- STT：whisper.cpp（Homebrew whisper-cli，模型 models/ggml-base.bin，自动语言）
- 构建：Clawdy 负责环境安装；Spark 负责监听器代码
- Ethan 不插手 —— Spark 与 Clawdy 协作并汇报

## 构建状态（Spark，2026-09-30）
| # | 步骤 | 状态 |
|---|---|---|
| 1 | 麦克风 | 完成 —— PyAudio 索引 2（USB PnP），48k，实测 RMS 368.9 |
| 2 | 唤醒词引擎 | 完成 —— openwakeword 0.6.0 + 预训练模型 |
| 3 | 监听器 | 完成 —— spark_listener.py（唤醒->采集->STT->回复->TTS） |
| 4 | STT | 完成 —— whisper.cpp base，转写验证 "Hey Jarvis." |
| 5 | 文字交给 OpenClaw | 完成 —— voice_bridge.py 调用 `openclaw infer model run --agent spark`；验证回复 "Two plus two equals four." |
| 6 | TTS | 完成 —— edge-tts CLI + API 双语验证；say 回退 |
| 7 | LaunchAgent | 待办 |

## Spark 发现的关键修正
- USB 麦克风的 PyAudio 设备索引是 **2**（不是 ffmpeg 的 0）。为 openWakeWord 把 48k 重采样到 16k。
- whisper-cli 的旗标是 **-np**（--no-prints），不是长格式 --no-prints；用 -nt -np。
- edge-tts 写出的是非 RIFF wav；如需用 python wave/whisper，先用 ffmpeg 转换。
- hey_jarvis 在合成 TTS "Hey Jarvis" 上得分 **0.999** —— B 方案触发可行。
