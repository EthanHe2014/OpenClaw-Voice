# ⚡ Spark — OpenClaw 的本地语音助手

用你的声音和 OpenClaw 智能体对话。Spark 在本地跑一条流水线：

```
USB 麦克风 ─► openWakeWord 唤醒词 ─► 持续录音直到静音 ─► 语音转文字
           ─► OpenClaw hook ─► 智能体回复 ─► 文字转语音
```

实时仪表盘会显示麦克风电平、唤醒分数、识别文本和回复，地址是
`http://127.0.0.1:8770`。

除了语音输入输出（edge-tts）和你的 OpenClaw 网关内部的模型调用之外，
一切都在本地完成。

---

## 你需要自己准备的东西

Spark **不附带**任何模型、令牌或个人配置。你需要提供：

| 东西 | 用途 |
|---|---|
| **一个 openWakeWord `.onnx` 模型** | 唤醒词（例如 `hey_spark.onnx`） |
| **一个 hook 令牌** | `setup.sh` 可以帮你生成 |
| **一段 `openclaw.json` 的 hooks 配置** | `setup.sh` 会打印出来，你粘进去 |
| 一个语音转文字后端 | macOS 自带（免费）或 whisper.cpp 的 `ggml` 模型 |
| （可选）一个麦克风 | 任意 PyAudio 输入设备 |

---

## 快速开始

```bash
git clone <本仓库> spark-voice
cd spark-voice
./setup.sh
```

`setup.sh` 会交互式地：
1. 检查前置依赖（Python 3.10+、ffmpeg）
2. 创建 `.venv/` 并安装 `requirements.txt`
3. 列出你的麦克风并记录你的选择
4. 询问你的 **`.onnx` 唤醒模型**
5. 选择**语音转文字**引擎
6. 询问你的**网关地址 / 智能体 id / 会话键**
7. **生成一个 hook 令牌**并写入 `spark_config.json`
8. 打印要加到 `openclaw.json` 的 **`hooks` 配置块**
9. 可选地安装一个 macOS LaunchAgent

然后启动它：

```bash
./.venv/bin/python spark_web.py      # 仪表盘 + 流水线
```

---

## 提供你的唤醒模型

用 [openWakeWord](https://github.com/dscripka/openWakeWord) 训练一个，或者直接用
内置模型名。把 `spark_config.json` 里的 `wake_model` 指向该文件：

```json
"wake_model": "wake_models/my_word.onnx",
"wake_threshold": 0.3
```

## 提供你的 hook 令牌

**不要把令牌提交到仓库。** `setup.sh` 会写入 `.hook_token`（权限 600），
`.gitignore` 已将其排除。如果你想手动创建：

```bash
openssl rand -hex 24 > .hook_token && chmod 600 .hook_token
```

## 修改你的 `openclaw.json`

在 `"hooks"` 键下合并一段像这样的配置：

```json
{
  "enabled": true,
  "path": "/hooks/voice",
  "token": "<在此粘贴 .hook_token 的内容>",
  "allowedAgentIds": ["spark"],
  "allowRequestSessionKey": true,
  "defaultSessionKey": "agent:spark:voice",
  "mappings": [
    { "id": "voice-spark", "match": { "path": "/hooks/voice" },
      "action": "agent", "agentId": "spark",
      "sessionKey": "agent:spark:voice", "sessionMode": "persistent",
      "deliver": true }
  ],
  "allowedSessionKeyPrefixes": ["agent:spark:"]
}
```

先备份 `openclaw.json`，改完之后重启网关。

> **回复是从文件里读取的。** 语音智能体被要求把回复写到 `./voice_reply.txt`。
> 请确保你的智能体对该项目目录（也就是这个文件夹里的 `voice_reply.txt`）有写权限。

---

## 配置

所有设置都在 `spark_config.json` 里（由 `setup.sh` 创建）—— 每个键及其默认值见
`spark_config.example.json`。你也可以用 `SPARK_*` 环境变量覆盖任何一项
（例如 `SPARK_PORT`、`SPARK_GATEWAY_URL`）。完整的映射见 `spark_config.py`。
**没有任何东西被硬编码到某个路径。**

运行下面这条命令可以打印解析后的配置：

```bash
./.venv/bin/python spark_config.py
```

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `spark_web.py` | 主程序：仪表盘 + 完整流水线 |
| `spark_listener.py` | 无界面版本（无仪表盘） |
| `voice_bridge.py` | 文件队列大脑（旧的单次路径） |
| `wake_dash.py` | 唤醒模型的训练监控界面 |
| `ask_mac.sh` | macOS 原生带选项的弹窗提示 |
| `setup.sh` | 交互式安装脚本 |
| `spark_config.py` | 配置加载器 |
| `Training/` | 唤醒模型训练启动脚本 |
| `Tools/` | 开发/实验脚本 |
| `spark_cues/` | 短提示音（唤醒/思考/完成/错误） |
| `models/`、`wake_models/` | 你自己提供（已被 gitignore） |

---

## 注意事项与坑

- **macOS 的桌面/文稿目录受保护。** `launchd` **无法执行**放在 `~/Desktop` 上的
  可执行文件（但可以读写）。请把项目放在别处，或者让 LaunchAgent 指向一个
  *受保护目录之外*的 Python 解释器。
- **首次运行**会触发麦克风权限弹窗 —— 请在屏幕上批准。
- **语音是不可信输入。** 麦克风无法证明说话人是谁；网关会把它包装成外部内容。
  不要给语音赋予原始的危险操作权限。
- **同一时间只能有一个进程**占用麦克风 —— 要么跑仪表盘，要么跑无界面监听器，
  不能两个同时跑。

---

## 许可证

MIT（或你自选）。
