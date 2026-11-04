"""Offline tests only: synthetic values and mocked transport."""
import contextlib
import hashlib
import importlib
import io
import json
import logging
import socket
import sys
import unittest
import urllib.error
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from app.control.dess_control_discovery import (
    DiscoveryError, GuardError, _BoundedResponse, _ERROR_NAMES, _NoRedirect, _safe_urlopen, diagnostic_error, discover, main, readonly_client_class,
)

SECRETS = ('user-secret@example.invalid', 'password-SENTINEL', 'company-SENTINEL', 'PN-SENTINEL',
           'SN-SENTINEL', '1234567', 'token-SENTINEL', 'session-SENTINEL')


def config():
    return SimpleNamespace(email=SECRETS[0], password=SECRETS[1], company_key=SECRETS[2], pn=SECRETS[3],
                           sn=SECRETS[4], dev_code=SECRETS[5], dev_addr=17)


def catalogue():
    return {'err': 0, 'dat': {'field': [
        {'id': 'example_output_priority', 'name': 'Output Source Priority',
         'item': [{'key': '0', 'val': 'Utility first'}, {'key': '1', 'val': 'SBU'}]},
        {'id': 'example_back_to_utility', 'name': 'Back to utility voltage', 'unit': 'V'},
        {'id': 'example_back_to_battery', 'name': 'Back to battery voltage', 'unit': 'V'},
        {'id': 'example_charger_priority', 'name': 'Charger source priority'},
    ]}}


