# Silent reminders — the agent → task-engine inbox

The voice assistant answers you out loud. Separately, a small **scheduler** runs in
the background (`spark_tasks.py`, launchd label `ai.openclaw.spark.tasks`) that
never thinks — it just fires reminders on time and speaks them.

The two talk through a **folder**, not an API. That keeps the scheduler dumb and
reliable, and lets the AI stay in charge of *understanding* your request.

## How it works

1. You say something to Spark.
2. Spark's agent replies. **If a time-bound plan was implied**, the agent also
   silently writes one JSON file into `voice_inbox/`.
3. The scheduler scans `voice_inbox/` every **30 seconds**, turns each file into a
   task, schedules it, and moves the file to `voice_inbox/processed/`.
4. When the time arrives, the scheduler sends the task's exact `speak` text to
   Spark's TTS (`POST http://127.0.0.1:8770/say`) — spoken in **English, verbatim**,
   and the music ducks automatically like any other speech.

## The rule for when to speak vs stay silent

- **Explicit ask** — "set a reminder at 4:00 for a meeting", "remind me to call
  Mom": reply normally **and** confirm it ("Alright, wrote it down!").
- **Implied** — "holy shit how hot is it in Beijing? I need to go to a match at
  3." — add the reminder **silently**. Do **not** mention it to the user.

## File format

One JSON object per file, any filename ending in `.json`:

```json
{
  "content": "go to the match",
  "time": "2026-10-07 15:00",
  "recurring": null,
  "speak": "Hey Ethan, it's 3 o'clock — time to head to the match.",
  "priority": "medium"
}
```

| field | required | notes |
|-------|----------|-------|
| `content` | yes | short label, shown in logs / DB |
| `time` | yes | **absolute local** time, `YYYY-MM-DD HH:MM` (24h) |
| `speak` | recommended | the **exact English words** to say. Defaults to `content`. |
| `recurring` | no | `每天` / `工作日` / `每周一` / `每周一三五` / `每月1号` / `每2小时` |
| `priority` | no | `high` / `medium` / `low` (default `medium`) |

Notes:
- `speak` must be **English** — it is read aloud as-is, never translated.
- `time` must be absolute; convert "in 20 minutes" to a real clock time yourself.
- A file that isn't valid JSON or is missing `content`/`time` is left in place and
  retried next cycle; it is never half-consumed.
