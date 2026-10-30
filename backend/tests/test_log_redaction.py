"""Secrets must not reach a log sink (item 1 of the WhatsApp debugging round).

The leak that started this: the `httpx` logger prints the full request URL at INFO, and the connection test
called

    GET https://graph.facebook.com/<ver>/debug_token?input_token=<USER TOKEN>&access_token=<APP ID>|<APP SECRET>

so one INFO line carried both the user's access token and the app secret — into a console, a pasted log, a
screenshot. Two independent defences are asserted here:

* the redaction processor, which masks by parameter name and after `Bearer `, whatever the call site;
* the level change, so `httpx`/`httpcore` never print a URL in the first place.
"""

from __future__ import annotations

import logging

import pytest
import structlog
from app.core.logging import NOISY_HTTP_LOGGERS, redact, setup_logging

USER_TOKEN = "EAAG-super-secret-user-token-123"
APP_SECRET = "0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c"
APP_ID = "1031129959988895"


def test_every_named_query_parameter_is_masked() -> None:
    url = (
        f"https://graph.facebook.com/v25.0/debug_token?input_token={USER_TOKEN}"
        f"&access_token={APP_ID}|{APP_SECRET}&client_secret={APP_SECRET}&token={USER_TOKEN}&app_secret={APP_SECRET}"
    )
    masked = redact(url)
    for secret in (USER_TOKEN, APP_SECRET):
        assert secret not in masked
    assert masked.count("***") >= 5
    # the parameter names stay: a log line has to remain diagnosable
    for name in ("input_token", "access_token", "client_secret", "app_secret"):
        assert name in masked


def test_a_bearer_header_is_masked_but_keeps_the_scheme() -> None:
    masked = redact(f"Authorization: Bearer {USER_TOKEN}")
    assert USER_TOKEN not in masked
    assert masked == "Authorization: Bearer ***"


def test_ordinary_text_is_left_alone() -> None:
    assert redact("GET /api/v1/leads 200 in 12ms") == "GET /api/v1/leads 200 in 12ms"
    # a word that merely starts like a parameter must not be swallowed
    assert "tokens" in redact("tokens issued: 3")


def test_logging_a_url_containing_secrets_emits_no_secret(capsys: pytest.CaptureFixture[str]) -> None:
    """The end-to-end assertion: configure the pipeline, log the URL, read what was actually written."""
    previous = structlog.get_config()
    try:
        setup_logging(level="INFO", json_logs=True)
        log = structlog.get_logger("test")
        log.info(
            "http",
            url=f"https://graph.facebook.com/v25.0/debug_token?input_token={USER_TOKEN}&access_token={APP_ID}|{APP_SECRET}",
            authorization=f"Bearer {USER_TOKEN}",
            nested=[f"?app_secret={APP_SECRET}"],
        )
        written = capsys.readouterr().err
    finally:
        structlog.configure(**previous)

    assert written, "the test proves nothing if nothing was written"
    assert USER_TOKEN not in written, "the user access token reached the log"
    assert APP_SECRET not in written, "the app secret reached the log"
    assert "***" in written
    assert "debug_token" in written, "the line must still say what happened"


def test_httpx_and_httpcore_are_silenced_below_warning() -> None:
    previous = structlog.get_config()
    try:
        setup_logging()
    finally:
        structlog.configure(**previous)
    for name in NOISY_HTTP_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING, f"{name} still prints every request URL"
    assert set(NOISY_HTTP_LOGGERS) == {"httpx", "httpcore"}


def test_uvicorn_access_log_masks_the_query_string() -> None:
    """The access log is a third sink, and the only one structlog cannot reach.

    Found by the live verification run: `INFO: … "GET /events?access_token=EAAG-… HTTP/1.1" 200` was
    printed verbatim while every application line was redacted. uvicorn formats its own record from
    `(client, method, full_path, version, status)` and writes it straight to stderr.
    """
    import logging

    from app.core.logging import RedactAccessLog, redact

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:5000", "GET", "/api/v1/events?access_token=EAAG-abc123&provider=whatsapp", "1.1", 200),
        exc_info=None,
    )
    assert RedactAccessLog().filter(record) is True
    rendered = record.getMessage()
    assert "EAAG-abc123" not in rendered
    assert "access_token=***" in rendered
    # the parts that make the line useful must survive
    assert "127.0.0.1:5000" in rendered and "GET" in rendered and "200" in rendered and "provider=whatsapp" in rendered
    assert redact("input_token=xyz") == "input_token=***"
