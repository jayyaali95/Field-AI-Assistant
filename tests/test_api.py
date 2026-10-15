from fastapi.testclient import TestClient

from copilot.api import app


def test_health(database):
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
