# Spark Extensions — ROADMAP (autonomous build backlog)

This file is the **source of truth** for the scheduled builder agents. Each job
reads this, takes the next **unchecked** items, marks them `[~]` (in progress)
with its run id, builds them per `BUILD_AGENT.md`, and marks them `[x]` when
committed+pushed+tested.

Format: `- [ ] item` = todo · `- [~] rid` = in progress · `- [x] rid` = done.

Rules for builders:
- NEVER edit a `[~]` item another job claimed — take the next `[ ]`.
- One item per attempt; if it fails, leave it `[ ]` and note why in the log.
- Any extension you add MUST follow the contract in `EXTENSIONS.md` and
  `WATCHER.md`: **tools-only, returns `data`, never speaks, never consumes a turn.**

---

## Backlog — tools to build (Tier A/B)

- [x] builder-E **notes** — capture a note to a dated file; tool `note(command=add|list|read, text=...)`
- [x] builder-E **todo** — add/list/complete items in a local JSON list; tool `todo(command=add|list|done, item=...)`
- [x] builder-G **timer** — countdown + fire via the task engine; tool `timer(duration=..., label=...)`
- [x] builder-E **dice** — roll dice / flip coin / random pick; tool `random(kind=dice|coin|pick, spec=...)`
- [x] builder-F **clipboard** — read/write the macOS clipboard; tool `clipboard(command=read|write, text=...)`
- [x] builder-G **appcontrol** — open/quit/focus apps via osascript; tool `app(command=open|quit|focus, name=...)`
- [x] builder-G **volume** — system output volume + mute; tool `sysvolume(command=set|up|down|mute, level=...)`
- [x] builder-A **screenshot** — capture the screen to a file; tool `screenshot(mode=screen|window, path=...)`
- [x] builder-A **spelling** — spell a word / define it (local dict if present); tool `spell(word=...)`
- [x] builder-F **unitconv** — units/currency conversion; tool `convert(value=..., from=..., to=...)`
- [ ] **translation** — translate text (network scope); tool `translate(text=..., to=...)`
- [ ] **weather** — current weather (network scope); tool `weather(city=...)`
- [x] builder-A **reminder** — quick reminder via the task engine; tool `reminder(content=..., when=...)`
- [ ] **search** — web search (network scope, needs key); tool `search(query=...)`
- [x] builder-A **systemstats** — disk/battery/memory; tool `stats(what=disk|battery|mem|all)`
- [ ] **focus** — Do Not Disturb / focus mode; tool `focus(on|off, minutes=...)`
- [ ] **photos** — search the Photos library by description; tool `photo(query=...)`
- [ ] **calendar** — read today's events; tool `calendar(range=today|tomorrow|week)`
- [ ] **mail** — unread count + quick summary (network scope); tool `mail(command=count|latest)`
- [ ] **dailybrief** — one-shot morning rundown combining clock+weather+tasks; tool `brief()`

## Backlog — framework refinements

- [x] builder-F **failure visibility** — dashboard event + route-log field when a skill fails
- [ ] **fast path** — skip the agent when every action succeeded and no prose needed (opt-in per skill)
- [~] builder-A **arg schemas** — enforce typed args in the registry (number/enum/date/duration)
- [ ] **date args** — let skills declare a `date` arg the runner resolves from phrases
- [ ] **chaining test** — prove `depends_on` across two actions end to end
- [ ] **confirm gate** — prove `confirm: true` blocks then runs on a spoken yes
- [ ] **idempotency** — dedup routes by id so a retried hook never double-creates
- [ ] **skills panel** — dashboard panel listing extensions + tools + scopes + health
- [ ] **route log view** — dashboard view of `voice_routes.jsonl` with pass/fail
- [ ] **model fallback** — try qwen then a backup local model if the first errors
- [ ] **few-shot tuning** — mine `voice_routes.jsonl` for bad routes, add examples
- [ ] **context window** — feed last N turns to the watcher for pronouns ("turn it down")
- [ ] **unit tests** — pytest suite for registry/watcher/bridge (no live model needed)
- [ ] **hot-reload test** — prove dropping a folder loads it without a restart
- [ ] **docs pass** — keep EXTENSIONS.md / WATCHER.md / BUILD_AGENT.md current

## Done (append `[x] <rid>` as jobs finish)

- [x] clock — done (v0.2.0, tools-only)
- [x] calculator — done (v0.2.0, tools-only, model normalizes expr)
- [x] music — done (v0.2.0, tools-only: play/stop/volume)
- [x] builder-E **notes** — done (v0.1.0, tools-only: add/list/read, dated file)
- [x] builder-E **todo** — done (v0.1.0, tools-only: add/list/done, local JSON)
- [x] builder-E **dice** — done (v0.1.0, tools-only: random dice/coin/pick)
- [x] builder-G **timer** — done (v0.1.0, tools-only: parse duration -> schedule speaking alert via task engine)
- [x] builder-G **appcontrol** — done (v0.1.0, tools-only: app open/quit/focus/running via osascript)
- [x] builder-G **volume** — done (v0.1.0, tools-only: sysvolume set/up/down/mute/unmute/get via osascript)
- [x] builder-A **screenshot** — done (v0.1.0, tools-only: capture screen/frontmost-window via screencapture + osascript)
- [x] builder-A **spelling** — done (v0.1.0, tools-only: spell letters offline + define via macOS DictionaryServices)
- [x] builder-A **reminder** — done (v0.1.0, tools-only: parse relative/clock times -> task engine speak)
- [x] builder-A **systemstats** — done (v0.1.0, tools-only: disk/battery/mem via df/pmset/vm_stat)
