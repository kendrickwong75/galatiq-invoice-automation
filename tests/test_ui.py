"""Optional web UI: uploads, rejection of unsupported files, and live stage tracking (skipped without Flask)."""

import io
import time

import pytest

pytest.importorskip("flask")

from invoice_agents.ui.server import STAGES, create_app  # noqa: E402

from .conftest import INVOICES  # noqa: E402


@pytest.fixture
def client(deps):
    return create_app(deps).test_client()


def upload(client, *names, extra=()):
    files = [(open(INVOICES / n, "rb"), n) for n in names] + list(extra)
    try:
        return client.post("/api/upload", data={"files": files}, content_type="multipart/form-data")
    finally:
        for f, _ in files:
            f.close()


def wait_done(client, count, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = client.get("/api/jobs").get_json()["jobs"]
        if len(jobs) >= count and all(j["status"] == "done" for j in jobs):
            return {j["file"]: j for j in jobs}
        time.sleep(0.1)
    raise AssertionError(f"jobs not finished: {jobs}")


def test_page_and_info(client):
    page = client.get("/")
    assert page.status_code == 200 and b"Drop invoices here" in page.data
    info = client.get("/api/info").get_json()
    assert info["provider"] == "mock" and ".pdf" in info["extensions"]


def test_upload_tracks_every_stage_and_rejects_unsupported(client):
    res = upload(client, "invoice_1001.txt", "invoice_1016.json",
                 extra=[(io.BytesIO(b"not an invoice"), "notes.docx")])
    body = res.get_json()
    assert [a["file"] for a in body["accepted"]] == ["invoice_1001.txt", "invoice_1016.json"]
    assert [r["file"] for r in body["rejected"]] == ["notes.docx"]

    jobs = wait_done(client, 2)
    paid, rejected = jobs["invoice_1001.txt"], jobs["invoice_1016.json"]
    assert paid["outcome"] == "APPROVED" and paid["payment_ref"].startswith("PAY-")
    assert rejected["outcome"] == "REJECTED" and "UNKNOWN_ITEM" in rejected["reason_codes"]
    for job in (paid, rejected):
        assert job["stages"] == {s: "done" for s in STAGES}
        assert job["rationale"]


def test_counters_show_self_correction_and_reflection(client):
    upload(client, "invoice_1012.pdf", "invoice_1013.json")
    jobs = wait_done(client, 2)
    assert jobs["invoice_1012.pdf"]["reads"] == 2  # mock misreads once, then corrects
    assert jobs["invoice_1013.json"]["critiques"] == 2  # over $10K: critic asks for one revision


def test_duplicates_are_processed_in_drop_order(client):
    upload(client, "invoice_1011.pdf", "invoice_1011.txt")
    jobs = wait_done(client, 2)
    assert jobs["invoice_1011.pdf"]["outcome"] == "APPROVED"
    assert jobs["invoice_1011.txt"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert "DUPLICATE" in jobs["invoice_1011.txt"]["reason_codes"]


def test_empty_upload_is_an_error(client):
    assert client.post("/api/upload", data={}, content_type="multipart/form-data").status_code == 400
