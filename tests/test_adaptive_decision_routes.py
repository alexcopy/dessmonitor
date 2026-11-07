import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    from app import web_routes
except ImportError:
    web_routes = None


@unittest.skipIf(web_routes is None, 'web dependencies unavailable')
class DecisionRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        router = web_routes.create_auth_router(None, None)
        self.api = next(route.endpoint for route in router.routes if route.path == '/api/controls/decisions')
        self.export = next(route.endpoint for route in router.routes if route.path == '/api/controls/decisions/export')

    async def test_authentication_is_required_before_database_access(self):
        with patch.object(web_routes, '_check_auth', return_value=(False, None)):
            response = await self.api(object(), days=1)
        self.assertEqual(response.status_code, 401)

    async def test_disabled_worker_without_journal_does_not_claim_a_success(self):
        with patch.object(web_routes, '_check_auth', return_value=(True, 'user')), \
             patch.dict('os.environ', {'ADAPTIVE_OUTPUT_LEARNING_ENABLED': 'false'}, clear=True):
            response = await self.api(object(), days=1)
        payload = json.loads(response.body)
        self.assertEqual(payload['worker_health']['status'], 'disabled')
        self.assertFalse(payload['worker_health']['enabled'])
        self.assertIsNone(payload['worker_health']['last_successful_evaluation_at'])
        self.assertEqual(payload['worker_health']['blocking_reason'], 'ADAPTIVE_OUTPUT_LEARNING_DISABLED')

    async def test_validated_range_and_read_only_export(self):
        entry = {'decision': 'INSUFFICIENT_DATA', 'hardware_write_attempted': False, 'token': 'secret',
                 'evidence_level': 'EXPLORATORY', 'reason_codes': ['DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED'],
                 'evaluated_at': '2026-11-04T10:00:00+00:00',
                 'current_settings': {'battery': None, 'token': 'secret'},
                 'worker_health': {'enabled': True, 'status': 'degraded',
                     'last_evaluation_at': '2026-11-04T10:00:00+00:00',
                     'last_successful_evaluation_at': '2026-11-04T10:00:00+00:00',
                     'last_error': None, 'blocking_reason': 'DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED',
                     'observation_count': 3, 'effective_window_seconds': 600}}
        conn = SimpleNamespace(fetch=AsyncMock(return_value=[{'entry': json.dumps(entry)}]), close=AsyncMock())
        asyncpg = SimpleNamespace(connect=AsyncMock(return_value=conn))
        latest = {'evaluation_id': 'id-1', 'evaluated_at': __import__('datetime').datetime.fromisoformat('2026-11-04T10:00:00+00:00'), 'entry': json.dumps(entry)}
        with patch.object(web_routes, '_check_auth', return_value=(True, 'user')), \
             patch.dict('os.environ', {'DATABASE_URL': 'synthetic', 'ADAPTIVE_OUTPUT_LEARNING_ENABLED': 'true', 'WEB_AUTH_SESSION_SECRET':'a'*64}), \
             patch.dict('sys.modules', {'asyncpg': asyncpg}), \
             patch('app.web_queries.latest_decision', new=AsyncMock(return_value=latest)), \
             patch('app.web_queries.decision_journal', new=AsyncMock(return_value=[latest])):
            invalid = await self.api(object(), days=3)
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(asyncpg.connect.await_count, 0)
            response = await self.api(object(), days=7)
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload['decisions'][0]['decision'], 'INSUFFICIENT_DATA')
        self.assertFalse(payload['has_more'])
        self.assertIsNone(payload['next_cursor'])
        self.assertEqual(payload['decisions'][0]['evidence_level'], 'EXPLORATORY')
        self.assertEqual(payload['decisions'][0]['reason_codes'], ['DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED'])
        self.assertEqual(payload['worker_health']['last_successful_evaluation_at'], '2026-11-04T10:00:00+00:00')
        self.assertEqual(payload['worker_health']['observation_count'], 3)
        self.assertEqual(payload['worker_health']['status_source'], 'PERSISTED_JOURNAL')
        self.assertNotIn('secret', response.body.decode())
        self.assertEqual(response.headers['cache-control'], 'no-store')
