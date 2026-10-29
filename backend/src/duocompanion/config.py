import os
from dataclasses import dataclass
from pathlib import Path

MIN_SECRET_LENGTH = 16
# backend/src/duocompanion/config.py -> repo root /userscript
DEFAULT_USERSCRIPT_DIR = Path(__file__).resolve().parents[3] / "userscript"


@dataclass(frozen=True)
class Settings:
    ingest_secret: str
    db_path: Path
    userscript_dir: Path = DEFAULT_USERSCRIPT_DIR


def db_path_from_env() -> Path:
    return Path(os.environ.get("DUO_DB_PATH", "data/companion.db"))


def load_settings() -> Settings:
    secret = os.environ.get("DUO_INGEST_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise SystemExit(
            f"DUO_INGEST_SECRET must be set and at least {MIN_SECRET_LENGTH} characters "
            "(see backend/.env.example)"
        )
    return Settings(ingest_secret=secret, db_path=db_path_from_env())
