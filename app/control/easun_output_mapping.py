"""Read-only mapping of EASUN catalogue values; no actuation."""
from decimal import Decimal, InvalidOperation
from enum import Enum
import re


class OutputField(str, Enum):
    PRIORITY = 'los_output_source_priority'
    UTILITY = 'bat_sp_utility_mode_voltage'
    BATTERY = 'bat_sp_battery_mode_voltage'


PRIORITY_KEYS = {'Utility': '0', 'Solar': '1', 'SBU': '2'}


def parse_reported(field: OutputField, value: str):
    """Parse reported human-readable settings, not physical operating state."""
    if type(field) is not OutputField or type(value) is not str:
        raise ValueError('invalid reported value')
    if field is OutputField.PRIORITY:
        if value not in PRIORITY_KEYS:
            raise ValueError('unknown priority read-back')
        return value
    if not re.fullmatch(r'\d+(?:\.\d+)?\s*V', value):
        raise ValueError('invalid voltage read-back')
    try:
        return Decimal(value[:-1].strip())
    except InvalidOperation as exc:
        raise ValueError('invalid voltage read-back') from exc


def validate_catalogue_value(field: OutputField, desired: str, catalogue: dict, *, installation_voltage_class=None):
    """Return the exact advertised key, never an inferred voltage encoding."""
    if type(field) is not OutputField or type(desired) is not str or type(catalogue) is not dict:
        raise ValueError('invalid field or catalogue')
    if type(installation_voltage_class) is not int or installation_voltage_class != 24:
        raise ValueError('installation voltage class is unconfirmed')
    if catalogue.get('id') != field.value or type(catalogue.get('item')) is not list:
        raise ValueError('missing advertised field or options')
    items = catalogue['item']
    if any(type(item) is not dict or type(item.get('key')) is not str or not item['key']
           or type(item.get('val')) is not str for item in items):
        raise ValueError('invalid catalogue item')
    if len({item['key'] for item in items}) != len(items):
        raise ValueError('duplicate catalogue key')
    if field is OutputField.PRIORITY:
        if desired not in PRIORITY_KEYS:
            raise ValueError('unknown priority')
        matches = [item['key'] for item in items if item['val'] == desired]
        if matches != [PRIORITY_KEYS[desired]]:
            raise ValueError('priority not uniquely advertised with verified key')
        return matches[0]
    if not re.fullmatch(r'\d+(?:\.\d+)?', desired):
        raise ValueError('invalid desired voltage')
    voltage = Decimal(desired)
    low, high = ((Decimal('21.0'), Decimal('25.5')) if field is OutputField.UTILITY
                 else (Decimal('24.0'), Decimal('29.0')))
    if not low <= voltage <= high or (voltage - low) % Decimal('0.5'):
        raise ValueError('voltage outside confirmed 24 V range or increment')
    matches = []
    for item in items:
        text = item['val']
        if not re.fullmatch(r'\d+(?:\.\d+)?\s*V', text):
            continue
        if parse_reported(field, text) == voltage:
            matches.append(item['key'])
    if len(matches) != 1:
        raise ValueError('voltage not uniquely advertised')
    return matches[0]
