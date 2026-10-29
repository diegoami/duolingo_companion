import json
from contextlib import closing

from duocompanion import db, discovery


def test_normalize_path_replaces_ids():
    host, path = discovery.normalize_path(
        "https://www.duolingo.com/2017-06-30/users/123456789/courses/es/en?fields=a"
    )
    assert host == "www.duolingo.com"
    assert path == "/2017-06-30/users/{id}/courses/es/en"
    _, path = discovery.normalize_path("/2017-06-30/sessions/6a1f0c2e9b8d4e7fa1b2c3d4")
    assert path == "/2017-06-30/sessions/{id}"
    # Long words without digits are not ids.
    _, path = discovery.normalize_path("/api/learning-path-with-units-and-sections")
    assert path == "/api/learning-path-with-units-and-sections"


def test_scrub_removes_secrets_but_keeps_tokens_lists():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjEyMzQ1Nn0.abcdefghijk"
    data = {
        "email": "me@example.com",
        "tokens": ["el", "gato"],
        "nested": [{"accessToken": "x" * 20, "note": f"Bearer {jwt}"}],
        "streak": 12,
    }
    assert discovery.scrub(data) == {
        "email": "[REDACTED]",
        "tokens": ["el", "gato"],
        "nested": [{"accessToken": "[REDACTED]", "note": "Bearer [REDACTED]"}],
        "streak": 12,
    }


def _store(conn, client_id, data):
    db.store_raw(
        conn,
        client_id=client_id,
        kind="discovery",
        body=json.dumps({"client_id": client_id, "data": data}),
        captured_at=None,
        source_url=None,
        script_version=None,
    )


def test_summary_and_export(tmp_path):
    with closing(db.connect(tmp_path / "d.db")) as conn:
        db.migrate(conn)
        for i in range(2):
            _store(conn, f"s{i}", {
                "via": "fetch",
                "method": "POST",
                "url": f"https://www.duolingo.com/2017-06-30/sessions/abc{i}def0123456789abcd",
                "status": 200,
                "request_body": json.dumps({"type": "LESSON"}),
                "response_body": json.dumps({"id": "x", "challenges": [], "email": "me@example.com"}),
            })
        _store(conn, "dom", {"via": "dom", "url": "https://www.duolingo.com/lesson", "html": "<div/>"})

        summaries = discovery.summarize(discovery.iter_captures(conn))
        sessions = next(s for s in summaries if s.path == "/2017-06-30/sessions/{id}")
        assert sessions.count == 2
        assert sessions.response_keys == ["challenges", "email", "id"]
        assert sessions.request_keys == ["type"]
        assert "DOM" in discovery.format_summary(summaries)

        written = discovery.export_fixtures(conn, tmp_path / "fx", match="sessions")
    assert len(written) == 2
    fixture = json.loads(written[0].read_text())
    assert fixture["data"]["response_json"]["email"] == "[REDACTED]"
    assert fixture["data"]["request_json"] == {"type": "LESSON"}
    assert "response_body" not in fixture["data"]
