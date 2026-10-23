# WATCHER contract — the skill-router JSON

Status: design v0.2. Author: Spark + Ethan.

## The idea

The watcher (a small LLM) sees the STT transcript and decides what should happen.
It emits a **route** as JSON and never runs a skill itself (a deterministic
runner does), but it **may finish the turn**: when its route fully covers the
request it writes its own spoken `reply` and OpenClaw is skipped entirely.

```
STT ─► WATCHER (LLM, JSON) ─┬─► reply present ──────────────► speak it (no agent)
                           └─► reply null ─► RUNNER ─► tool output ─► OpenClaw ─► TTS
```

Hard rule: **the watcher can degrade to OpenClaw at any point.** If it is unsure,
the JSON is invalid, confidence is low, or it wrote no reply, it emits a plain
passthrough and the system behaves exactly like before. Never worse than current.

## Local handler (v0.3) — two phases, never a template

The local model is not a router that also writes answers. Writing the words
*before* the skills run forces it to guess, and any example reply becomes a
template a small model parrots. So the turn has two phases:

```
1. ROUTE    decide which skills + args, and whether skills can FULLY cover it
            (`answer`). No prose is written here, ever.
2. RUN      the deterministic runner executes the skills.
3. COMPOSE  the local model phrases the REAL output in its own words.
```

Compose only runs when the skills actually produced a result. It receives the
actual data, so it cannot invent a number. If compose returns nothing, the turn
falls back to OpenClaw — never silence.

**Asking.** When a required arg is unknown the model marks `missing[]` and the
tool refuses; the local model then asks for exactly that, in its own words:

> user: "play some music" → route `music(play)` with no target → tool returns
> `missing_args: [target]` → "Can you tell me what kind of music you're in the
> mood for?"

Nothing is invented: `target` may only be filled from what the user actually
named. "my liked songs" → `target: "liked"`; "play some music" → ask.

`chat` is the inverse of `answer`: `answer:true` ⇒ `chat:false` (turn ends
locally), `answer:false` ⇒ `chat:true` (escalate).

Safety: a route whose skills reported a *hard* failure never composes, even if
`answer` was true — the agent gets the turn so the user is told the truth.
`missing_args` alone does not block, because that case *is* the question. Any
exception degrades to passthrough.

History turns are passed to the model **explicitly labelled** as past context. A
small model otherwise treats unlabelled prior user turns as part of the current
request and re-runs their skills (observed: actions accumulating across turns).

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
   earlier result (e.g. "get my next event, then text me the address"). The
   runner is implemented: an action with `depends_on: N` (an *earlier* index)
   receives the prior action's `data` dict merged into its args (the action's own
   args win on collision). If the referenced action failed or the index is not an
   earlier action, the dependent action is skipped with `dependency_failed`.
   Covered by `tests/test_registry.py` (chaining section).
3. **Typed args** — the runner validates against each extension's arg schema:
   `string` / `number` / `bool` / `enum` / `date` / `duration`, coercing numeric
   strings and rejecting clearly-wrong or missing args (`invalid_args` /
   `missing_args`, reported with the offending arg name). A declared `date` arg
   is additionally **resolved** by the runner from the model's literal phrase to
   an absolute local value (`sparkext/dates.py`: `mode` `datetime`/`date`/`time`,
   relative + weekday + clock phrases, offline and deterministic), so the skill
   stays dumb. The skills that take a time (timer, reminder) parse the phrase
   themselves, deterministically and offline, using their own clock — the model
   hands them the literal phrase.
4. **Missing / ambiguous args** — never invent. `missing[]` ⇒ runner asks the
   user; or `intent:"clarify"` if the whole request is ambiguous.
5. **Pronoun / context** — "turn *it* down" needs history. The watcher gets the
   last N turns (N=6); `context_used` records what it leaned on. Implemented: the
   bridge owns a bounded `TurnBuffer` (stdlib `deque`, size 6) and passes its
   contents to `watcher.route(..., history=...)` on every turn, so pronouns and
   ellipsis ("do it again") resolve across live turns. The buffer records the
   utterance *after* routing, so the current turn is never duplicated into its
   own history; it dedups a repeated utterance and never raises into the loop
   (empty buffer => exactly the pre-context behaviour). The prompt tells the
   router to use history only to resolve references, never to re-run a previous
   request. Covered by `tests/test_watcher.py` (TurnBuffer) and
   `tests/test_bridge.py` (bridge feeds history).
6. **Safety** — destructive skills carry `confirm:true`; runner gates on the next
   utterance being a yes. Implemented in `sparkext/bridge.py`: when a route is
   blocked, the bridge stashes it and, on the NEXT utterance, classifies a short
   reply deterministically (`yes`/`no`/other). Yes re-runs just the blocked
   actions under a fresh route id (`-confirmed`, so dedup does not suppress them);
   no drops them; anything else drops the pending route and is handled normally.
   Covered by `tests/test_bridge.py` (confirm-gate section). Scope checks from the
   manifest still apply.
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
12. **Model fallback** — the router tries the primary model then each configured
    backup (`watcher_backup_models`) if a call errors (model missing, timeout,
    bad JSON), and only degrades to passthrough if *all* models fail. Success
    path is unchanged (`_models()` returns just the primary with no backups).
    Covered by `tests/test_watcher.py` (model fallback section).
13. **Observability** — every route is appended to a log so you can see *why* a
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

## Fast path (opt-in per skill)

If **every** action in a route ran a skill that opted into the fast path
(`fast: true` in its tool decl), all of them succeeded, nothing is pending
confirmation, and the route asked for **no prose** (`chat:false`), the runner
sets `fast_path:true` and returns `fast_speech` — the concatenation of the
skills' own `speak` strings. The voice loop then speaks that directly and skips
the agent round-trip entirely (saves a full agent call for trivia like
"what time is it").

The gate is strict on purpose: a single non-`fast` action, a failure, a pending
confirmation, or `chat:true` keeps `forward_to_agent:true`, so the default
behaviour is unchanged until a skill explicitly opts in. Covered by
`tests/test_registry.py` (fast-path section). This is question 3 answered:
**opt-in per skill**, not global.

## Open questions to settle

1. **Brain:** local 7–8B (private, needs install) vs gateway flash (robust, ships
   your speech). My lean: local, with constrained decoding. Resolved enough: the
   router is local Ollama (`watcher_model`, default `qwen2.5:7b`) with an optional
   `watcher_backup_models` list tried in order if the primary errors; if every
   model errors the route degrades to a plain passthrough (the safe invariant).
2. **History window** for pronoun resolution — how many turns does the watcher see?
   Settled: 6 (the bridge's `TurnBuffer` default), tunable via `history_size`.
3. **Fast path** on by default, or opt-in per skill? Settled: **opt-in per skill**
   (`fast: true` in the tool decl). The runner sets `fast_path`/`fast_speech`
   only when every action is a `fast` skill, all succeeded, none pending
   confirmation, and `chat:false`; otherwise the agent still phrases the reply.
4. **Confidence threshold** — 0.7? Tune with the route log.
