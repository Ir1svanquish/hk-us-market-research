import logging
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import requests

from src.log_redaction import redact_sensitive_text
from src.logging_config import RelativePathFormatter
from src.notification_sender.telegram_sender import TelegramSender


FAKE_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
FAKE_URL = f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"


class TelegramLogRedactionTest(unittest.TestCase):
    def setUp(self) -> None:
        config = SimpleNamespace(
            telegram_bot_token=FAKE_TOKEN,
            telegram_chat_id="123456",
            telegram_message_thread_id=None,
            telegram_max_length=4096,
        )
        self.sender = TelegramSender(config)

    def test_redaction_helper_removes_token_and_token_bearing_url(self) -> None:
        redacted = redact_sensitive_text(
            f"request failed for {FAKE_URL}; token={FAKE_TOKEN}",
            secrets=(FAKE_TOKEN,),
        )
        self.assertNotIn(FAKE_TOKEN, redacted)
        self.assertIn("[REDACTED]", redacted)

    @mock.patch("src.notification_sender.telegram_sender.time.sleep")
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_request_exception_logs_do_not_expose_bot_token(self, mock_post, _mock_sleep) -> None:
        mock_post.side_effect = requests.ConnectionError(f"connection failed for {FAKE_URL}")

        with self.assertLogs("src.notification_sender.telegram_sender", level="WARNING") as captured:
            result = self.sender._send_telegram_message(FAKE_URL, "123456", "hello")

        self.assertFalse(result)
        logged = "\n".join(captured.output)
        self.assertNotIn(FAKE_TOKEN, logged)
        self.assertIn("[REDACTED]", logged)

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_response_body_echo_does_not_expose_bot_token(self, mock_post) -> None:
        response = mock.Mock(status_code=400)
        response.json.return_value = {
            "ok": False,
            "description": f"bad request at {FAKE_URL}",
        }
        response.text = f"proxy echoed {FAKE_URL}"
        mock_post.return_value = response

        with self.assertLogs("src.notification_sender.telegram_sender", level="ERROR") as captured:
            result = self.sender._send_telegram_message(FAKE_URL, "123456", "hello")

        self.assertFalse(result)
        logged = "\n".join(captured.output)
        self.assertNotIn(FAKE_TOKEN, logged)
        self.assertIn("[REDACTED]", logged)

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_document_and_photo_exception_logs_do_not_expose_bot_token(self, mock_post) -> None:
        mock_post.side_effect = requests.ConnectionError(f"upload failed at {FAKE_URL}")

        with mock.patch("builtins.open", mock.mock_open(read_data=b"data")), self.assertLogs(
            "src.notification_sender.telegram_sender", level="ERROR"
        ) as captured:
            document_result = self.sender.send_telegram_document("report.pdf")
            photo_result = self.sender._send_telegram_photo(b"image")

        self.assertFalse(document_result)
        self.assertFalse(photo_result)
        logged = "\n".join(captured.output)
        self.assertNotIn(FAKE_TOKEN, logged)
        self.assertIn("[REDACTED]", logged)

    def test_production_formatter_redacts_message_and_exception_traceback(self) -> None:
        formatter = RelativePathFormatter("%(levelname)s %(message)s")
        try:
            raise RuntimeError(f"failed at {FAKE_URL}")
        except RuntimeError:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg=f"request failed for {FAKE_URL}",
                args=(),
                exc_info=sys.exc_info(),
            )
        formatted = formatter.format(record)
        self.assertNotIn(FAKE_TOKEN, formatted)
        self.assertIn("[REDACTED]", formatted)


if __name__ == "__main__":
    unittest.main()
