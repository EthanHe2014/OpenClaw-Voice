# WATCHER contract — the skill-router JSON

Status: design v0.1. Author: Spark + Ethan.

## The idea

The watcher (a small LLM) sees the STT transcript and does **one** thing: decide
what should happen, and emit a **route** as JSON. It never runs a skill. A
deterministic **runner** executes the route, then hands a **result** block to
OpenClaw, which writes the spoken reply.

```
STT ─► WATCHER (LLM, JSON-constrained) ─► route.json ─► RUNNER (python)
                                                          │
                                          result.json ◄───┘
                                                          │
                       OpenClaw hook: user_text + note + tool_output
                                                          │
                                                     plain text ─► TTS
```

Hard rule: **the watcher can degrade to OpenClaw at any point.** If it is unsure,
the JSON is invalid, or confidence is low, it emits a plain passthrough and the
system behaves exactly like today. Never worse than current.

## Document 1 — ROUTE (watcher -> runner)

```json
{
  "v": 1,
  "id": "r-0f3a9c",
  "ts": "2026-10-11T16:10:02+08:00",
  "transcript": "what's 47 times 89 and remind me to stretch in 20 minutes",
  "locale": "en-US",
  "tz": "Asia/Shanghai",
  "intent": "mixed",
  "confidence": 0.93,
  "actions": [
    {
      "skill": "calculator",
      "skill_version": ">=0.1",
      "args": { "expr": "47*89" },
      "depends_on": null,
      "critical": false,
      "confirm": false,
      "on_error": "skip",
      "timeout_ms": 3000,
      "speak_hint": null
    },
    {
      "skill": "reminder",
      "skill_version": ">=0.1",
      "args": { "content": "stretch", "when": "2026-10-11 16:30" },
      "depends_on": null,
      "critical": false,
      "confirm": false,
      "on_error": "ask",
      "timeout_ms": 3000,
      "speak_hint": null
    }
  ],
  "chat": true,
  "clarify": null,
  "notes": "two independent intents; neither needs agent reasoning",
  "context_used": []
}
```

### Envelope fields

| Field | Meaning |
|---|---|
| `v` | schema version (the runner refuses unknown majors) |
| `id` | unique id; used for dedup + correlating the result |
| `ts`, `tz`, `locale` | when + how to parse relative times ("in 20 min", "tomorrow") |
| `transcript` | the exact words, kept for OpenClaw and the log |
| `intent` | `skill` \| `chat` \| `mixed` \| `clarify` \| `noop` |
| `confidence` | 0–1; below threshold ⇒ runner treats it as `chat` (passthrough) |
| `actions` | ordered list (see below). Empty ⇒ pure chat |
| `chat` | true ⇒ also forward to OpenClaw (it may still write prose / run un-ported skills) |
| `clarify` | if set, the question to ask the user instead of acting |
| `notes` | freeform reasoning, for the log only — OpenClaw may read it |
| `context_used` | which recent turns were needed (audit trail for pronoun resolution) |

### Action fields

| Field | Meaning |
|---|---|
| `skill` | extension id from its manifest |
| `skill_version` | optional constraint (`>=0.1`) |
| `args` | typed arguments (see below) |
| `missing` | required args the model could not infer ⇒ runner asks, does not guess |
| `depends_on` | index of a prior action whose output feeds this one (chaining) |
| `critical` | if true, its failure aborts the whole route |
| `confirm` | true ⇒ runner must get a "yes" before executing (destructive ops) |
| `on_error` | `skip` \| `ask` \| `abort` — what to do if the skill fails |
| `timeout_ms` | per-action budget |
| `speak_hint` | optional phrasal steer for TTS |

### Design aspects covered

1. **Multi-intent** — `actions[]` is ordered and may hold several skills from one
   utterance ("what's the weather, and remind me…").
2. **Chaining / dependencies** — `depends_on` lets a later action consume an
   earlier result (e.g. "get my next event, then text me the address").
