"""In-memory, per-user progress tracker for the Market Scanner - lets the dashboard
show what's actually happening during a scan (which chunk is being swept, which
symbol is being scored, how many qualifying candidates found so far) instead of a
scan looking like a frozen button press, and keeps the last completed scan's
results around so the Scanner view has something to show between runs.

Deliberately in-memory and unlocked at the dict-entry level (not persisted, not
process-safe) - a single-process app, and a scan in progress when the process
restarts just starts fresh, which is fine for a "what's happening right now" view.
"""

import time

_status: dict[int, dict] = {}


def _get(user_id: int) -> dict:
    return _status.setdefault(
        user_id,
        {
            "scanning": False,
            "phase": "idle",
            "detail": "",
            "progress_done": 0,
            "progress_total": 0,
            "found": 0,
            "started_at": None,
            "finished_at": None,
            "results": [],
            "scanned_count": 0,
            "swept_count": 0,
        },
    )


def start(user_id: int, phase: str) -> None:
    status = _get(user_id)
    status.update(scanning=True, phase=phase, detail="", progress_done=0, progress_total=0, found=0, started_at=time.time())


def update(user_id: int, *, phase: str | None = None, detail: str = "", done: int = 0, total: int = 0, found: int = 0) -> None:
    status = _get(user_id)
    if phase is not None:
        status["phase"] = phase
    status["detail"] = detail
    status["progress_done"] = done
    status["progress_total"] = total
    status["found"] = found


def finish(user_id: int, results: list, scanned_count: int, swept_count: int = 0) -> None:
    status = _get(user_id)
    status.update(
        scanning=False,
        phase="idle",
        detail="",
        finished_at=time.time(),
        results=results,
        scanned_count=scanned_count,
        swept_count=swept_count,
    )


def fail(user_id: int, message: str) -> None:
    status = _get(user_id)
    status.update(scanning=False, phase="idle", detail=message, finished_at=time.time())


def snapshot(user_id: int) -> dict:
    return dict(_get(user_id))
