"""Collection telemetry distinguishes local deferral from upstream failure."""
from collections import deque
import time
from uuid import uuid4

import pytest
from app.db import session_scope
from app.models import ProviderCall
from app.providers.base import ProviderDeferred, record_call
from app.providers.free import _Http


def test_local_quota_deferral_does_not_report_provider_outage(monkeypatch):
    http = _Http()
    http._requests['geckoterminal'] = deque([time.monotonic()] * 8)
    calls = []
    monkeypatch.setattr('app.providers.free.record_call', lambda *args: calls.append(args))
    with pytest.raises(ProviderDeferred):
        http.get_json('geckoterminal', 'https://unused.invalid')
    assert calls == []


def test_real_call_health_is_persisted_without_credentials():
    provider = 'test-' + uuid4().hex[:12]
    record_call(provider, True, 12.5)
    with session_scope() as db:
        row = db.query(ProviderCall).filter_by(provider=provider).one()
        assert row.ok is True
        assert row.latency_ms == 12.5
        assert row.endpoint == ''
        assert row.error == ''
        db.delete(row)