3. **Typed args** — the runner validates against each extension's arg schema:
   `string` / `number` / `bool` / `enum` / `date` / `duration`, coercing numeric
   strings and rejecting clearly-wrong or missing args (`invalid_args` /
   `missing_args`, reported with the offending arg name). The skills that take a
   time (timer, reminder) parse the phrase themselves, deterministically and
   offline, using their own clock — the model hands them the literal phrase.
4. **Missing / ambiguous args** — never invent. `missing[]` ⇒ runner asks the
   user; or `intent:"clarify"` if the whole request is ambiguous.
5. **Pronoun / context** — "turn *it* down" needs history. The watcher gets the
   last N turns; `context_used` records what it leaned on.
6. **Safety** — destructive skills carry `confirm:true`; runner gates on the next
   utterance being a yes. Scope checks from the manifest still apply.
7. **Idempotency** — `id` lets the runner ignore a repeated route (e.g. a retried
   hook) so a reminder is never created twice.
8. **Error taxonomy** — `unknown_skill`, `bad_args`, `missing_args`, `skill_error`,
   `timeout`. Each maps to an `on_error` behavior.
9. **Budgets** — `confidence` threshold + per-action `timeout_ms` keep worst-case
   latency bounded; the watcher itself gets a hard wall-clock cap.
10. **Fallback = passthrough** — any failure path ends as `intent:"chat"`, i.e.
    today's behavior. This is the invariant that makes it safe to ship early.
11. **Constrained decoding** — the watcher emits JSON against this exact schema
    (Ollama structured outputs), so malformed JSON is impossible by construction.
12. **Observability** — every route is appended to a log so you can see *why* a
    skill fired, and tune the few-shot examples.

## Document 2 — RESULT (runner -> OpenClaw)

What actually reaches OpenClaw is **the original words + a note + tool output**:

```json
{
  "v": 1,
  "id": "r-0f3a9c",
  "user_text": "what's 47 times 89 and remind me to stretch in 20 minutes",
  "results": [
    { "skill": "calculator", "ok": true, "speak": "4183", "data": {"value": 4183}, "ms": 11 },
    { "skill": "reminder",   "ok": true, "speak": null, "data": {"task_id": 42}, "ms": 8 }
  ],
  "handled": ["calculator", "reminder"],
  "forward_to_agent": true,
  "agent_note": "The watcher already ran: calculator (result 4183) and reminder (scheduled for 16:30). Do NOT recompute or re-create these. Compose ONE short spoken reply. If the user asked something else too, handle that normally.",
  "needs_confirmation": false
}
```

### What OpenClaw receives (rendered)

```
what's 47 times 89 and remind me to stretch in 20 minutes

[WATCHER] already handled: calculator -> 4183; reminder -> scheduled 16:30.
Do NOT redo these. Compose a short spoken reply, and handle anything else.
```

So OpenClaw:
- stops carrying the giant skill prompt,
- is told exactly what **not** to redo,
- gets the **tool output** to weave into prose,
- still handles any skill you haven't ported yet.

### Result fields

| Field | Meaning |
|---|---|
| `handled[]` | skill ids that already ran (the "don't redo" list) |
| `results[]` | per-skill outcome: `ok`, `speak` (verbatim if the skill wants to be read), `data` (structured), `ms` |
| `forward_to_agent` | false ⇒ watcher's `speak` is read directly, skip the agent (fast path) |
| `agent_note` | the instruction to the agent (the "taken care of, don't redo" note) |
| `needs_confirmation` | true ⇒ runner is waiting on a yes/no before acting |

## Fast path (optional)

If **every** action succeeded and `chat:false` (pure skill, no prose needed), the
runner can skip OpenClaw entirely and speak `result.speak` straight through. Saves
a full agent round-trip for trivia like "what time is it". Default stays
`forward_to_agent:true` until we trust it.

## Open questions to settle

1. **Brain:** local 7–8B (private, needs install) vs gateway flash (robust, ships
   your speech). My lean: local, with constrained decoding.
2. **History window** for pronoun resolution — how many turns does the watcher see?
3. **Fast path** on by default, or opt-in per skill?
4. **Confidence threshold** — 0.7? Tune with the route log.
