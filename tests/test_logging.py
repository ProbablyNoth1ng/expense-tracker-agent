import json
import tempfile
import unittest
from pathlib import Path

from expense_agent.logging_utils import configure_logging


class LoggingTests(unittest.TestCase):
    def test_json_log_redacts_configured_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = configure_logging(Path(tmp), secrets=["mono-secret", "openai-secret"])
            logger.info("tokens mono-secret and openai-secret")
            for handler in logger.handlers:
                handler.flush()
            line = (Path(tmp) / "expense-agent.log").read_text(encoding="utf-8").strip()
            payload = json.loads(line)
            self.assertNotIn("mono-secret", payload["message"])
            self.assertNotIn("openai-secret", payload["message"])
            self.assertIn("[REDACTED]", payload["message"])
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
