import datetime as dt
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from scheduler import movie_tick, refresh_due


class SchedulerTests(unittest.TestCase):
    def test_daily_boundary_and_restart(self):
        before = dt.datetime(2026, 10, 12, 6, 9, tzinfo=ZoneInfo('Asia/Shanghai'))
        config = dict(enabled=True, token_configured=True, last_success=before.timestamp())
        self.assertFalse(refresh_due(config, before))
        self.assertTrue(refresh_due(config, before + dt.timedelta(minutes=1)))
        config['last_success'] += 60
        self.assertFalse(refresh_due(config, before + dt.timedelta(hours=1)))

    def test_unconfigured_and_cold_start(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.assertFalse(refresh_due(dict(enabled=False, token_configured=False), now))
        self.assertFalse(refresh_due(dict(enabled=True, token_configured=False), now))
        self.assertTrue(refresh_due(dict(enabled=True, token_configured=True), now))

    def test_failure_remains_retryable(self):
        service = Mock()
        service.config.return_value = dict(enabled=True, token_configured=True)
        service.refresh.side_effect = RuntimeError('upstream failure')
        for _ in range(2):
            movie_tick(service, dt.datetime.now(dt.timezone.utc))
        self.assertEqual(service.refresh.call_count, 2)
