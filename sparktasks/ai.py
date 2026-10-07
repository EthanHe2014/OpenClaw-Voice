"""AI 接入模块 —— 通过可配置的 OpenAI 兼容接口调用大模型"""
import asyncio
import json
import re
import httpx
from .config import ai_base_url, ai_api_key, ai_model

from .prompts import SYSTEM_PROMPT




async def call_ai(messages: list, system_prompt: str = None, retries: int = 1,
                    temperature: float = None, max_tokens: int = None,
                    json_mode: bool = True) -> dict:
    """Call the AI API and return the response.

    Uses STREAMING because some OpenAI-compatible endpoints only respond
    to stream=true requests (non-streaming may hang/timeout).

    Args:
        messages: Conversation messages
        system_prompt: System prompt (default: main SYSTEM_PROMPT)
        retries: Number of retries on failure
        temperature: Sampling temperature (default 0.3 for precision, use 0.8+ for creativity)
        max_tokens: Max response tokens (default 1500 — JSON 可能较长，勿设过小以免截断丢字段)
        json_mode: 强制模型输出 JSON（response_format=json_object）。
           对话/任务解析用 True；纯文本生成（提醒文案/新闻等）用 False。
           若接口不支持该参数（返回 400），会自动去掉重试，不影响其它提供商。
    """
    # 运行时配置（settings.json 覆盖 .env，改模型/地址/密钥即时生效，无需重启）
    key = ai_api_key()
    base_url = ai_base_url()
    model = ai_model()
    if not key:
        return {"content": None, "error": "AI API key not configured"}
    if not base_url:
        return {"content": None, "error": "AI base URL not configured"}
    if not model:
        return {"content": None, "error": "AI model not configured"}

    if system_prompt is None:
        system_prompt = SYSTEM_PROMPT
    
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    
    url = f"{base_url.rstrip('/')}/chat/completions"
    
    use_json_mode = json_mode
    # json 模式走非流式：部分接口（如 DeepSeek）stream + json_object 会返回空流；
    # 纯文本生成保持流式（原行为，兼容只支持流式的接口）。
    use_stream = not json_mode
    last_error = None
    # 尝试预算：常规重试 + json 空白响应重试 + 换流式重试（DeepSeek json 模式偶发空白）
    max_attempts = retries + 6
    for attempt in range(max_attempts):
        payload = {
            "model": model,
            "messages": full_messages,
            "temperature": temperature if temperature is not None else 0.3,
            "max_tokens": max_tokens or 1500,  # 足够大，避免 JSON 被截断导致任务/提醒丢失
            "stream": use_stream
        }
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0 if not use_stream else 10.0) as client:
                if use_stream:
                    content_parts = []
                    async with client.stream("POST", url, json=payload, headers=headers) as response:
                        if response.status_code == 400 and use_json_mode:
                            # 接口不支持 response_format → 去掉它并切回流式重试（兼容 Ollama 等）
                            body = (await response.aread()).decode(errors="ignore")
                            if "response_format" in body:
                                print("[ai] 接口不支持 json_object，自动降级为普通流式模式")
                                use_json_mode = False
                                use_stream = True
                                continue
                            last_error = f"API error 400: {body[:200]}"
                            if attempt < retries:
                                await asyncio.sleep(1)
                                continue
                            return {"content": None, "error": last_error}
                        if response.status_code != 200:
                            body = (await response.aread()).decode(errors="ignore")
                            last_error = f"API error {response.status_code}: {body[:200]}"
                            if attempt < retries:
                                await asyncio.sleep(1)
                                continue
                            return {"content": None, "error": last_error}
                        
                        # Accumulate streaming deltas
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                piece = delta.get("content")
                                if piece:
                                    content_parts.append(piece)
                    content = "".join(content_parts).strip()
                else:
                    response = await client.post(url, json=payload, headers=headers)
                    if response.status_code == 400 and use_json_mode:
                        body = response.text
                        if "response_format" in body:
                            print("[ai] 接口不支持 json_object，自动降级为普通流式模式")
                            use_json_mode = False
                            use_stream = True
                            continue
                        last_error = f"API error 400: {body[:200]}"
                        if attempt < retries:
                            await asyncio.sleep(1)
                            continue
                        return {"content": None, "error": last_error}
                    if response.status_code != 200:
                        last_error = f"API error {response.status_code}: {response.text[:200]}"
                        if attempt < retries:
                            await asyncio.sleep(1)
                            continue
                        return {"content": None, "error": last_error}
                    data = response.json()
                    choices = data.get("choices") or []
                    content = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
                
                if content:
                    return {"content": content, "error": None}
                
                # 空内容（DeepSeek json_object 偶发返回纯空白）→ 同模式重试；
                # 非流式重试耗尽后再换流式兜底一次；流式也空 → 降级本地模型。
                last_error = "Empty response"
                if not use_stream:
                    if attempt < max_attempts - 3:
                        await asyncio.sleep(1)
                        continue
                    use_stream = True
                    continue
                return await _try_fallback(full_messages, temperature, max_tokens)
        except httpx.HTTPStatusError as e:
            last_error = f"API error {e.response.status_code}: {e.response.text[:200]}"
        except httpx.ConnectError as e:
            last_error = f"Connection failed: {str(e)[:100]}"
        except Exception as e:
            last_error = f"Request failed: {str(e)[:100]}"
        
        if attempt < retries:
            await asyncio.sleep(1)
    
    # 主服务连接失败/异常 → 降级本地模型（保证用户永远有回复）
    if last_error:
        print(f"[ai] 主服务失败原因: {last_error}")
    return await _try_fallback(full_messages, temperature, max_tokens)


