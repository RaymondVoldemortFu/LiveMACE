from __future__ import annotations

import logging
from unittest.mock import patch

from config.logging_config import ErrorEmailHandler, _parse_recipients


def test_parse_recipients_supports_comma_separated_values():
    parsed = _parse_recipients(" a@test.com, b@test.com ,, c@test.com ")
    assert parsed == ["a@test.com", "b@test.com", "c@test.com"]


@patch("config.logging_config.smtplib.SMTP_SSL")
def test_error_email_handler_sends_email_on_error(mock_smtp_ssl):
    handler = ErrorEmailHandler(
        smtp_server="smtp.126.com",
        smtp_port=465,
        from_addr="agent_benchmark@126.com",
        password="auth-code",
        recipients=["alert@example.com"],
    )
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))

    logger = logging.getLogger("unit-test-email-alert")
    logger.setLevel(logging.ERROR)
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(handler)

    logger.error("critical failure")

    assert mock_smtp_ssl.call_count == 1
    smtp_client = mock_smtp_ssl.return_value.__enter__.return_value
    smtp_client.login.assert_called_once_with("agent_benchmark@126.com", "auth-code")
    smtp_client.send_message.assert_called_once()
