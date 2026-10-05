"""Local web UI server: a drop zone that uploads invoices, and a live tracker of each one's progress.

Jobs run one at a time, in upload order, on a single background thread. That keeps duplicate detection
deterministic (the ledger sees invoices in the order they were dropped) and lets the progress hook attribute
every node event to the job currently running.
"""

from __future__ import annotations

import copy
import logging
import queue
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from .. import config
from ..agents.state import Deps
from ..graph import build_graph, run_invoice
from ..models import InvoiceResult

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

STAGES = ("ingestion", "validation", "approval", "payment")
NODE_STAGE = {
    "load": "ingestion", "extract": "ingestion", "check_extraction": "ingestion", "extraction_failed": "ingestion",
    "validator_agent": "validation", "guard": "validation",
    "policy": "approval", "approve_draft": "approval", "critique": "approval", "finalize": "approval",
    "pay": "payment", "record_rejection": "payment", "enqueue_review": "payment",
}
NODE_ACTIVITY = {
    "load": "Opening the file", "extract": "Reading the invoice", "check_extraction": "Checking the read",
    "extraction_failed": "Could not read the invoice", "validator_agent": "Running validation checks",
    "guard": "Collecting findings", "policy": "Applying approval policy", "approve_draft": "Drafting the decision",
    "critique": "Reviewing the decision", "finalize": "Finalising the decision", "pay": "Sending payment",
    "record_rejection": "Recording the rejection", "enqueue_review": "Queuing for human review",
}


class JobTracker:
    """In-memory job list plus the single worker thread that processes it."""

    def __init__(self, deps: Deps):
        self.deps = deps
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._queue: queue.Queue[tuple[str, Path]] = queue.Queue()
        self._current: str | None = None
        self._graph = None
        deps.on_progress = self._on_progress
        threading.Thread(target=self._work, name="invoice-worker", daemon=True).start()

    def submit(self, path: Path) -> str:
        job_id = uuid.uuid4().hex[:10]
        job = {
            "id": job_id, "file": path.name, "status": "queued", "activity": "Waiting in line",
            "stages": {s: "pending" for s in STAGES}, "reads": 0, "critiques": 0,
            "vendor": None, "invoice_number": None, "outcome": None, "amount_usd": None, "currency": None,
            "reason_codes": [], "rationale": None, "payment_ref": None, "duration_ms": None, "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
        self._queue.put((job_id, path))
        return job_id

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(self._jobs[j]) for j in self._order]

    # --- worker -----------------------------------------------------------

    def _work(self) -> None:
        while True:
            job_id, path = self._queue.get()
            with self._lock:
                self._current = job_id
                self._jobs[job_id].update(status="processing", activity="Starting")
            try:
                if self._graph is None:
                    self._graph = build_graph(self.deps)
                self._finish(job_id, run_invoice(path, self.deps, self._graph))
            except Exception as exc:  # run_invoice never raises; this guards graph construction
                with self._lock:
                    self._jobs[job_id].update(status="done", outcome="NEEDS_HUMAN_REVIEW", error=repr(exc),
                                              activity="Processing error")
            finally:
                with self._lock:
                    self._current = None
                self._queue.task_done()

    def _on_progress(self, event: dict[str, Any]) -> None:
        node, phase = event.get("node"), event.get("phase")
        stage = NODE_STAGE.get(node)
        if stage is None:
            return
        with self._lock:
            job = self._jobs.get(self._current or "")
            if job is None:
                return
            stages = job["stages"]
            # Reaching a stage closes the ones before it: the one that was working is done, any never
            # reached (e.g. validation after an unreadable file) is skipped.
            for earlier in STAGES[:STAGES.index(stage)]:
                if stages[earlier] == "working":
                    stages[earlier] = "done"
                elif stages[earlier] == "pending":
                    stages[earlier] = "skipped"
            if phase == "start":
                if stages[stage] != "failed":
                    stages[stage] = "working"
                job["activity"] = NODE_ACTIVITY.get(node, node)
                if node == "extract":
                    job["reads"] += 1
                elif node == "critique":
                    job["critiques"] += 1
            elif phase == "error" or (node == "extraction_failed" and phase == "end"):
                stages[stage] = "failed"

    def _finish(self, job_id: str, result: InvoiceResult) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for stage in STAGES:
                if job["stages"][stage] == "working":
                    job["stages"][stage] = "failed" if result.error else "done"
                elif job["stages"][stage] == "pending":
                    job["stages"][stage] = "skipped"
            inv = result.invoice
            job.update(
                status="done", activity=None, outcome=result.outcome.value, amount_usd=result.amount_usd,
                currency=inv.currency if inv else None, vendor=inv.vendor if inv else None,
                invoice_number=inv.invoice_number if inv else None, reason_codes=result.reason_codes,
                rationale=result.rationale, duration_ms=result.duration_ms, error=result.error,
                payment_ref=result.payment.payment_ref if result.payment else None,
            )


def _safe_name(filename: str, used: set[str]) -> str:
    from werkzeug.utils import secure_filename

    name = secure_filename(Path(filename).name) or "upload"
    stem, ext = Path(name).stem, Path(name).suffix
    candidate, n = name, 2
    while candidate in used:
        candidate, n = f"{stem}-{n}{ext}", n + 1
    used.add(candidate)
    return candidate


def create_app(deps: Deps, upload_dir: str | Path | None = None):
    from flask import Flask, jsonify, request, send_from_directory

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    tracker = JobTracker(deps)
    app.extensions["invoice_tracker"] = tracker
    uploads = Path(upload_dir) if upload_dir else Path(deps.output_dir) / "ui_uploads"
    supported = sorted(config.SUPPORTED_EXTENSIONS)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/info")
    def info():
        return jsonify(provider=deps.provider, model=deps.model, extensions=supported)

    @app.post("/api/upload")
    def upload():
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if not files:
            return jsonify(error="No files received."), 400
        batch = uploads / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        accepted, rejected, used = [], [], set()
        for f in files:
            ext = Path(f.filename).suffix.lower()
            if ext not in config.SUPPORTED_EXTENSIONS:
                rejected.append({"file": Path(f.filename).name,
                                 "reason": f"Unsupported type {ext or '(no extension)'}; "
                                           f"use {', '.join(supported)}."})
                continue
            batch.mkdir(parents=True, exist_ok=True)
            path = batch / _safe_name(f.filename, used)
            f.save(path)
            accepted.append({"id": tracker.submit(path), "file": path.name})
        return jsonify(accepted=accepted, rejected=rejected)

    @app.get("/api/jobs")
    def jobs():
        return jsonify(jobs=tracker.snapshot())

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error=f"Upload too large: {MAX_UPLOAD_BYTES // (1024 * 1024)} MB at most per drop."), 413

    return app


def serve(deps: Deps, port: int = 8765, open_browser: bool = True) -> None:
    app = create_app(deps)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    url = f"http://127.0.0.1:{port}"
    print(f"Invoice UI running at {url}  (LLM: {deps.provider} / {deps.model}). Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)
