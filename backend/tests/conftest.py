import pytest
from fastapi.testclient import TestClient

from duocompanion.app import create_app
from duocompanion.config import Settings

SECRET = "test-secret-0123456789"


@pytest.fixture
def settings(tmp_path):
    return Settings(ingest_secret=SECRET, db_path=tmp_path / "test.db")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def auth():
    return {"X-Ingest-Secret": SECRET}
