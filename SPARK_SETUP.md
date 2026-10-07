# SPARK 安装指南 —— 唤醒词 + 语音识别 + 语音合成

> 由 **main**（Clawdy）为 **spark** 智能体编写。
> 当 Spark 请求安装帮助时，先读这个文件，然后核实实时状态。

## Spark 应该是什么样

**Ethan 的 Mac mini**（macOS 26.3，arm64）上的语音助手，循环如下：

```
USB 麦克风（常开）
  -> 唤醒词检测器听到 "Hey Spark"    （空闲时约 1% CPU）
  -> 录下用户这句话
  -> STT（语音 -> 文字）
  -> 把文字发给 OpenClaw（spark 智能体）  -> 智能体回复
  -> TTS（文字 -> 语音）从扬声器/耳机放出
```

**设计原则：** 监听器应该把文字当作普通消息交给 OpenClaw。
不要另造一个“大脑” —— 智能体本身就是大脑，自带记忆和工具。

## 硬件 / 环境（2026-09-30 核实）

- 主机：Ethan 的 Mac mini，主机名 "vincent的Mac mini"，macOS 26.3（arm64），node v24.18.1
- **麦克风（最适合常听）：`USB PnP Sound Device`**（C-Media，48kHz，USB，单声道）
  - 设备索引因 API 而异（2026-09-30 修正，由 Spark 发现）：
    - ffmpeg/AVFoundation：索引 0    （ffmpeg -f avfoundation -i ":0"）
    - PyAudio：索引 2                 （input_device_index=2）
    - PyAudio 列表：0 = Ethan Headphone（16kHz），2 = USB PnP（48kHz）
    - 优先按名称（子串 USB PnP）匹配，索引 2 作为回退；重新插拔索引会变。
  - 能量门限：持续 RMS > ~25 约 300ms。门槛太松（>1.0）会把蓝牙底噪当语音（RMS 1-10）。实测语音 RMS 60-370。
  - 不要默认用蓝牙耳机麦克风（"Ethan's Headphone" / JBL TUNE670NC）做 24/7 监听 —— 蓝牙麦克风会掉线。
- 输出：`Mac mini扬声器`（内置）或蓝牙耳机。
- `ffmpeg`：/opt/homebrew/bin/ffmpeg  （可用）
- `say`：/usr/bin/say  （macOS 自带 TTS，始终可用）
- `sag`：/opt/homebrew/bin/sag  （ElevenLabs TTS CLI —— 声音更好，需要 API key）
- Python：3.14.3（`python3`）
- 尚未安装：pyaudio、sounddevice、openwakeword、pvporcupine、whisper

## 代理陷阱（血泪教训）

没有 Clash 代理时，Homebrew 和其他网络工具会卡住。
- 系统代理：`http://127.0.0.1:7897`（Clash Verge）；socks5 同端口。
- `~/.zshenv` 现在导出了 http_proxy/https_proxy/all_proxy，让非交互式 shell 也能拿到。
- **如果任何安装卡住，先导出代理：**
  ```bash
  export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897 all_proxy=socks5://127.0.0.1:7897
  ```
- macOS **没有 `timeout` 命令**。用 `perl -e 'alarm shift; exec @ARGV' 30 cmd` 或后台+kill。

## 已安装 / 可用的 STT 与 TTS

- **STT：** OpenMemo 项目用 sherpa-onnx（`openmemo/stt.py`，POST /api/stt）。
  模型：`models/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23/`（中文，约 78MB）。
  对 Spark 来说，中英双语识别器最理想。备选：whisper.cpp、faster-whisper。
- **TTS：** `say`（零配置）或 `sag`（ElevenLabs，更好听）。先用 `say` 跑通循环，再升级。

## 构建顺序（按此顺序做；每步都要验证）

1. **先证明麦克风可用。** 从 USB 设备录 3 秒再放出来。
   （需要 pyaudio 或 ffmpeg。ffmpeg 可录：`ffmpeg -f avfoundation -i ":<idx>" out.wav`）
2. **装唤醒词引擎。** 推荐 **openWakeWord**（免费、无需账号、可训练 "Hey Spark"）。
   - `pip install openwakeword pyaudio`（记得走代理！）
   - Porcupine 是备选，但需要 Picovoice 账号/key —— Ethan 不喜欢搞 key。
3. **唤醒词监听器** —— Python 循环：读麦克风帧 -> 喂给 openWakeWord -> 命中时打印/触发。
4. **采集 + STT** —— 唤醒后：录到静音 -> 转写 -> 得到文字。
5. **把文字交给 OpenClaw** -> 拿到智能体回复。
6. **把回复 TTS 出来**（先用 `say`）。
7. **做成 LaunchAgent**，让它开机自启、重启后存活。

## 规则 / 注意事项

- Ethan **12 岁**，**周一至周五在校寄宿**（那时联系不到）。周末是开发时间。
- 他讨厌**被遗忘 / 被反驳**。把决定写进文件。
- **应用 UI 里不用 emoji**（聊天里可以）。
- **绝不把密码/API key 粘到聊天里。**
- 任何离开本机的事情都要先问。本地安装没问题。
- Git：每个有意义的步骤都 commit+push（他的固定规则）。

## 需要 Spark 和 Ethan 确认的开放问题

- 唤醒词：确定就是 **"Hey Spark"** 吗？
- 用哪个 TTS 声音：macOS `say`（即时）还是 `sag`/ElevenLabs（更好，需要 key）？
- 语言：中文、英文，还是都要？
