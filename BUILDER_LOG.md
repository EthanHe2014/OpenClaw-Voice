# BUILDER_LOG

Append-only. One line per builder run:
`<iso time> | <run id> | <item> | <ok|fail> | <commit sha> | <note>`

(seed entry below)
2026-10-11T16:50+08:00 | seed | framework+scaffolding | ok | - | roadmap, playbook, cron fleet created by Spark
2026-10-11T17:11+08:00 | builder-E | notes | ok | dfad133 | tools-only note(add/list/read) to dated file in data_dir; unit-tested + live-load check passed
2026-10-11T17:14+08:00 | builder-E | todo | ok | 199224b | tools-only todo(add/list/done) local JSON in data_dir; unit-tested + live-load check passed
2026-10-11T17:17+08:00 | builder-E | dice | ok | 3716fd7 | tools-only random(dice/coin/pick); unit-tested + live-load check passed
2026-10-11T19:13+0800 | builder-F | failure visibility | ok | 1a4dca1 | dashboard WATCHER event + route-log failures field + agent note when a skill fails; unit-tested + live-load check passed
2026-10-11T19:14+0800 | builder-F | clipboard | ok | 9ba98d3 | tools-only clipboard(read/write/clear) via pbcopy/pbpaste; unit-tested + live-load check passed
2026-10-11T19:15+0800 | builder-F | unitconv | ok | 58e3c66 | tools-only convert(value/from/to) for length/mass/volume/time/speed/data/temperature (offline, no currency); unit-tested + live-load check passed
2026-10-11T21:14+0800 | builder-G | timer | ok | 693e463 | tools-only timer(duration,label) parses countdown phrase offline -> absolute time -> task engine speak; unit-tested + live-load check passed
2026-10-11T21:18+0800 | builder-G | appcontrol | ok | 58cf2a3 | tools-only app(open/quit/focus/running) via osascript; unit-tested + live-load check passed
2026-10-11T21:20+0800 | builder-G | volume | ok | 889fde5 | tools-only sysvolume(set/up/down/mute/unmute/get) via osascript; unit-tested (state restored) + live-load check passed
2026-10-12T09:13+08:00 | builder-A | screenshot | ok | 594617a | tools-only screenshot(mode=screen|window, optional path) via screencapture/osascript; unit-tested + live-load check passed
2026-10-12T09:14+08:00 | builder-A | spelling | ok | 40a1271 | tools-only spell(spell/define) via string split + DictionaryServices (offline); unit-tested + live-load check passed
2026-10-12T09:15+08:00 | builder-A | reminder | ok | 3d5b6f5 | tools-only reminder(content/when) parses relative + clock phrases -> task engine speak; unit-tested + live-load check passed
2026-10-12T09:16+08:00 | builder-A | systemstats | ok | b11f19b | tools-only stats(disk/battery/mem/all) via df/pmset/vm_stat (offline); unit-tested + live-load check passed
2026-10-12T09:18+08:00 | builder-A | arg schemas | ok | 12cf803 | registry enforces string/number/enum/date/duration + coerces numeric strings; unit-tested + live restart clean (15/15 ext active)
2026-10-12T09:21+08:00 | builder-A | docs pass | ok | ede3160 | EXTENSIONS.md arg-schema table + scope note; WATCHER.md typed-args corrected; added bool arg type
2026-10-12T11:20+08:00 | builder-B | unit tests | ok | 6dce741 | 37 pytest tests for registry/watcher/bridge (typed args, route result, confirm gate, critical abort, passthrough invariants, agent handoff); no live model; live loop untouched
2026-10-12T11:24+08:00 | builder-B | hot-reload test | ok | 8ff62d6 | 6 pytest cases prove drop/edit/remove folder + broken ext containment via Manager.reload_tick(); no restart, live loop untouched
2026-10-12T11:31+08:00 | builder-B | idempotency | ok | 0bb72e8 | Registry.run_route dedups by route id via bounded LRU (512); repeat returns prior RESULT with deduped:true; 5 tests; live loop restarted clean (15/15 ext)
2026-10-12T13:16+08:00 | builder-C | translation | ok | 54828eb | tools-only translate(text/to/from) offline phrasebook (no network scope exists); unit-tested + live hot-load check passed (16/16 ext active)
2026-10-12T13:22+08:00 | builder-C | skills panel | ok | 924a047 | /skills JSON endpoint + dashboard panel (id/version/state/scopes/hooks/error) fed by EXT_MGR.status()+EXT_REG.catalog(); live restart clean, 16/16 ext, 16 tools, no errors
2026-10-12T13:23+08:00 | builder-C | note: remaining tool backlog | skip | - | weather/search/mail need network scope (no host HTTP capability) and/or API keys; focus/calendar/photos need TCC permission; left [ ] per contract
2026-10-12T15:18+08:00 | builder-D | route log view | ok | 8125134 | /routes JSON endpoint + dashboard 'watcher routes' panel over voice_routes.jsonl (per-route ok/fail + failures, whole-file pass/fail + bad-rate); unit-tested (_smoke_routes.py, real + synthetic mix) + live restart clean (16/16 ext, 16 tools, no errors)
2026-10-12T15:26+08:00 | builder-D | chaining test | ok | f47a027 | implemented depends_on resolution in Registry.run_route (merge upstream action's data into downstream args, own args win; failed/forward-index -> dependency_failed) + 5 chaining tests in tests/test_registry.py; WATCHER.md note; full suite green (51 tests); live restart clean (16/16 ext, 16 tools)
2026-10-12T15:34+08:00 | builder-D | confirm gate | ok | 50c0fef | bridge stashes confirm:true routes, classifies next short utterance yes/no/other deterministically, re-runs only blocked actions under fresh '-confirmed' route id on yes, drops on no/other; 5 tests in tests/test_bridge.py; WATCHER.md note; full suite green (58 tests); live restart clean (16/16 ext, 16 tools)
2026-10-12T17:14+0800 | builder-E | context window | ok | dbdcd92 | bridge owns a bounded TurnBuffer (N=6), feeds last turns to watcher.route(history=...) so pronouns resolve across live turns; prompt uses history only for references; 5 new tests (63 total); live restart clean (16/16 ext, 16 tools, no errors)
2026-10-12T19:11+0800 | builder-F | model fallback | ok | b4333d9 | Watcher tries watcher_model then watcher_backup_models (same Ollama server), degrades to passthrough only if ALL error; config DEFAULTS + ENV_MAP + list coercion; 4 new tests (67 total); live restart clean (16/16 ext, 16 tools)
2026-10-12T19:14+0800 | builder-F | fast path | ok | 0aa4b3fcc77aa931f0241cc89c1aff8cadbc861a | opt-in per skill (fast:true in tool decl); Registry sets fast_path/fast_speech only when every action is fast, all succeed, none pending confirm, and chat:false; loop speaks fast_speech and skips the agent. Dormant until a skill opts in. 6 tests + _smoke_fastpath.py; full suite 78 green; live restart clean (16/16 ext, 16 tools, 0 errors)
2026-10-12T19:14+0800 | builder-F | few-shot tuning | skip | - | blocked: voice_routes.jsonl holds only 2 real routes, 0 failures - nothing to mine for bad-route examples yet; left [ ] (revisit once the log has volume)
2026-10-12T21:20+08:00 | builder-G | weather | ok | ebf899a | tools-only weather(city) via keyless Open-Meteo (stdlib urllib, network scope); WMO->English phrase, geocode+forecast cached in state, offline-safe; watcher prompt + example added; unit-tested + live restart clean (17/17 ext, 17 tools, 0 errors)
