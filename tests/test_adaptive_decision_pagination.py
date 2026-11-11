import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app import web_routes, web_queries


class DecisionPaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes = {r.path: r.endpoint for r in web_routes.create_auth_router(None, None).routes}
        self.page = routes['/api/controls/decisions']
        self.export = routes['/api/controls/decisions/export']
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.rows = [self.row(i) for i in range(123)]
        # Journal v2 not installed: these tests cover the Journal v1 read path.
        _v1 = patch.object(web_queries, 'journal_v2_status', new=AsyncMock(return_value={'mode': 'v1', 'v2_installed': False}))
        _v1.start(); self.addCleanup(_v1.stop)

    def row(self, i):
        stamp = self.now - timedelta(seconds=i // 2)
        entry = {'evaluation_id': f'id-{i:05d}', 'evaluated_at': stamp.isoformat(),
                 'decision': 'HOLD', 'hardware_write_attempted': False,
                 'reason_codes': ['SYNTHETIC'], 'configuration_secret': 'never-export'}
        return {'evaluation_id': f'id-{i:05d}', 'evaluated_at': stamp, 'entry': json.dumps(entry)}

    def query_fakes(self):
        async def latest(_url):
            return max(self.rows, key=lambda r:(r['evaluated_at'],r['evaluation_id'])) if self.rows else None
        async def page(_url, lower, upper, limit, after_time=None, after_id=None):
            rows=sorted(self.rows,key=lambda r:(r['evaluated_at'],r['evaluation_id']),reverse=True)
            rows=[r for r in rows if r['evaluated_at'] >= lower and r['evaluated_at'] <= upper]
            if after_time:
                after=((after_time if isinstance(after_time,datetime) else datetime.fromisoformat(after_time)),after_id)
                rows=[r for r in rows if (r['evaluated_at'],r['evaluation_id'])<after]
            return rows[:limit]
        return latest,page

    async def test_pages_are_bounded_stable_and_include_new_records_once(self):
        latest, page = self.query_fakes()
        with patch.object(web_routes,'_check_auth',return_value=(True,'u')), patch.dict('os.environ',{'DATABASE_URL':'synthetic','WEB_AUTH_SESSION_SECRET':'a'*64}), \
             patch.object(web_queries,'latest_decision',side_effect=latest), patch.object(web_queries,'decision_journal',side_effect=page):
            response=await self.page(object())
            first=json.loads(response.body)
            self.assertEqual(len(first['decisions']),50, response.body.decode())
            self.assertTrue(first['has_more'])
            original_ids=[r['evaluation_id'] for r in self.rows]
            incoming=self.row(9999)
            incoming['evaluated_at']=self.now+timedelta(seconds=30)
            entry=json.loads(incoming['entry']); entry['evaluated_at']=incoming['evaluated_at'].isoformat()
            incoming['entry']=json.dumps(entry)
            self.rows.append(incoming)
            token=first['next_cursor']; seen=[e['evaluation_id'] for e in first['decisions']]
            while token:
                response=await self.page(object(),cursor=token)
                body=json.loads(response.body)
                seen.extend(e['evaluation_id'] for e in body['decisions'])
                token=body['next_cursor']
            self.assertEqual(len(seen),123)
            self.assertEqual(len(set(seen)),123)
            self.assertEqual(set(seen),set(original_ids))
            self.assertEqual(json.loads(response.body)['latest_decision']['evaluation_id'], incoming['evaluation_id'])

    async def test_boundaries_stay_fixed_and_backdated_inserts_are_best_effort(self):
        latest, page = self.query_fakes()
        bounds = []
        async def capture(url, lower, upper, limit, after_time=None, after_id=None):
            bounds.append((lower, upper))
            return await page(url, lower, upper, limit, after_time, after_id)
        with patch.object(web_routes, '_check_auth', return_value=(True, 'u')), \
             patch.dict('os.environ', {'DATABASE_URL': 'synthetic', 'WEB_AUTH_SESSION_SECRET': 'a'*64}), \
             patch.object(web_queries, 'latest_decision', side_effect=latest), \
             patch.object(web_queries, 'decision_journal', side_effect=capture):
            first = json.loads((await self.page(object())).body)
            inserted = self.row(500)
            self.rows.append(inserted)
            second = json.loads((await self.page(object(), cursor=first['next_cursor'])).body)
            self.assertEqual(bounds[0], bounds[1])
            self.assertEqual(bounds[0][1] - bounds[0][0], timedelta(days=1))
            token = second['next_cursor']
            seen = [x['evaluation_id'] for x in second['decisions']]
            while token:
                result = json.loads((await self.page(object(), cursor=token)).body)
                seen.extend(x['evaluation_id'] for x in result['decisions'])
                token = result['next_cursor']
            self.assertIn(inserted['evaluation_id'], seen)

    async def test_default_limit_max_validation_and_authentication(self):
        with patch.object(web_routes,'_check_auth',return_value=(False,None)):
            self.assertEqual((await self.page(object())).status_code,401)
            self.assertEqual((await self.export(object())).status_code,401)
        with patch.object(web_routes,'_check_auth',return_value=(True,'u')):
            self.assertEqual((await self.page(object(),limit=201)).status_code,422)
            self.assertEqual((await self.page(object(),cursor='junk')).status_code,422)

    async def test_database_failure_is_503_and_empty_page_is_success(self):
        latest, page = self.query_fakes()
        with patch.object(web_routes,'_check_auth',return_value=(True,'u')), \
             patch.dict('os.environ',{'DATABASE_URL':'synthetic','WEB_AUTH_SESSION_SECRET':'a'*64}), \
             patch.object(web_queries,'latest_decision',side_effect=latest), \
             patch.object(web_queries,'decision_journal',side_effect=RuntimeError('database unavailable')):
            failed=await self.page(object())
        self.assertEqual(failed.status_code,503)
        with patch.object(web_routes,'_check_auth',return_value=(True,'u')), \
             patch.dict('os.environ',{'DATABASE_URL':'synthetic','WEB_AUTH_SESSION_SECRET':'a'*64}), \
             patch.object(web_queries,'latest_decision',new=AsyncMock(return_value=None)), \
             patch.object(web_queries,'decision_journal',new=AsyncMock(return_value=[])):
            empty=await self.page(object())
        self.assertEqual(empty.status_code,200)
        payload=json.loads(empty.body)
        self.assertEqual(payload['decisions'],[])
        self.assertFalse(payload['has_more'])

    async def test_signed_cursor_rejects_tampering_expiry_and_range_mismatch(self):
        secret='s'*64
        payload={'v':1,'exp':int(__import__('time').time())+300,'days':1,
                 'lower':self.now.isoformat(),'upper':(self.now+timedelta(days=1)).isoformat(),
                 'after_time':self.now.isoformat(),'after_id':'row-1'}
        token=web_routes._encode_decision_cursor(payload,secret)
        self.assertEqual(web_routes._decode_decision_cursor(token,secret,1)['days'],1)
        with self.assertRaises(ValueError): web_routes._decode_decision_cursor(token+'x',secret,1)
        with self.assertRaises(ValueError): web_routes._decode_decision_cursor(token,secret,2)
        expired=dict(payload,exp=int(__import__('time').time())-1)
        with self.assertRaises(ValueError): web_routes._decode_decision_cursor(web_routes._encode_decision_cursor(expired,secret),secret,1)

    async def test_streaming_export_over_ten_thousand_is_complete_and_sanitized(self):
        async def stream(_url, _days):
            for i in range(10037):
                yield {'entry':json.dumps({'evaluation_id':str(i),'decision':'HOLD','hardware_write_attempted':False,
                                           'reason_codes':['SYNTHETIC'],'configuration_secret':'never-export',
                                           'decision_diagnostics':{'sample_counts':{'analysed_observations':3},'db_password':'hidden'}})}
        with patch.object(web_routes,'_check_auth',return_value=(True,'u')), patch.dict('os.environ',{'DATABASE_URL':'synthetic','WEB_AUTH_SESSION_SECRET':'a'*64}), \
             patch.object(web_queries,'stream_decisions',side_effect=stream):
            response=await self.export(object(),days=7)
            content=b''.join([chunk async for chunk in response.body_iterator])
        payload=json.loads(content)
        self.assertEqual(len(payload['decisions']),10037)
        self.assertNotIn(b'never-export',content)
        self.assertNotIn(b'db_password',content)
        self.assertIn(b'analysed_observations',content)
        self.assertEqual(payload['decisions'][10036]['evaluation_id'],'10036')

    async def test_interrupted_export_closes_nested_async_iterator(self):
        closed=[]
        async def stream(_url,_days):
            try:
                yield {'entry':json.dumps({'decision':'HOLD'})}
                await __import__('asyncio').Event().wait()
            finally:
                closed.append(True)
        with patch.object(web_routes,'_check_auth',return_value=(True,'u')), patch.dict('os.environ',{'DATABASE_URL':'synthetic'}), \
             patch.object(web_queries,'stream_decisions',side_effect=stream):
            response=await self.export(object())
            iterator=response.body_iterator
            await iterator.__anext__()
            await iterator.__anext__()
            await iterator.aclose()
        self.assertEqual(closed,[True])

@unittest.skipUnless(__import__('os').environ.get('DECISION_PAGINATION_LOCAL_POSTGRES') == '1', 'requires disposable pagination PostgreSQL')
class DecisionPaginationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_cursor_export_over_ten_thousand(self):
        import pg_isolation
        dsn=pg_isolation.configured_dsn()
        conn=await pg_isolation.connect(dsn)
        try:
            await conn.execute('DROP TABLE IF EXISTS adaptive_decision_journal')
            await conn.execute('''CREATE TABLE adaptive_decision_journal (
                evaluation_id text PRIMARY KEY, evaluated_at timestamptz NOT NULL, entry jsonb NOT NULL)''')
            await conn.execute('''INSERT INTO adaptive_decision_journal
                SELECT 'eval-' || lpad(n::text,5,'0'), now() - n * interval '1 second',
                       jsonb_build_object('evaluation_id','eval-' || lpad(n::text,5,'0'),
                         'evaluated_at',(now() - n * interval '1 second')::text,
                         'decision','HOLD','hardware_write_attempted',false,'reason_codes',jsonb_build_array('synthetic'),
                         'configuration_secret','hidden')
                FROM generate_series(0,10036) n''')
        finally:
            await conn.close()
        routes={r.path:r.endpoint for r in web_routes.create_auth_router(None,None).routes}
        page=routes['/api/controls/decisions']; export=routes['/api/controls/decisions/export']
        seen=[]; cursor=None
        with patch.object(web_routes,'_check_auth',return_value=(True,'tester')), pg_isolation.app_connections(), patch.dict('os.environ',{'WEB_AUTH_SESSION_SECRET':'a'*64}):
            while True:
                response=await page(object(),cursor=cursor)
                data=json.loads(response.body)
                self.assertEqual(response.status_code,200)
                if cursor is None: self.assertLessEqual(len(data['decisions']),50)
                seen.extend(x['evaluation_id'] for x in data['decisions'])
                if 'has_more' not in data: self.fail(response.body.decode())
                if not data['has_more']: break
                cursor=data['next_cursor']
            streamed=await export(object(),days=7)
            content=b''.join([chunk async for chunk in streamed.body_iterator])
        exported=json.loads(content)['decisions']
        self.assertEqual(len(seen),10037)
        self.assertEqual(len(set(seen)),10037)
        self.assertEqual(len(exported),10037)
        self.assertNotIn(b'configuration_secret',content)
