"""Telegram delivery must never write the bot token to the log."""

from __future__ import annotations

import httpx

from remote_jobs_digest import notify


def test_telegram_error_does_not_log_the_token(monkeypatch, capsys):
    token = "123456:SECRET-token-value"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    real_client = httpx.Client
    monkeypatch.setattr(notify.httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(lambda req: httpx.Response(401)), **kw))

    assert notify.send_telegram("hi") is False
    out = capsys.readouterr()
    assert "Telegram failed" in out.out + out.err
    assert token not in out.out + out.err