class FakeBase:
    calls = []
    response = None
    value_response = None
    error = None
    def __init__(self, cfg, logger):
        self.email, self.password, self.company_key = cfg.email, cfg.password, cfg.company_key
        self.pn, self.sn, self.dev_code, self.dev_addr = cfg.pn, cfg.sn, cfg.dev_code, cfg.dev_addr
        self.token, self.secret = SECRETS[6], SECRETS[7]
        self.logger = logger
    def should_refresh_token(self): return False
    def authenticate(self):
        return self._do_api_request({'action': 'authSource', 'usr': self.email, 'company-key': self.company_key}, need_auth=False)
    def refresh_token(self): return self._do_api_request({'action': 'updateToken'})
    def ctrl_device(self, field, value):
        return self._do_api_request({'action': 'ctrlDevice', 'id': field, 'val': value})
    def _do_api_request(self, params, **kwargs):
        self.calls.append((params, kwargs))
        if self.error is not None:
            raise self.error
        if params['action'] == 'queryDeviceCtrlField':
            return self.response if self.response is not None else catalogue()
        if params['action'] == 'queryDeviceCtrlValue':
            return self.value_response if self.value_response is not None else {'err': 0, 'dat': {'id': params['id'], 'val': '1'}}
        return {'err': 0, 'dat': {}}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        FakeBase.calls = []
        FakeBase.response = None
        FakeBase.value_response = None
        FakeBase.error = None
        self.client = readonly_client_class(SimpleNamespace(DessAPI=FakeBase))(config(), Mock())

    def test_catalogue_and_value_compose_documented_read_requests(self):
        result = discover(self.client, 'example_output_priority')
        self.assertEqual([f['candidate'] for f in result['catalogue']], ['output_priority', 'back_to_utility', 'back_to_battery', None])
        self.assertEqual(result['catalogue'][0]['enum'][1], {'key': '1', 'value': 'SBU'})
        self.assertEqual(result['reported_value']['value'], '1')
        self.assertIn('physical application is unverified', result['reported_value']['meaning'])
        requests = [item[0] for item in FakeBase.calls]
        self.assertEqual([p['action'] for p in requests], ['queryDeviceCtrlField', 'queryDeviceCtrlValue'])
        self.assertEqual(set(requests[0]), {'action', 'pn', 'devcode', 'devaddr', 'sn', 'i18n'})
        self.assertEqual(set(requests[1]), set(requests[0]) | {'id'})
        self.assertTrue(all(item[1]['need_auth'] for item in FakeBase.calls))

    def test_existing_signer_and_common_parameters_with_synthetic_data(self):
        logger_stub = ModuleType('app.logger')
        logger_stub.loki_handler = Mock()
        previous = sys.modules.pop('app.api', None)
        try:
            with patch.dict(sys.modules, {'app.logger': logger_stub}), \
                    patch('socket.socket', side_effect=AssertionError('network prohibited')):
                module = importlib.import_module('app.api')
                api = module.DessAPI.__new__(module.DessAPI)
                api.secret, api.token = SECRETS[7], SECRETS[6]
                params = {'action': 'queryDeviceCtrlField', 'pn': SECRETS[3], 'devcode': SECRETS[5],
                          'devaddr': 17, 'sn': SECRETS[4], 'i18n': 'en_US'}
                action = api._serialize_action_pairs(api._build_action_pairs(params))
                with patch.object(module.time, 'time', return_value=1234567890):
                    signature, salt = api._generate_sign(action)
                expected = hashlib.sha1((salt+SECRETS[7]+SECRETS[6]+action).encode()).hexdigest()
                self.assertTrue(signature == expected)
                self.assertTrue(salt == '1234567890000')
                self.assertTrue(action.startswith('&action=queryDeviceCtrlField&'))
                self.assertIn('&i18n=en_US&source=1&', action)
        finally:
            sys.modules.pop('app.api', None)
            if previous is not None:
                sys.modules['app.api'] = previous

    def test_real_client_guard_and_rejection_with_mocked_http(self):
        logger_stub = ModuleType('app.logger')
        logger_stub.loki_handler = Mock(return_value=logging.NullHandler())
        previous = sys.modules.pop('app.api', None)
        try:
            with patch.dict(sys.modules, {'app.logger': logger_stub}), \
                    patch('socket.socket', side_effect=AssertionError('network prohibited')):
                module = importlib.import_module('app.api')
                logger = logging.Logger('synthetic-private-test')
                logger.disabled = True
                client = readonly_client_class(module)(config(), logger)
                client.token, client.secret = SECRETS[6], SECRETS[7]
                client.should_refresh_token = Mock(return_value=False)
                with self.assertRaises(GuardError): client.set_charger_source('solar_only')
                with self.assertRaises(GuardError): client.ctrl_device('example', '1')
                payload = json.dumps({'err': 6, 'desc': 'password-SENTINEL https://invalid/?token=token-SENTINEL'}).encode()
                with patch('urllib.request.urlopen', return_value=io.BytesIO(payload)) as mocked_http:
                    with self.assertRaises(Exception) as raised:
                        discover(client)
                result = diagnostic_error(raised.exception)
                self.assertEqual((result['action'], result['err_decimal'], result['error_name']),
                                 ('queryDeviceCtrlField', 6, 'ERR_FORMAT_ERROR'))
                self.assertEqual(mocked_http.call_count, 1)
                for secret in (*SECRETS, 'https://invalid'):
                    self.assertNotIn(secret, json.dumps(result))
        finally:
            sys.modules.pop('app.api', None)
            if previous is not None:
                sys.modules['app.api'] = previous

    def test_guard_rejects_writes_passthrough_and_unadvertised_value_at_boundary(self):
        common = {'pn': self.client.pn, 'devcode': self.client.dev_code, 'devaddr': self.client.dev_addr,
                  'sn': self.client.sn, 'i18n': 'en_US'}
        for action in ('ctrlDevice', 'sendCmdToDevice', 'setChargerSource', 'unknown', 'queryDeviceLastData'):
            with self.subTest(action=action), self.assertRaises(GuardError):
                self.client._do_api_request({'action': action, **common})
        with self.assertRaises(GuardError): self.client.ctrl_device('example', '1')
        with self.assertRaises(GuardError):
            self.client._do_api_request({'action': 'queryDeviceCtrlValue', **common, 'id': 'unknown'})
        with self.assertRaises(GuardError):
            self.client._do_api_request({'action': 'queryDeviceCtrlField', **common, 'val': '1'})
        with self.assertRaises(GuardError):
            self.client._do_api_request({'action': 'queryDeviceCtrlField', **common}, base_url='https://invalid.example')
        self.assertEqual(FakeBase.calls, [])

    def test_no_retry_no_second_catalogue_and_value_must_be_advertised(self):
        with self.assertRaises(DiscoveryError): discover(self.client, 'not-advertised')
        self.assertEqual(len(FakeBase.calls), 1)

    def test_transport_also_enforces_catalogue_membership_after_read(self):
        common = {'pn': self.client.pn, 'devcode': self.client.dev_code, 'devaddr': self.client.dev_addr,
                  'sn': self.client.sn, 'i18n': 'en_US'}
        self.client._do_api_request({'action': 'queryDeviceCtrlField', **common})
        with self.assertRaises(GuardError):
            self.client._do_api_request({'action': 'queryDeviceCtrlValue', **common, 'id': 'not-advertised'})
        self.assertEqual([p['action'] for p, _ in FakeBase.calls], ['queryDeviceCtrlField'])
        with self.assertRaises(GuardError): discover(self.client)
        self.assertEqual(len(FakeBase.calls), 1)

    def test_invalid_and_duplicate_schemas_do_not_authorize_value(self):
        for response in ({'err': 0, 'dat': {}}, {'dat': {'field': []}},
                         {'err': 0, 'dat': {'field': [{'id': 'x'}, {'id': 'x'}]}},
                         {'err': 0, 'dat': {'field': [{'id': 'x', 'item': [{'key': '0'}]}]}}):
            with self.subTest(response=response):
                FakeBase.response = response
                client = readonly_client_class(SimpleNamespace(DessAPI=FakeBase))(config(), Mock())
                with self.assertRaises(DiscoveryError): discover(client, 'x')
                self.assertFalse(any(item[0]['action'] == 'queryDeviceCtrlValue' for item in FakeBase.calls))
                FakeBase.calls = []

    def test_malformed_value_response_has_the_value_action_and_safe_schema_status(self):
        FakeBase.value_response = {'err': 0, 'dat': {'id': 'different', 'val': SECRETS[1]}}
        with self.assertRaises(DiscoveryError) as raised:
            discover(self.client, 'example_output_priority')
        result = diagnostic_error(raised.exception)
        self.assertEqual(result['action'], 'queryDeviceCtrlValue')
        self.assertEqual(result['status'], 'invalid_response')
        self.assertFalse(result['response_schema_matched'])
        self.assertNotIn(SECRETS[1], json.dumps(result))

    def test_sensitive_fields_are_redacted_and_unknown_response_fields_omitted(self):
        raw = catalogue()
        raw['dat']['field'][0]['name'] = 'Output PN-SENTINEL token=token-SENTINEL'
        raw['dat']['field'][0]['hint'] = 'https://invalid.example/?sign=raw&salt=raw'
        raw['dat']['field'][0]['extra'] = 'session-SENTINEL'
        raw['dat']['field'][1]['hint'] = 'device address 17'
        raw['dat']['field'][2]['hint'] = 'generated-SENTINEL'
        self.client._diagnostic_sensitive_values.add('generated-SENTINEL')
        FakeBase.response = raw
        output = json.dumps(discover(self.client))
        for secret in (*SECRETS, 'https://', 'sign=raw', 'salt=raw'):
            self.assertNotIn(secret, output)
        self.assertIn('[REDACTED]', output)
        self.assertNotIn('device address 17', output)
        self.assertNotIn('generated-SENTINEL', output)

    def test_documented_and_unknown_errors_are_safe(self):
        class ApiError(Exception):
            def __init__(self, code, action): self.err, self.action = code, action
        for code, name in ((6, 'ERR_FORMAT_ERROR'), (10, 'ERR_NO_AUTH'), (12, 'ERR_NO_RECORD'), (258, 'ERR_NOT_FOUND_DEVICE')):
            result = diagnostic_error(ApiError(code, 'queryDeviceCtrlValue'))
            self.assertEqual(result['action'], 'queryDeviceCtrlValue')
            self.assertEqual(result['err_decimal'], code)
            self.assertEqual(result['err_hex'], f'0x{code:04X}')
            self.assertEqual(result['error_name'], name)
            self.assertIsNone(result['response_schema_matched'])
        unknown = diagnostic_error(ApiError(0xABCD, 'queryDeviceCtrlField'))
        self.assertIsNone(unknown['error_name'])
        self.assertEqual(unknown['err_hex'], '0xABCD')
        self.assertGreater(len(_ERROR_NAMES), 70)
        self.assertNotIn('password-SENTINEL', json.dumps(diagnostic_error(RuntimeError('password-SENTINEL'))))

    def test_auth_timeout_http_and_network_errors_have_distinct_safe_categories(self):
        class DessAuthError(Exception): pass
        class DessTimeoutError(Exception): pass
        http_error = urllib.error.HTTPError('https://secret.invalid', 403, SECRETS[3], None, io.BytesIO())
        for exc, status in ((DessAuthError(SECRETS[0]), 'authentication_error'),
                            (DessTimeoutError(SECRETS[1]), 'timeout'),
                            (urllib.error.URLError(SECRETS[2]), 'network_error'),
                            (http_error, 'http_error')):
            out = diagnostic_error(exc)
            self.assertEqual(out['status'], status)
            self.assertIsNone(out['err_decimal'])
            for secret in SECRETS: self.assertNotIn(secret, json.dumps(out))
        http_error.close()

    def test_offline_default_and_flags_never_load_config_or_use_network(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('network prohibited')), \
                patch('builtins.open', side_effect=AssertionError('file access prohibited')), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main([]), 0)
            self.assertEqual(main(['--live']), 1)
            self.assertEqual(main(['--read-only']), 1)
            self.assertEqual(main(['--value', 'example']), 1)
        out = json.loads(stdout.getvalue())
        self.assertEqual(out['mode'], 'offline')
        self.assertEqual([e['err_decimal'] for e in out['error_examples']], [6, 10, 12, 258])
        self.assertTrue(all(not x['hardware_write_attempted'] for x in out['error_examples']))
        for secret in SECRETS: self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())

    def test_explicit_live_flags_with_mocked_client_suppress_diagnostics(self):
        import app
        fake_api, fake_config = ModuleType('app.api'), ModuleType('app.config')
        fake_api.DessAPI = FakeBase
        fake_api.loki_handler = Mock()
        fake_api.urllib = SimpleNamespace(request=SimpleNamespace(urlopen=None))
        def fake_config_factory():
            print(SECRETS[1])
            return config()
        fake_config.Config = fake_config_factory
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(sys.modules, {'app.api': fake_api, 'app.config': fake_config}), \
                patch.object(app, 'api', fake_api, create=True), \
                patch('socket.socket', side_effect=AssertionError('network prohibited')), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(['--live', '--read-only']), 0)
        self.assertEqual([p['action'] for p, _ in FakeBase.calls], ['queryDeviceCtrlField'])
        self.assertFalse(json.loads(out.getvalue())['hardware_write_attempted'])
        for secret in SECRETS: self.assertNotIn(secret, out.getvalue() + err.getvalue())

    def test_mocked_live_rejection_reports_code_without_exception_or_logs(self):
        import app
        class DessAPIError(Exception):
            err = 6
        FakeBase.error = DessAPIError('password-SENTINEL https://invalid/?token=token-SENTINEL')
        fake_api, fake_config = ModuleType('app.api'), ModuleType('app.config')
        fake_api.DessAPI, fake_api.loki_handler = FakeBase, Mock()
        fake_api.urllib = SimpleNamespace(request=SimpleNamespace(urlopen=None))
        fake_config.Config = config
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(sys.modules, {'app.api': fake_api, 'app.config': fake_config}), \
                patch.object(app, 'api', fake_api, create=True), \
                patch('socket.socket', side_effect=AssertionError('network prohibited')), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(['--live', '--read-only']), 1)
        result = json.loads(err.getvalue())
        self.assertEqual(result['action'], 'queryDeviceCtrlField')
        self.assertEqual((result['err_decimal'], result['err_hex'], result['error_name']), (6, '0x0006', 'ERR_FORMAT_ERROR'))
        self.assertIsNone(result['response_schema_matched'])
        for secret in (*SECRETS, 'https://invalid'): self.assertNotIn(secret, out.getvalue()+err.getvalue())
        self.assertEqual([p['action'] for p, _ in FakeBase.calls], ['queryDeviceCtrlField'])

    def test_signed_transport_rejects_other_hosts_and_redirects(self):
        opener = Mock()
        with patch('urllib.request.build_opener', return_value=opener):
            for url in ('http://api.dessmonitor.com/public/?token=synthetic',
                        'https://attacker.invalid/public/?token=synthetic',
                        'https://api.dessmonitor.com/other/?token=synthetic'):
                with self.assertRaises(GuardError): _safe_urlopen(url, timeout=1)
            opener.open.assert_not_called()
            _safe_urlopen('https://api.dessmonitor.com/public/?action=synthetic', timeout=1)
            opener.open.assert_called_once()
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://attacker.invalid'))

    def test_response_size_is_bounded(self):
        source = io.BytesIO(b'x' * (1024*1024+1))
        with self.assertRaises(DiscoveryError):
            with _BoundedResponse(source) as response:
                response.read()
        self.assertTrue(source.closed)


if __name__ == '__main__':
    unittest.main()
