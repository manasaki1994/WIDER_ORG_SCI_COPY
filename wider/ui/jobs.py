"""Background runs for the UI: a long stage (screening, embedding, enrichment, a proposal) runs in its own thread with
its own connection to the store, so the page stays usable, other pages can be opened and the run continues when
the browser tab closes. Progress, ETA and the cost so far are read from the ``jobs`` table and the usage log.

A job that was running when the server stopped is shown as ``interrupted``; screening, embedding and enrichment resume
where they stopped when started again, because finished items are skipped.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from ..core.estimate import calls_left, projection
from ..core.project import Project
from ..core.provenance import utcnow
from ..core.usage import JOB_ID, usage_for_job

ACTIVE = ("running",)


class JobCancelled(Exception):
    """Raised from a progress callback when the researcher cancelled the run."""


def _age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


class JobRunner:
    def __init__(self):
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: set[str] = set()
        self._tasks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ start / cancel
    def start(self, root, kind: str, stage: str, label: str, params: dict, fn) -> str:
        """``fn(project, progress)`` (sync or async) returns the result dict stored with the job."""
        job_id = f"{kind}-{uuid.uuid4().hex[:8]}"
        p = Project.load(root)
        try:
            busy = p.store.one("SELECT job_id, label FROM jobs WHERE stage=? AND status='running'", (stage,))
            if busy and busy["job_id"] in self._threads and self._threads[busy["job_id"]].is_alive():
                raise RuntimeError(f"'{busy['label']}' is still running on this stage; wait for it or cancel it first")
            now = utcnow()
            p.store.execute("INSERT INTO jobs(job_id,kind,stage,label,params,status,n_done,n_total,n_start,phase_at,started_at,updated_at) "
                            "VALUES(?,?,?,?,?,'running',0,NULL,0,?,?,?)", (job_id, kind, stage, label, json.dumps(params), now, now, now))
        finally:
            p.close()
        ctx = contextvars.copy_context()  # the job keeps the page's reviewer name (journal.AUTHOR), not the server's login
        t = threading.Thread(target=ctx.run, args=(self._run, root, job_id, fn), name=f"wider-job-{job_id}", daemon=True)
        with self._lock:
            self._threads[job_id] = t
        t.start()
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            alive = job_id in self._threads and self._threads[job_id].is_alive()
            if not alive:
                return False
            self._cancel.add(job_id)
            lt = self._tasks.get(job_id)
        if lt:
            loop, task = lt
            loop.call_soon_threadsafe(task.cancel)
        return True

    def is_alive(self, job_id: str) -> bool:
        t = self._threads.get(job_id)
        return bool(t and t.is_alive())

    # ------------------------------------------------------------------ the thread
    def _run(self, root, job_id: str, fn) -> None:
        JOB_ID.set(job_id)
        p = Project.load(root)
        st = p.store
        state = {"last_write": 0.0, "n_done": 0, "n_total": None, "n_start": 0}

        def progress(n_done: int, n_total: int | None = None, *rest: Any) -> None:
            if job_id in self._cancel:
                raise JobCancelled()
            now_m = time.monotonic()
            new_phase = (n_total != state["n_total"]) or (n_done < state["n_done"])
            state["n_done"], state["n_total"] = n_done, n_total
            msg = " · ".join(str(x) for x in rest if x not in (None, ""))[:200] or None
            if new_phase:   # a new pass (protocol runs) restarts the rate used for the ETA
                state["n_start"] = max(n_done - 1, 0)
                st.execute("UPDATE jobs SET n_start=?, phase_at=? WHERE job_id=?", (state["n_start"], utcnow(), job_id))
            if new_phase or now_m - state["last_write"] > 0.5 or (n_total and n_done >= n_total):
                state["last_write"] = now_m
                st.execute("UPDATE jobs SET n_done=?, n_total=?, message=?, updated_at=? WHERE job_id=?",
                           (n_done, n_total, msg, utcnow(), job_id))

        status, result, error = "done", None, None
        try:
            if inspect.iscoroutinefunction(fn):
                loop = asyncio.new_event_loop()
                try:
                    task = loop.create_task(fn(p, progress))
                    with self._lock:
                        self._tasks[job_id] = (loop, task)
                    result = loop.run_until_complete(task)
                finally:
                    with self._lock:
                        self._tasks.pop(job_id, None)
                    loop.close()
            else:
                result = fn(p, progress)
        except (JobCancelled, asyncio.CancelledError):
            status, error = "cancelled", "cancelled by the researcher; start it again to resume"
        except Exception as e:  # report on the page, never kill the server
            # ``job_message``: a fixed sentence the page shows as is, for runs whose exception text could hold what
            # the researcher typed (Ask WIDER, guide-assistant D15)
            status, error = "failed", (getattr(e, "job_message", None) or f"{type(e).__name__}: {e}")[:800]
        try:
            st.execute("UPDATE jobs SET status=?, result=?, error=?, finished_at=?, updated_at=?, n_done=COALESCE(?, n_done) WHERE job_id=?",
                       (status, json.dumps(result, default=str) if result is not None else None, error, utcnow(), utcnow(),
                        state["n_done"] or None, job_id))
        finally:
            p.close()
            with self._lock:
                self._cancel.discard(job_id)

    # ------------------------------------------------------------------ reading
    def describe(self, p: Project, row) -> dict[str, Any]:
        d = dict(row)
        if d["status"] == "running" and not self.is_alive(d["job_id"]):
            # the server that ran it has stopped (or crashed): it will not finish on its own
            p.store.execute("UPDATE jobs SET status='interrupted', finished_at=? WHERE job_id=? AND status='running'", (utcnow(), d["job_id"]))
            # re-read: a thread that wrote its own end (done, cancelled, failed) just after the row was read is not interrupted
            d = dict(p.store.one("SELECT * FROM jobs WHERE job_id=?", (d["job_id"],)))
        d["params"] = json.loads(d["params"] or "{}")
        d["result"] = json.loads(d["result"]) if d.get("result") else None
        d["elapsed_s"] = _age_s(d["started_at"]) if d["status"] == "running" else None
        eta = None
        if d["status"] == "running" and d["n_total"]:
            dt = _age_s(d.get("phase_at")) or 0
            moved = (d["n_done"] or 0) - (d["n_start"] or 0)
            if moved > 0 and dt > 0:
                eta = max(0.0, (d["n_total"] - d["n_done"]) / (moved / dt))
        d["eta_s"] = round(eta) if eta is not None else None
        d["usage"] = usage_for_job(p, d["job_id"])
        if d["status"] == "running":   # cost so far and the projected range (the CLI progress lines print the same)
            pre = d["params"].get("estimate") if isinstance(d["params"], dict) else None
            proj = projection(d["usage"], calls_left(d["n_done"], d["n_total"], d["usage"], pre), pre)
            if proj:
                d["usage"].update(proj)
        return d

    def list(self, p: Project, active_only: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs" + (" WHERE status='running'" if active_only else "") + " ORDER BY started_at DESC LIMIT ?"
        return [self.describe(p, r) for r in p.store.query(sql, (limit,))]

    def get(self, p: Project, job_id: str) -> dict[str, Any] | None:
        r = p.store.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        return self.describe(p, r) if r else None
