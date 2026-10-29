import json
import sqlite3


def envelope(client_id="c-1", **data):
    return {
        "client_id": client_id,
        "kind": "discovery",
        "captured_at": "2026-10-29T10:00:00Z",
        "page_url": "https://www.duolingo.com/lesson",
        "script_version": "0.1.0",
        "data": data or {"via": "fetch", "method": "GET", "url": "https://www.duolingo.com/x"},
    }


def test_health(client):
    assert client.get("/health").json()["ok"] is True


def test_rejects_missing_or_wrong_secret(client):
    assert client.post("/ingest/discovery", json=envelope()).status_code == 401
    bad = {"X-Ingest-Secret": "nope"}
    assert client.post("/ingest/discovery", json=envelope(), headers=bad).status_code == 401


def test_unknown_kind_is_404(client, auth):
    assert client.post("/ingest/whatever", json=envelope(), headers=auth).status_code == 404


def test_rejects_non_json(client, auth):
    resp = client.post("/ingest/discovery", content=b"not json", headers=auth)
    assert resp.status_code == 400
    resp = client.post("/ingest/discovery", json=[1, 2], headers=auth)
    assert resp.status_code == 400


def test_stores_body_verbatim(client, auth, settings):
    body = json.dumps(envelope(), indent=3)  # odd formatting must survive
    resp = client.post("/ingest/session", content=body.encode(), headers=auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "stored"
    row = sqlite3.connect(settings.db_path).execute(
        "SELECT kind, body, captured_at, source_url, script_version FROM raw_payloads"
    ).fetchone()
    assert row == ("session", body, "2026-10-29T10:00:00Z", "https://www.duolingo.com/lesson", "0.1.0")


def test_retry_with_same_client_id_is_duplicate(client, auth, settings):
    first = client.post("/ingest/discovery", json=envelope("same"), headers=auth).json()
    second = client.post("/ingest/discovery", json=envelope("same"), headers=auth).json()
    assert first["status"] == "stored"
    assert second == {"id": first["id"], "status": "duplicate"}
    count = sqlite3.connect(settings.db_path).execute("SELECT count(*) FROM raw_payloads").fetchone()[0]
    assert count == 1


def test_missing_client_id_falls_back_to_content_hash(client, auth):
    payload = {"data": {"anything": 1}}
    assert client.post("/ingest/progress", json=payload, headers=auth).json()["status"] == "stored"
    assert client.post("/ingest/progress", json=payload, headers=auth).json()["status"] == "duplicate"


def test_serves_userscript(client):
    resp = client.get("/userscript/discovery.user.js")
    assert resp.status_code == 200
    assert "// ==UserScript==" in resp.text
    assert client.get("/userscript/missing.user.js").status_code == 404
    assert client.get("/userscript/..%2Fpyproject.toml").status_code == 404
