"""Credential-safe DESS control discovery, offline unless explicitly enabled.

Only catalogue/value reads and authentication are allowed; no control action is
available. Reported values do not establish command execution or mode changes.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch


class DiscoveryError(ValueError):
    def __init__(self, message, *, action='queryDeviceCtrlField'):
        super().__init__(message)
        self.action = action


class GuardError(DiscoveryError):
    pass


class SelectionError(DiscoveryError):
    pass


class ConfigurationError(Exception):
    pass


class RequestFailure(Exception):
    def __init__(self, action):
        super().__init__('request failed')
        self.action = action


# Symbolic names from the official generic error table. No server descriptions
# are displayed; unknown codes remain numeric and are never interpreted.
_ERROR_NAMES = {
    0x0000:'ERR_NONE', 0x0001:'ERR_FAIL', 0x0002:'ERR_TIMEOUT', 0x0003:'ERR_SYSTEM_EXCEPTION',
    0x0004:'ERR_SIGN', 0x0005:'ERR_SALT', 0x0006:'ERR_FORMAT_ERROR', 0x0007:'ERR_MISSING_PARAMETER',
    0x0008:'ERR_FORBIDDEN', 0x0009:'ERR_UNSUPPORTED', 0x000A:'ERR_NO_AUTH', 0x000B:'ERR_NO_PERMISSION',
    0x000C:'ERR_NO_RECORD', 0x000D:'ERR_OVER_LIMITED', 0x000E:'ERR_DUPLICATE_OPER',
    0x000F:'ERR_NOT_FOUND_COMPANY_KEY', 0x0010:'ERR_PASSWORD_VERIF_FAIL', 0x0011:'ERR_PASSWORD_FORMAT_ERROR',
    0x0012:'ERR_MOBILE_BINDED', 0x0013:'ERR_MOBILE_FORMAT', 0x0014:'ERR_EMAIL_FORMAT',
    0x0015:'ERR_USRNAME_FORMAT', 0x0016:'ERR_COMPANY_NAME_FORMAT', 0x0017:'ERR_VCODE_INVALID',
    0x0018:'ERR_USR_NAME_DUPLICATE', 0x0019:'ERR_USR_DISABLED', 0x0100:'ERR_NOT_FOUND_API',
    0x0101:'ERR_NOT_FOUND_COLLECTOR', 0x0102:'ERR_NOT_FOUND_DEVICE', 0x0103:'ERR_INVALID_PN',
    0x0104:'ERR_NOT_FOUND_PLANT', 0x0105:'ERR_NOT_FOUND_USR', 0x0106:'ERR_DEVICE_OFFLINE',
    0x0107:'ERR_COLLECTOR_OFFLINE', 0x0108:'ERR_NOT_FOUND_DEVICE_WARNING', 0x0109:'ERR_NOT_FOUND_ENVMONITOR',
    0x010F:'ERR_NONE_OPERATION', 0x0110:'ERR_DUPLICATE_OPER_BIND',
    0x0111:'ERR_NOT_SUPPORT_ADD_OTHER_VCODE_COLLECTOR', 0x0112:'ERR_USR_BIND_WX_BIND',
    0x0113:'ERR_NOT_BIND_WX', 0x0114:'ERR_NOT_SUPPORT_MOVE_OTHER_VCODE_COLLECTOR_PLANTOWNER',
    0x0115:'ERR_NOT_FOUND_VENDOR', 0x0120:'ERR_NOT_FOUND_CAMERA', 0x0160:'ERR_NOT_FOUND_FIREWARE',
    0x0161:'ERR_NOT_FOUND_PROTOCOL', 0x0170:'ERR_NOT_FOUND_DEFINEDROLE', 0x0171:'ERR_NOT_FOUND_USR_GROUP',
    0x0172:'ERR_NOT_FOUND_PLANT_GROUP', 0x0173:'ERR_NOT_FOUND_GPRS_TARIFFPACKAGE',
    0x0174:'ERR_NOT_FOUND_RECHARGE_ORDER', 0x0180:'ERR_NOT_FOUND_SMSCODE', 0x0181:'ERR_NOT_FOUND_DEVICE_TOKEN',
    0x0182:'ERR_NOT_FOUND_DEVICE_CFG', 0x0183:'ERR_NOT_FOUND_COLLECTOR_TYPE', 0x0184:'ERR_NOT_FOUND_PERMISSION_GROUP',
    0x0185:'ERR_NOT_SETTING_LONLAT', 0x0200:'ERR_SMSCODE_DISMATCH', 0x0201:'ERR_SMSCODE_OVERTIME',
    0x0202:'ERR_MOBILE_UNBINDED', 0x0203:'ERR_MOBILE_UNMATCH', 0x0204:'ERR_NAME_DUPLICATE',
    0x0205:'ERR_COLLECOTR_ISNOT_EMPTY', 0x0206:'ERR_SUBUSR_ISNOT_EMPTY', 0x0207:'ERR_USR_EXISTED',
    0x0208:'ERR_PN_REGISTER', 0x0209:'ERR_COLLECTOR_NOTBELONG_USR', 0x020A:'ERR_COLLECTOR_EXISTED',
    0x020B:'ERR_NOT_PLANTBROWSER', 0x020C:'ERR_SMSCODE_TO_SHORT', 0x0300:'ERR_PROTOCOL_NOT_OPTION',
    0x0400:'ERR_OLNY_SUPPORT_PLANTOWNER', 0x0401:'ERR_OLNY_SUPPORT_DIST', 0x0402:'ERR_OLNY_SUPPORT_VERDOR',
    0x0403:'ERR_SUPERIOR_DIST_VERDOR', 0x0404:'ERR_UNSUPPORTED_CHARGE_TYPE', 0x0405:'ERR_DUPLICATE_PN',
    0x0406:'ERR_INVALID_GPRS_CARD', 0x0407:'ERR_USR_NOT_CREATE_ADMIN', 0x0408:'ERR_SUPPORT_DIST_VERDOR',
    0x0409:'ERR_NOT_CONFIRM', 0x0410:'ERR_ACCEPT', 0x0411:'ERR_QR_KEY_INVALID', 0x0412:'ERR_INVALID_CCID',
    0x0700:'ERR_COLLECTORS_NOT_ONLY_BELONG_DIST',
}
_GUIDANCE = {
    0x0002: ('The DESS service reported a timeout.', 'Check connectivity and retry manually later.'),
    0x0004: ('The DESS service rejected the signature.', 'Check the existing application authentication locally.'),
    0x0005: ('The DESS service rejected the salt.', 'Check the local clock and existing authentication locally.'),
    0x0006: ('The API rejected the request parameters.', 'Check the configured device and API support locally.'),
    0x0007: ('The API reported a missing parameter.', 'Check the existing device configuration locally.'),
    0x0009: ('The API reports that this operation is unsupported.', 'Consult the device vendor about catalogue support.'),
    0x000A: ('The API rejected authentication.', 'Check the existing application authentication locally.'),
    0x000B: ('The account lacks permission for this operation.', 'Ask the vendor about read-only catalogue permissions.'),
    0x000C: ('No visible catalogue records were returned.', 'Ask the vendor whether this protocol exposes a catalogue.'),
    0x0102: ('The API could not find the device.', 'Check the configured device locally without sharing identifiers.'),
    0x0106: ('The API reports that the device is offline.', 'Check the vendor application and retry manually later.'),
    0x0161: ('The API could not find the device protocol.', 'Ask the vendor about support for the installed protocol.'),
    0x0300: ('The protocol has no selectable fields.', 'Ask the vendor whether read-only control metadata is available.'),
}
_ALLOWED = frozenset(('authSource', 'updateToken', 'queryDeviceCtrlField', 'queryDeviceCtrlValue'))
_MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _BoundedResponse:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._response.close()

    def read(self):
        data = self._response.read(_MAX_RESPONSE_BYTES + 1)
        if len(data) > _MAX_RESPONSE_BYTES:
            raise DiscoveryError('response too large')
        return data


def _safe_urlopen(url, timeout):
    """Restrict signed requests to the official HTTPS endpoint; no proxies or redirects."""
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (parsed.scheme == 'https' and parsed.hostname == 'api.dessmonitor.com'
                 and parsed.port in (None, 443) and parsed.path == '/public/'
                 and parsed.username is None and parsed.password is None)
    except ValueError:
        valid = False
    if not valid:
        raise GuardError('unsupported endpoint')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return _BoundedResponse(opener.open(url, timeout=timeout))


def diagnostic_error(exc, action='queryDeviceCtrlField'):
    """Build a fixed-text diagnostic without examining free-form server text."""
    cause = exc
    chain = []
    while cause is not None and id(cause) not in {id(x) for x in chain}:
        chain.append(cause)
        cause = getattr(cause, '__cause__', None)
    supplied_action = next((value for x in chain if type(value := getattr(x, 'action', None)) is str and value in _ALLOWED), None)
    if supplied_action is not None:
        action = supplied_action
    if type(action) is not str or action not in _ALLOWED:
        action = 'queryDeviceCtrlField'
    code = next((getattr(x, 'err', None) for x in chain if type(getattr(x, 'err', None)) is int), None)
    if type(code) is int and 0 < code <= 0xFFFFFFFF:
        explanation, step = _GUIDANCE.get(code, ('The DESS API rejected the request.',
                                                'Consult the official error table and device vendor.'))
        return {'action': action, 'status': 'rejected', 'err_decimal': code, 'err_hex': f'0x{code:04X}',
                'error_name': _ERROR_NAMES.get(code), 'explanation': explanation, 'next_step': step,
                # The shared client exposes the parsed code, not the original
                # error envelope, so its full schema cannot be verified here.
                'response_schema_matched': None, 'hardware_write_attempted': False}
    if any(isinstance(x, GuardError) for x in chain):
        status, explanation = 'blocked', 'The diagnostic transport refused an unsupported action.'
    elif any(isinstance(x, SelectionError) for x in chain):
        status, explanation = 'invalid_selection', 'The requested field was not present in the returned catalogue.'
    elif any(isinstance(x, ConfigurationError) for x in chain):
        status, explanation = 'configuration_error', 'The existing application configuration is unavailable.'
    elif (any(isinstance(x, (DiscoveryError, json.JSONDecodeError, UnicodeDecodeError)) for x in chain)
          or (any(isinstance(x, RequestFailure) for x in chain)
              and any(isinstance(x, (ValueError, AttributeError)) for x in chain))):
        status, explanation = 'invalid_response', 'The diagnostic request or response did not match the expected schema.'
    elif any(isinstance(x, urllib.error.HTTPError) for x in chain):
        status, explanation = 'http_error', 'The HTTP request failed before a usable DESS response.'
    elif any(isinstance(x, (TimeoutError,)) or type(x).__name__ == 'DessTimeoutError' for x in chain):
        status, explanation = 'timeout', 'The request timed out without a usable DESS response.'
    elif any(isinstance(x, (urllib.error.URLError, ConnectionError)) for x in chain):
        status, explanation = 'network_error', 'The network request failed before a usable DESS response.'
    elif any(type(x).__name__ == 'DessAuthError' for x in chain):
        status, explanation = 'authentication_error', 'Authentication did not provide a usable session.'
    elif isinstance(exc, ImportError):
        status, explanation = 'dependency_error', 'Required application dependencies are unavailable.'
    else:
        status, explanation = 'local_error', 'The diagnostic could not complete safely.'
    return {'action': action, 'status': status, 'err_decimal': None, 'err_hex': None, 'error_name': None,
            'explanation': explanation, 'next_step': 'Check local configuration and dependencies; do not share credentials or raw responses.',
            'response_schema_matched': False if status == 'invalid_response' else (True if status == 'invalid_selection' else None),
            'hardware_write_attempted': False}


def _redactor(client):
    names = ('email', 'password', 'company_key', 'pn', 'sn', 'dev_code', 'dev_addr', 'token', 'secret')
    secrets = [str(x) for name in names if (x := getattr(client, name, None)) is not None and str(x)]
    def clean(value):
        text = str(value)
        if re.search(r'(?i)(https?://|(?:token|secret|password|sign|salt|devaddr|devcode|pn|sn)\s*[=:])', text):
            return '[REDACTED]'
        text = re.sub(r'(?i)\b[0-9a-f]{32,}\b', '[REDACTED]', text)
        generated = getattr(client, '_diagnostic_sensitive_values', ())
        generated = generated if type(generated) in (set, frozenset, tuple, list) else ()
        known = set(secrets) | {item for item in generated if type(item) is str and item}
        for secret in sorted(known, key=len, reverse=True):
            if text == secret:
                return '[REDACTED]'
            if len(secret) >= 4:
                text = text.replace(secret, '[REDACTED]')
            elif secret.isdigit():
                # Short addresses/protocol codes can collide with legitimate
                # enum values; privacy takes precedence over displaying them.
                text = re.sub(r'(?<!\d)' + re.escape(secret) + r'(?!\d)', '[REDACTED]', text)
        return text
    return clean


def _candidate(field_id, name):
    text = (field_id + ' ' + name).lower().replace('_', ' ').replace('-', ' ')
    if 'charg' in text:
        return None
    if 'output' in text and ('priority' in text or 'source' in text):
        return 'output_priority'
    if any(word in text for word in ('utility', 'grid', 'mains')) and any(word in text for word in ('back', 'return', 'comeback')):
        return 'back_to_utility'
    if 'battery' in text and any(word in text for word in ('back', 'return', 'comeback')):
        return 'back_to_battery'
    return None


def discover(client, value_id=None):
    """Read one catalogue and optionally one ID verified against that response."""
    client._ensure_authenticated()
    common = {'pn': client.pn, 'devcode': client.dev_code, 'devaddr': client.dev_addr,
              'sn': client.sn, 'i18n': 'en_US'}
    response = client._do_api_request({'action': 'queryDeviceCtrlField', **common}, need_auth=True, transport='api')
    dat = response.get('dat') if isinstance(response, dict) and type(response.get('err')) is int and response['err'] == 0 else None
    fields = dat.get('field') if isinstance(dat, dict) else None
    if not isinstance(fields, list):
        raise DiscoveryError('invalid catalogue')
    clean = _redactor(client)
    catalogue, ids = [], set()
    for field in fields:
        if not isinstance(field, dict) or type(field.get('id')) is not str or not field['id'] or field['id'] in ids:
            raise DiscoveryError('invalid catalogue ID')
        field_id = field['id']
        ids.add(field_id)
        name, items = field.get('name'), field.get('item', [])
        if name is not None and type(name) is not str:
            raise DiscoveryError('invalid catalogue label')
        if items is None:
            items = []
        if not isinstance(items, list) or any(not isinstance(item, dict) or type(item.get('key')) is not str or type(item.get('val')) is not str for item in items):
            raise DiscoveryError('invalid catalogue enum')
        if any(field.get(key) is not None and type(field[key]) is not str for key in ('unit', 'hint')):
            raise DiscoveryError('invalid catalogue metadata')
        catalogue.append({'id': clean(field_id), 'name': clean(name) if name is not None else None,
                          'unit': clean(field['unit']) if field.get('unit') is not None else None,
                          'hint': clean(field['hint']) if field.get('hint') is not None else None,
                          'enum': [{'key': clean(item['key']), 'value': clean(item['val'])} for item in items],
                          'candidate': _candidate(field_id, name or '')})
    result = {'action': 'queryDeviceCtrlField', 'status': 'ok', 'response_schema_matched': True,
              'hardware_write_attempted': False, 'catalogue': catalogue,
              'candidate_labels_are_unverified': True, 'reported_value_is_not_verified_physical_application': True}
    if value_id is not None:
        if type(value_id) is not str or value_id not in ids:
            raise SelectionError('unadvertised ID', action='queryDeviceCtrlValue')
        value = client._do_api_request({'action': 'queryDeviceCtrlValue', **common, 'id': value_id}, need_auth=True, transport='api')
        value = value.get('dat') if isinstance(value, dict) and type(value.get('err')) is int and value['err'] == 0 else None
        if not isinstance(value, dict) or value.get('id') != value_id or 'val' not in value or type(value['val']) not in (str, int, float, bool):
            raise DiscoveryError('invalid value response', action='queryDeviceCtrlValue')
        result['reported_value'] = {'action': 'queryDeviceCtrlValue', 'status': 'ok', 'response_schema_matched': True,
                                    'id': clean(value_id), 'value': clean(value['val']),
                                    'meaning': 'reported setting only; physical application is unverified'}
    return result


def readonly_client_class(api_module):
    """Wrap the existing signer with a narrow request-boundary allowlist."""
    class ReadOnlyClient(api_module.DessAPI):
        def __init__(self, config, logger):
            self._catalogue_ids = frozenset()
            self._counts = {}
            self._diagnostic_sensitive_values = set()
            super().__init__(config, logger)

        def _load_cached_token(self):
            pass

        def _save_token(self):
            pass

        def _generate_sign(self, *args, **kwargs):
            signature, salt = super()._generate_sign(*args, **kwargs)
            self._diagnostic_sensitive_values.update((signature, salt))
            return signature, salt

        def _ensure_authenticated(self):
            # No fallback/retry on authentication failure.
            if not self.token or not self.secret:
                self.authenticate()
            elif self.should_refresh_token():
                self.refresh_token()

        def _do_api_request(self, params, *, need_auth=True, base_url=None, transport='api'):
            if type(params) is not dict or params.get('action') not in _ALLOWED or base_url is not None or transport != 'api':
                raise GuardError('unsupported action')
            action = params['action']
            device_keys = {'action', 'pn', 'devcode', 'devaddr', 'sn', 'i18n'}
            expected = {'authSource': {'action', 'usr', 'company-key'}, 'updateToken': {'action'},
                        'queryDeviceCtrlField': device_keys, 'queryDeviceCtrlValue': device_keys | {'id'}}[action]
            if set(params) != expected or need_auth is not (action != 'authSource'):
                raise GuardError('unexpected parameters')
            if action in ('queryDeviceCtrlField', 'queryDeviceCtrlValue'):
                if any(params[key] != expected_value for key, expected_value in
                       (('pn', self.pn), ('devcode', self.dev_code), ('devaddr', self.dev_addr), ('sn', self.sn), ('i18n', 'en_US'))):
                    raise GuardError('device parameters do not match')
            if action == 'authSource' and (params['usr'] != self.email or params['company-key'] != self.company_key):
                raise GuardError('authentication parameters do not match')
            if action == 'queryDeviceCtrlValue' and params['id'] not in self._catalogue_ids:
                raise GuardError('unadvertised ID')
            if self._counts.get(action, 0) >= 1:
                raise GuardError('request limit reached')
            self._counts[action] = self._counts.get(action, 0) + 1
            try:
                response = super()._do_api_request(params, need_auth=need_auth, transport=transport)
            except Exception as exc:
                raise RequestFailure(action) from exc
            if action == 'queryDeviceCtrlField':
                data = response.get('dat') if isinstance(response, dict) and type(response.get('err')) is int and response['err'] == 0 else None
                fields = data.get('field') if isinstance(data, dict) else None
                if not isinstance(fields, list) or any(not isinstance(field, dict) or type(field.get('id')) is not str or not field['id'] for field in fields):
                    raise DiscoveryError('invalid catalogue')
                ids = [field['id'] for field in fields]
                if len(set(ids)) != len(ids):
                    raise DiscoveryError('duplicate catalogue ID')
                self._catalogue_ids = frozenset(ids)
            return response
    return ReadOnlyClient


def offline_demo():
    """Use in-memory synthetic data only, with no configuration or networking."""
    class SyntheticClient:
        email = 'example@example.invalid'; password = 'synthetic-password'; company_key = 'synthetic-company'
        pn = 'synthetic-pn'; sn = 'synthetic-sn'; dev_code = 'synthetic-code'; dev_addr = 1
        token = 'synthetic-token'; secret = 'synthetic-secret'
        def _ensure_authenticated(self): pass
        def _do_api_request(self, params, **kwargs):
            if params.get('action') != 'queryDeviceCtrlField':
                raise GuardError('offline action refused')
            return {'err': 0, 'dat': {'field': [{'id': 'example_output_priority', 'name': 'Output Source Priority',
                                        'item': [{'key': '0', 'val': 'Example option'}]}]}}
    class SyntheticError(Exception):
        def __init__(self, code): self.err = code
    return {'mode': 'offline', 'synthetic': True, 'result': discover(SyntheticClient()),
            'error_examples': [diagnostic_error(SyntheticError(code)) for code in (6, 10, 12, 258)]}


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise GuardError('invalid arguments')


def main(argv=None):
    parser = _SafeParser(description='DESS catalogue diagnostics; offline by default.')
    parser.add_argument('--live', action='store_true', help='explicitly enable the operator-only API request')
    parser.add_argument('--read-only', action='store_true', help='confirm read-only diagnostic mode')
    parser.add_argument('--value', metavar='CATALOGUE_ID', help='also request one ID from the returned live catalogue')
    try:
        args = parser.parse_args(argv)
        if args.live != args.read_only or (args.value is not None and not args.live):
            raise GuardError('explicit live and read-only flags required together')
        if not args.live:
            print(json.dumps(offline_demo(), ensure_ascii=False, indent=2))
            return 0
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                import app.api as api_module
                from app.config import Config
                logger = logging.Logger('dess-diagnostic-disabled')
                logger.disabled = True
                logger.propagate = False
                with patch.object(api_module, 'loki_handler', return_value=logging.NullHandler()):
                    client = readonly_client_class(api_module)(Config(), logger)
                # Preserve the shared signer while preventing credential-bearing
                # redirects or proxy forwarding during this diagnostic process.
                with patch.object(api_module.urllib.request, 'urlopen', side_effect=_safe_urlopen):
                    result = discover(client, args.value)
            except SystemExit as exc:
                raise ConfigurationError('configuration unavailable') from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps(diagnostic_error(exc)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
