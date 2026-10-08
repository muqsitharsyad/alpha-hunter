from __future__ import annotations

from types import SimpleNamespace

from app import notify as notify_module


class _Response:
    def raise_for_status(self) -> None:
        return None


def test_notifier_is_noop_without_telegram_configuration(monkeypatch):
    monkeypatch.setattr(
        notify_module,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="", telegram_chat_id="", telegram_topic_id=0),
    )
    notifier = notify_module.Notifier()
    called = False

    def unexpected_post(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled notifier must not make an HTTP request")

    monkeypatch.setattr(notify_module.httpx, "post", unexpected_post)

    assert notifier.notify("stale_feed", "provider unavailable", force=True) is False
    assert called is False


def test_notifier_sends_configured_topic_and_returns_delivery(monkeypatch):
    monkeypatch.setattr(
        notify_module,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_bot_token="test-token",
            telegram_chat_id="-100123",
            telegram_topic_id=42,
        ),
    )
    notifier = notify_module.Notifier()
    captured: dict[str, object] = {}

    def post(url, *, json, timeout):
        captured.update(url=url, payload=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr(notify_module.httpx, "post", post)

    assert notifier.notify("stale_feed", "provider unavailable", force=True) is True
    assert captured == {
        "url": "https://api.telegram.org/bottest-token/sendMessage",
        "payload": {
            "chat_id": "-100123",
            "text": "[stale_feed] provider unavailable",
            "disable_web_page_preview": True,
            "message_thread_id": 42,
        },
        "timeout": 10.0,
    }
