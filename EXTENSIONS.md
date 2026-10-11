# Spark Extensions — framework design

Status: v0.1 (skeleton). Author: Spark + Ethan.

## Why

Spark today is a voice loop (`spark_web.py`) plus a reminder engine
(`sparktasks/`). Adding a capability means editing those files — surgery every
time. OpenMemo was bolted on. We want *many* polished add-ons, so we build one
loading system and every new capability is a **drop-in folder**, not a patch.

Goal: add a feature by creating `extensions/<name>/` with two files. No edits
to core. Enable/disable without restarting the whole pipeline.

## The pipeline (where extensions hook)

```
  mic ──► wakeword ──► STT ──►[on_transcript]──► agent (OpenClaw hook)
                                    │                    │
                                    │              [on_reply]
                                    ▼                    ▼
                              create tasks ──►[task hooks]  speak ──►[on_speak]──► speaker
                                    │                              │
                              dashboard events ◄──[ctx.emit]       HTTP routes ◄──[ctx.register_route]
```

Every box in `[brackets]` is a **hook point**. An extension declares which ones
it wants in its manifest, and the framework calls it at that moment.

## Extension = a folder

```
extensions/
  clock/
    extension.json     # manifest: identity, scopes, hooks, config
    extension.py       # code: one setup(ctx) function
  openmemo/
    extension.json
    extension.py
```

### `extension.json`

```json
{
  "id": "clock",
  "name": "Clock & Timers",
  "version": "0.1.0",
  "description": "Speaks the time, sets quick timers.",
  "author": "Ethan",
  "entry": "extension.py",
  "enabled": true,
  "scopes": ["speak", "tasks", "log"],
  "hooks": ["on_transcript"],
  "config": { "timezone": "Asia/Shanghai" }
}
```

### `extension.py`

```python
def setup(ctx):
    ctx.log("clock extension ready")

    def on_transcript(text, ctx):
        # return a new string to REWRITE what the agent sees,
        # or None to leave it untouched.
        if text.strip().lower() in ("what time is it", "what's the time"):
            ctx.speak("It's " + ctx.now("%H:%M") + ".")
            return None
        return None

    return {"on_transcript": on_transcript}
```

`setup(ctx)` returns a dict of `{hook_name: callable}`. That's the whole
contract. Simple, testable, no magic.

## Hook points

| Hook | Called with | Return | Use for |
|---|---|---|---|
| `on_startup` | `ctx` | — | load models, open files, warm caches |
| `on_shutdown` | `ctx` | — | flush, close, save |
| `on_transcript` | `(text, ctx)` | `str` or `None` | rewrite/pre-empt STT before the agent (commands, translation, wake filters) |
| `on_reply` | `(text, ctx)` | `str` or `None` | postprocess the agent's reply before speech (cleanup, tone, length) |
| `on_speak` | `(text, ctx)` | — | side-effects when something is spoken (ducking, logging) |
| `on_task_created` | `(task, ctx)` | — | react when the engine schedules a reminder |
| `tools` | `(ctx)` | `list[dict]` | expose callable tools to the agent |

Rule: **a hook may rewrite text but must never block or crash the pipeline.**
The loader wraps every call in try/except and logs a failure as an event; the
voice loop never dies because one extension broke.

## The context object (`ctx`)

What every extension is handed — the *only* way it touches Spark:

| Member | Meaning |
|---|---|
| `ctx.id`, `ctx.name`, `ctx.config` | identity + merged config from the manifest |
| `ctx.log(msg)` | write to the dashboard event feed (as INFO) |
| `ctx.data_dir` | private folder for this extension (`data/extensions/<id>/`) |
| `ctx.speak(text)` | queue text through Spark's TTS (English) |
| `ctx.ask_agent(text)` | ask the real Spark agent, get the reply (for tool-style extensions) |
| `ctx.emit(kind, detail)` | push an event to the dashboard |
| `ctx.now(fmt)` | current local time by Spark's clock |
| `ctx.register_route(path, handler)` | add `GET /ext/<id>/<path>` to the dashboard server |
| `ctx.create_task(content, time, speak)` | schedule a reminder (drops a JSON file into `voice_inbox/`) |
| `ctx.get_state(key)` / `ctx.set_state(key, val)` | per-extension key/value store (persisted) |

Extensions get **capabilities, not access**. They can't read arbitrary files or
hit the network unless we grow the context to allow it — which is the point.

## Scopes (permissions)

The manifest declares what it needs; the loader enforces:

- `speak` — may call `ctx.speak`
- `agent` — may call `ctx.ask_agent`
- `tasks` — may call `ctx.create_task`
- `network` — outbound HTTP (future; off by default)
- `files` — read/write outside its own `data_dir` (off by default)
- `log` — dashboard events

An extension that calls something outside its scopes gets a logged warning and
a no-op, not a crash. Keeps a random extension from silently joining your Wi-Fi.

## Isolation & failure

- Each extension loads in-process (simple, fast, and fine for locally-authored
  code). No sandbox yet — that's a later tier.
- Every hook invocation is guarded: one bad extension logs an error and is
  skipped for that call; the pipeline continues.
- An extension that throws on `setup` is marked **failed** and disabled, with
  the reason shown on the dashboard.

## Hot reload

The loader watches the `extensions/` directory (poll on a timer). Dropping a new
folder or editing a manifest loads/unloads it while Spark runs — no restart. Same
mechanism as the mic hot-swap we already built.

## Dashboard

A new **Extensions** panel lists every extension: name, version, enabled state,
scopes, and hooks it provides. Toggle on/off from there. Failure reasons show
inline. (The Control UI widget can render this read-only.)

## Build order

1. **Loader + manifest + context** (`sparkext/` package) — the engine. *(this commit)*
2. **Reference extension** (`extensions/clock/`) — proves the contract end to end.
3. **Wire hooks into `spark_web.py`** — call `on_transcript` before `ask_spark`,
   `on_reply` before `speak`, `on_speak` inside `speak`. *(step 2 — touches the live loop)*
4. **Port OpenMemo** into `extensions/openmemo/` as the big reference.
5. **Extensions dashboard panel** + enable/disable toggle.

## Non-goals (for now)

- No third-party/sandboxed code execution.
- No marketplace. Local folders first; ClawHub-style sharing later.
- Extensions never own the mic loop or the agent call — they *hook* them.
