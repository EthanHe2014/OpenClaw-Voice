#!/usr/bin/env python3
"""Spark task scheduler daemon.

Runs OpenMemo's reminder engine (ported into the `sparktasks` package) with
Spark's own data dir. APScheduler keeps jobs for pending tasks, watchdog and
reconcile jobs re-arm anything lost across restarts, and fired reminders are
spoken with Edge TTS + a macOS notification banner.

Run directly:   ./.venv/bin/python spark_tasks.py
Or via launchd: ai.openclaw.spark.tasks.plist
"""
import asyncio
import signal

from sparktasks.scheduler import start_scheduler, stop_scheduler


async def _main():
    start_scheduler()
    print("[spark-tasks] engine up — reminders will fire on schedule", flush=True)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await stop.wait()
    finally:
        stop_scheduler()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
