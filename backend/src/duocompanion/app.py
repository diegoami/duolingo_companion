import hashlib
import hmac
import json
import re
from contextlib import closing

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

from . import __version__, db
from .config import Settings

INGEST_KINDS = ("discovery", "session", "progress")
MAX_CLIENT_ID_LENGTH = 128


def _optional_str(envelope: dict, key: str) -> str | None:
    value = envelope.get(key)
    return value if isinstance(value, str) else None


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Duolingo companion", version=__version__)

    with closing(db.connect(settings.db_path)) as conn:
        db.migrate(conn)

    def require_secret(x_ingest_secret: str | None = Header(default=None)) -> None:
        if x_ingest_secret is None or not hmac.compare_digest(
            x_ingest_secret.encode(), settings.ingest_secret.encode()
        ):
            raise HTTPException(status_code=401, detail="bad or missing X-Ingest-Secret")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/userscript/{name}")
    def userscript(name: str) -> FileResponse:
        # Lets Violentmonkey on the phone install (and update) the script from this server.
        path = settings.userscript_dir / name
        if not re.fullmatch(r"[\w-]+\.user\.js", name) or not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="text/javascript; charset=utf-8")

    @app.post("/ingest/{kind}", dependencies=[Depends(require_secret)])
    async def ingest(kind: str, request: Request) -> dict:
        if kind not in INGEST_KINDS:
            raise HTTPException(status_code=404, detail=f"unknown kind; use one of {INGEST_KINDS}")
        raw = await request.body()
        try:
            text = raw.decode("utf-8")
            envelope = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"body is not UTF-8 JSON: {exc}") from exc
        if not isinstance(envelope, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")

        client_id = envelope.get("client_id")
        if not isinstance(client_id, str) or not 0 < len(client_id) <= MAX_CLIENT_ID_LENGTH:
            # Content hash still makes retries of the same payload idempotent.
            client_id = "sha256:" + hashlib.sha256(raw).hexdigest()

        # Stored verbatim: parsing happens later and can be redone from this text.
        with closing(db.connect(settings.db_path)) as conn:
            row_id, created = db.store_raw(
                conn,
                client_id=client_id,
                kind=kind,
                body=text,
                captured_at=_optional_str(envelope, "captured_at"),
                source_url=_optional_str(envelope, "page_url"),
                script_version=_optional_str(envelope, "script_version"),
            )
        return {"id": row_id, "status": "stored" if created else "duplicate"}

    return app
