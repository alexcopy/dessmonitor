import unittest
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from app.control.easun_output_planner import plan_battery_threshold
from app.control.easun_output_mapping import OutputField
from test_adaptive_output_policy import BASE

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.learner = Mock()
        self.learner.propose.return_value = SimpleNamespace(status='SHADOW_PROPOSAL', reason='test', expires_at=BASE+timedelta(minutes=1), battery_threshold_v=26.5)
        self.cat = {'id': OutputField.BATTERY.value, 'item': [{'key': 'actual-key', 'val': '26.5V'}]}
    def plan(self, **overrides):
        args = dict(evaluated_at=BASE, reported_value='26.0V', installation_voltage_class=24,
                    approved_min_v=Decimal('25'), approved_max_v=Decimal('27'))
        args.update(overrides)
        return plan_battery_threshold(self.learner, None, None, (), None, None, self.cat, **args)
    def test_shadow_plan_and_inhibition(self):
        plan = self.plan()
        self.assertEqual((plan.status, plan.key, plan.executable), ('PROPOSED', 'actual-key', False))
        self.assertEqual(self.plan(manual_override=True).status, 'REFUSED')
        self.assertEqual(self.plan(unknown_write=True).status, 'REFUSED')
        self.assertEqual(self.plan(approved_max_v=None).status, 'REFUSED')
        self.assertEqual(self.plan(installation_voltage_class=None).status, 'REFUSED')
        self.assertEqual(self.plan(reported_value='26.5V').reason, 'ALREADY_REPORTED')
    def test_expired_and_cooldown_are_refused(self):
        self.learner.propose.return_value.expires_at = BASE-timedelta(seconds=1)
        self.assertEqual(self.plan().reason, 'PROPOSAL_EXPIRED')
        self.learner.propose.return_value.status = 'COOLDOWN'
        self.assertEqual(self.plan().status, 'REFUSED')

if __name__ == '__main__': unittest.main()
