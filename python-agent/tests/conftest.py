from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from server import app


def pytest_sessionstart(session):  # pragma: no cover - pytest hook
    del session


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - pytest hook
    del session, exitstatus


def pytest_configure(config):  # pragma: no cover - pytest hook
    del config


import pytest


@pytest.fixture(autouse=True)
def internal_token(monkeypatch) -> str:
    token = "test-internal-token"
    monkeypatch.setattr(server.SETTINGS, "python_agent_internal_token", token)
    return token


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
