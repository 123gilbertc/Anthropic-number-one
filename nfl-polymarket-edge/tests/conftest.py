"""Test guard: no test may touch the network. Cached nflverse data and JSON fixtures only."""
import pytest
import requests


class _NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise _NetworkBlocked("network access is disabled in tests; use fixtures or the data cache")

    monkeypatch.setattr(requests.Session, "request", _blocked)
    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests, "post", _blocked)
    yield
