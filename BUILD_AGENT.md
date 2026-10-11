# BUILD_AGENT.md — playbook for autonomous extension builders

You are a scheduled builder for **Spark**, a local voice assistant. Your job:
read `ROADMAP.md`, take the next unchecked item, build it properly, test it,
commit + push, and log what you did. Work autonomously. Do not ask questions.

## Project

- Root: `/Users/Ethan/Desktop/Projects/Spark Voice`
- Python venv: `./.venv/bin/python` (use this, never system python, for project code)
- Repo: `https://github.com/EthanHe2014/OpenClaw-Voice.git` (branch `main`)
- Framework docs: `EXTENSIONS.md` (plugin system) and `WATCHER.md` (router contract)
- Backlog: `ROADMAP.md` (the queue you pull from)

## The contract (READ THIS OR YOU WILL BUILD IT WRONG)

Two hard rules, learned the hard way:

1. **Extensions NEVER speak and NEVER consume the turn.** An extension runs a
   tool and RETURNS DATA. OpenClaw phrases the spoken reply; the TTS helper
   reads *that*. If you call `ctx.speak` or return `""` from `on_transcript` to
   swallow the turn, you have broken the design.
2. **The MODEL interprets language, the TOOL stays dumb.** The watcher's prompt
   converts spoken words into clean machine args (e.g. "100 times 3 cubed" ->
   `expr="100*3**3"`). Your tool receives already-clean args and just executes.

So every tool extension looks like this:

- `extensions/<id>/extension.json` — manifest:
  ```json
  {
    "id": "notes", "name": "Notes", "version": "0.1.0",
    "description": "...", "author": "Ethan", "entry": "extension.py",
    "enabled": true, "scopes": ["log"],
    "hooks": ["tools"], "config": {}
  }
  ```
  Scopes: `log` (events), `network` (outbound HTTP), `files` (outside its own
  `data_dir`). Request only what you need.
- `extensions/<id>/extension.py` — code:
  ```python
  import os, re, subprocess

  def setup(ctx):
      ctx.log("notes ready")

      def do_notes(args, ctx):
          # args are ALREADY normalized by the model
          # return {"ok": True, "data": {...}}  (structured output)
          # or     {"ok": False, "error": "why"}
          return {"ok": True, "data": {"saved": True}}

      def tools(ctx):
          return [{
              "name": "notes",
              "description": "Add or list notes. command='add'|'list'.",
              "args": {
                  "command": {"type": "enum", "required": True, "values": ["add", "list"]},
                  "text": {"type": "string", "required": False},
              },
              "fn": do_notes,
          }]

      return {"tools": tools}
  ```

### Adding the tool to the watcher prompt

For the model to route to your tool, it must be described in the watcher's
system prompt examples. To register it:
- The tool's `name`, `description`, and `args` are auto-collected into the
  catalog the watcher sees (via `Registry.collect_from`). Good descriptions and
  enum args are usually enough.
- If the tool needs arg normalization (like calculator's `expr` or music's
  `level`), add a line to the GRADER examples in `sparkext/watcher.py`
  `_system_prompt`. Keep it short.

## How to test (always, before commit)

1. Syntax: `./.venv/bin/python -c "import ast; ast.parse(open('extensions/<id>/extension.py').read())"`
2. Tool unit test (no model): load the Manager, call the skill's `fn` directly
   with sample args, assert the `data`. Use the pattern in `_smoke_ext.py` /
   `_smoke_watcher.py`.
3. If it touches the live loop: restart and check it loads:
   `launchctl kickstart -k gui/502/ai.openclaw.spark.web` then
   `curl -s http://127.0.0.1:8770/state | python3 -m json.tool | grep -i <id>`
4. Never leave the pipeline broken. If a change risks the live loop and you
   cannot test it, revert your change and leave the roadmap item `[ ]`.

## Commit + push (every finished item)

The GitHub push needs the local proxy or it hangs. Always:

```bash
cd "/Users/Ethan/Desktop/Projects/Spark Voice"
git add -A
git -c user.name=Ethan -c user.email=EthanHe2014@users.noreply.github.com commit -m "<what + why>"
export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897 all_proxy=http://127.0.0.1:7897
git push
```

Confirm `git rev-parse HEAD` == `git rev-parse origin/main` after pushing.

## ROADMAP protocol

- Before building: pick the next `- [ ]` item. Change it to `- [~] <your run id>`
  and commit that claim FIRST (so a parallel job won't grab it).
- After a successful build+push: change it to `- [x] <run id>`.
- If you fail: revert it to `- [ ]` and append a line to `BUILDER_LOG.md` saying
  what failed and why.
- One run may do several small items if budget allows — but finish each fully
  (build, test, commit, push, mark) before starting the next.

## Guardrails

- NEVER delete or rewrite `spark_web.py` wholesale; patch surgically. The voice
  pipeline must stay up.
- NEVER touch secrets, tokens, `.env`, `data/`, models, or recordings.
- NEVER run destructive shell (`rm -rf`, etc.). Prefer reverting your own edits.
- Keep tools deterministic and fast (<1s). Long work detaches via launchd.
- English only in anything spoken. No emojis anywhere.
- If an item needs a paid API or a new credential, SKIP it, leave it `[ ]`, and
  note "needs credential" in `BUILDER_LOG.md` — do not sign up for anything.

## Your report

End with a short log line appended to `BUILDER_LOG.md`:
`<iso time> | <run id> | <item> | <ok|fail> | <commit sha> | <one-line note>`