async def _try_fallback(messages: list, temperature: float = None, max_tokens: int = None) -> dict:
    """主 AI 服务连续失败时，降级到本地模型（llama-server），避免「AI 服务不可用」。
    本地模型是自托管推理，不依赖外网/API 配额。"""
    from .config import get_setting
    fb_url = (get_setting("fallback_ai_base_url") or "").strip()
    fb_model = (get_setting("fallback_ai_model") or "").strip()
    if not fb_url or not fb_model:
        return {"content": None, "error": "Empty response"}
    print(f"[ai] 主服务失败，降级到本地模型 {fb_model}")
    try:
        async with httpx.AsyncClient(timeout=240.0) as client:
            r = await client.post(
                f"{fb_url.rstrip('/')}/chat/completions",
                json={
                    "model": fb_model,
                    "messages": messages,
                    "temperature": temperature if temperature is not None else 0.3,
                    "max_tokens": max_tokens or 2000,
                    "stream": False,
                },
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                choices = r.json().get("choices") or []
                content = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
                if content:
                    print(f"[ai] 本地降级成功（{len(content)} 字符）")
                    return {"content": content, "error": None}
                return {"content": None, "error": "Fallback empty response"}
            return {"content": None, "error": f"Fallback failed: HTTP {r.status_code}"}
    except Exception as e:
        return {"content": None, "error": f"Fallback failed: {str(e)[:120]}"}


def _extract_json(content: str) -> dict | None:
    """Robustly extract JSON from AI response, handling various formats.

    Also attempts to repair JSON truncated by max_tokens: if the closing
    brace is missing, we try progressively appending closing braces/quotes
    so task/appointment fields (placed first) survive.
    """
    content = content.strip()

    try:
        obj = json.loads(content)
        # 只接受 dict（AI 契约是对象）；list/其它类型不是合法回复
        return obj if isinstance(obj, dict) and obj else None
    except json.JSONDecodeError:
        pass

    json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
    if json_match:
        candidate = json_match.group(1).strip()
        parsed = _try_parse_or_repair(candidate)
        if parsed:
            return parsed

    brace_start = content.find('{')
    if brace_start != -1:
        # 用括号配对找到第一个完整 JSON 对象（AI 偶尔在 JSON 后追加散文/思考）
        obj_text = _match_balanced(content, brace_start)
        if obj_text:
            parsed = _try_parse_or_repair(obj_text)
            if parsed:
                return parsed
        # 没有完整闭合 → 可能被截断；从 '{' 到结尾尝试修复
        parsed = _try_parse_or_repair(content[brace_start:])
        if parsed:
            return parsed

    return None


def _match_balanced(text: str, start: int) -> str | None:
    """从 start 的 '{' 开始，用深度计数找到配对的 '}'，返回完整 JSON 子串。
    支持字符串内的括号（跳过 "..." 和转义）。找不到闭合时返回 None。"""
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _try_parse_or_repair(text: str) -> dict | None:
    """Try json.loads, then attempt several truncation repairs."""
    for candidate in _repair_candidates(text):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and obj:
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _repair_candidates(text: str) -> list:
    """Yield increasingly aggressive repairs for a truncated JSON object."""
    yield text
    # 1) Missing closing brace(s)
    for n in range(1, 6):
        yield text + '}' * n
    # 2) Unclosed string at the very end -> close it then brace(s)
    for n in range(1, 6):
        yield text + '"' + '}' * n
    # 3) Trailing comma before closing
    stripped = text.rstrip()
    if stripped.endswith(','):
        for candidate in _repair_candidates(stripped[:-1]):
            yield candidate
    # 4) 任意位置的多余尾逗号：把 ",}" 修成 "}"（含嵌套，如 {"a":{"b":1,},}）
    fixed = re.sub(r",\s*(?=[}\]])", "", text)
    if fixed != text:
        yield fixed
    return


async def analyze_intent(user_message: str, conversation_context: list = None,
                        speaker: str = None, emotion: str = None, event: str = None) -> dict:
    """Analyze user message for intent and extract slots.

    Injects current time, speaker identity + privacy rules so the AI
    knows who it is talking to and protects other people's secrets.
    (speaker 为空 = 访客/未识别)
    """
    from datetime import datetime
    from .tasks import TaskManager
    from .prompts import PRIVACY_RULES
    
    # Build context-aware system prompt
    now = datetime.now()
    hour = now.hour
    if 5 <= hour < 9:
        time_desc = "早上"
    elif 9 <= hour < 12:
        time_desc = "上午"
    elif 12 <= hour < 14:
        time_desc = "中午"
    elif 14 <= hour < 18:
        time_desc = "下午"
    elif 18 <= hour < 21:
        time_desc = "晚上"
    else:
        time_desc = "深夜"
    
    time_context = f"\n\n## 当前时间\n现在是 {now.strftime('%Y年%m月%d日 %H:%M')}（{time_desc}，星期{['一','二','三','四','五','六','日'][now.weekday()]}）。解析时间时请参考当前时间。"
    
    # 说话人身份 + 隐私：已识别才给任务上下文；访客不给任何私密信息
    if emotion or event:
        print(f"[ai] 情绪感知：emotion={emotion} event={event}（来自语音 SenseVoice）")
    if speaker:
        identity_context = f"\n\n## 当前说话人\n{speaker}（已通过语音识别确认）"
        # 情绪感知：SenseVoice 本地识别的语音情绪 → AI 调整语气
        if emotion:
            if event:
                identity_context += f"\n声音事件：{event}（真实检测，如笑声/哭声——文本里看不到的东西）"
            identity_context += (
                f"\n当前说话人的语音情绪：{emotion}（由本地 SenseVoice 从语音语气识别，真实数据，不是猜测）"
                f"\n用法：用户问「我是什么心情/情绪」时，直接告诉他识别结果（如：听起来有点生气/挺平静的/很开心），"
                f"绝不要说『读不到/没有情绪数据/不能读心』。同时按情绪调整语气：生气→先安抚，难过→温柔些，开心→一起开心。"
            )
        try:
            tm = TaskManager()
            # 隐私：只给 AI 当前说话人自己的待办（owner 过滤，绝不混入别人的任务）
            pending = tm.list_tasks(status="pending", limit=5, owner=speaker)
            if pending:
                task_lines = [f"  - {t['content']}（{t.get('trigger_time', '无时间')}，{t['status']}）" for t in pending]
                task_context = f"\n\n## 用户当前待办任务\n" + "\n".join(task_lines)
            else:
                task_context = "\n\n## 用户当前待办任务\n（无待办任务）"
        except Exception:
            task_context = ""
    else:
        identity_context = "\n\n## 当前说话人\n访客（未通过语音识别）"
        task_context = ""   # 访客：不给任务/提醒等私密信息
    
    enhanced_prompt = SYSTEM_PROMPT + time_context + identity_context + task_context + PRIVACY_RULES
    
    messages = []
    
    if conversation_context:
        messages.extend(conversation_context[-6:])
    
    messages.append({"role": "user", "content": user_message})
    
    result = await call_ai(messages, enhanced_prompt)
    
    if result["error"]:
        print(f"[ai] call_ai error: {result['error']}")
        # L5：AI 健康监控 —— 连续失败阈值告警（不改变回复内容）
        try:
            from .monitor import note_ai_failure
            note_ai_failure(False)
        except Exception:
            pass
        return {
            "action": "chat",
            "reply": "抱歉，AI服务暂时不可用，请稍后再试。",
            "task": None,
            "appointment": None
        }
    try:
        from .monitor import note_ai_failure
        note_ai_failure(True)
    except Exception:
        pass

    parsed = _extract_json(result["content"])

    if parsed and isinstance(parsed, dict):
        has_mechanical = any(parsed.get(k) for k in ("task", "tasks", "appointment"))
        if "reply" in parsed or has_mechanical:
            return {
                "action": parsed.get("action", "chat"),
                "reply": parsed.get("reply") or ("好的，已记下。" if has_mechanical else ""),
                "task": parsed.get("task"),
                "tasks": parsed.get("tasks"),
                "appointment": parsed.get("appointment"),
                "search": parsed.get("search"),
                "speaker": parsed.get("speaker"),
            }

    # JSON 解析失败 → 绝不把原始输出（可能含 {\"action\":...} JSON）展示给用户。
    # 用极简提示重试一次，逼模型只回 JSON；仍失败则给安全兜底回复。
    if result.get("content"):
        try:
            retry_result = await call_ai(
                messages,
                enhanced_prompt + "\n\n上次输出不是合法 JSON，请只输出一个 JSON 对象，"
                "字段：action/reply/task/tasks/appointment/search，不要任何解释或代码块标记。",
                retries=1,
            )
            retry_parsed = _extract_json(retry_result.get("content"))
            if retry_parsed and isinstance(retry_parsed, dict) and retry_parsed.get("reply"):
                return {
                    "action": retry_parsed.get("action", "chat"),
                    "reply": retry_parsed.get("reply", "好的，已记下。"),
                    "task": retry_parsed.get("task"),
                    "tasks": retry_parsed.get("tasks"),
                    "appointment": retry_parsed.get("appointment"),
                    "speaker": retry_parsed.get("speaker"),
                    "search": retry_parsed.get("search"),
                }
        except Exception:
            pass

    return {
        "action": "chat",
        "reply": "嗯，我在听，你再说一遍？",
        "task": None,
        "appointment": None
    }
