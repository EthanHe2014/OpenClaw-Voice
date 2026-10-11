# BUILDER_LOG

Append-only. One line per builder run:
`<iso time> | <run id> | <item> | <ok|fail> | <commit sha> | <note>`

(seed entry below)
2026-10-11T16:50+08:00 | seed | framework+scaffolding | ok | - | roadmap, playbook, cron fleet created by Spark
2026-10-11T17:11+08:00 | builder-E | notes | ok | dfad133 | tools-only note(add/list/read) to dated file in data_dir; unit-tested + live-load check passed
