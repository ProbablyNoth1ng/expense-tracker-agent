import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from expense_agent.locking import ProcessLock
from expense_agent.monobank import MonobankClient


class MonobankRetryTests(unittest.TestCase):
    def test_429_is_retried_with_server_delay(self):
        transport = Mock()
        transport.get.side_effect = [
            Mock(status_code=429, headers={"Retry-After": "2"}),
            Mock(status_code=200, json=lambda: {"accounts": []}),
        ]
        sleeper = Mock()
        client = MonobankClient(
            token="secret", transport=transport, sleeper=sleeper, minimum_interval=0, max_attempts=3
        )
        self.assertEqual(client.list_accounts(), [])
        sleeper.assert_any_call(2.0)


class ProcessLockTests(unittest.TestCase):
    def test_second_process_lock_is_rejected_and_release_allows_reentry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.lock"
            with ProcessLock(path):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with ProcessLock(path):
                        pass
            with ProcessLock(path):
                self.assertTrue(path.exists())
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
