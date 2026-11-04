import unittest
from decimal import Decimal
from app.control.easun_output_mapping import OutputField, parse_reported, validate_catalogue_value

class MappingTests(unittest.TestCase):
    def test_reported_values(self):
        self.assertEqual(parse_reported(OutputField.PRIORITY, 'SBU'), 'SBU')
        self.assertEqual(parse_reported(OutputField.UTILITY, '25.5V'), Decimal('25.5'))
        for value in ('2', 'unknown'):
            with self.assertRaises(ValueError): parse_reported(OutputField.PRIORITY, value)

    def test_real_catalogue_keys_and_24v_filter(self):
        cat = {'id': OutputField.UTILITY.value, 'item': [
            {'key': 'wire-unknown-a', 'val': '25.0V'}, {'key': 'wire-unknown-b', 'val': '25.5V'},
            {'key': 'wire-48v', 'val': '48.0V'}]}
        self.assertEqual(validate_catalogue_value(OutputField.UTILITY, '25.0', cat, installation_voltage_class=24), 'wire-unknown-a')
        for value in ('25.3', '26.0', '48.0', 'NaN', '24.5'):
            with self.assertRaises(ValueError): validate_catalogue_value(OutputField.UTILITY, value, cat, installation_voltage_class=24)
        with self.assertRaises(ValueError): validate_catalogue_value(OutputField.UTILITY, '25.0', cat)
        with self.assertRaises(ValueError): validate_catalogue_value(OutputField.UTILITY, '25.0', cat, installation_voltage_class=48)
        with self.assertRaises(ValueError): validate_catalogue_value('arbitrary', '25.0', cat, installation_voltage_class=24)
        with self.assertRaises(ValueError): validate_catalogue_value(OutputField.UTILITY, '25.0', {**cat, 'item': cat['item'] + [{'key': 'other', 'val': '25.0V'}]}, installation_voltage_class=24)

    def test_priority_requires_advertised_enum(self):
        cat = {'id': OutputField.PRIORITY.value, 'item': [{'key': '2', 'val': 'SBU'}]}
        self.assertEqual(validate_catalogue_value(OutputField.PRIORITY, 'SBU', cat, installation_voltage_class=24), '2')
        with self.assertRaises(ValueError): validate_catalogue_value(OutputField.PRIORITY, 'Solar', cat, installation_voltage_class=24)
        with self.assertRaises(ValueError): validate_catalogue_value(OutputField.PRIORITY, 'SBU', {**cat, 'item': [{'key':'0', 'val':'SBU'}]}, installation_voltage_class=24)

if __name__ == '__main__': unittest.main()
