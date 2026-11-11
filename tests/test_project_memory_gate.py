"""Regression tests for scripts/check-project-memory.sh: rejected directories fail the gate.

Each test builds a synthetic .project-memory tree in a temporary directory and runs the
real script there; the repository's own .project-memory is only read, never changed.
"""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / 'scripts' / 'check-project-memory.sh'
GOVERNANCE = ('AGENT_STANDARD.txt', 'ORCHESTRATOR_STANDARD.txt', 'ROADMAP.md', 'CURRENT_STATE.md',
              'ADR/ADR-0001-agent-workflow.md', 'ADR/ADR-0002-dockerhub-argocd-deployment.md',
              'ADR/ADR-0003-runtime-safety-before-ml-control.md')
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def run_gate(cwd):
    result = subprocess.run(['bash', str(GATE)], cwd=cwd, capture_output=True, text=True, timeout=60)
    return result.returncode, ANSI.sub('', result.stdout + result.stderr)


@unittest.skipUnless(shutil.which('bash'), 'requires bash')
class ProjectMemoryGateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='pm-gate-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in GOVERNANCE:
            path = self.root / '.project-memory' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic\n')
        self.pr('0010-legacy', 'PLAN.md', 'reviews/plan-review.yml')  # legacy names are fine before 0034a
        self.pr('0040-canonical', 'PLAN.txt', 'reviews/PLAN_REVIEW.txt', 'CODER_REPORT.txt',
                'reviews/PRECOMMIT_REVIEW.txt')
        self.pr('fix-auth-validation-python-discovery', 'PLAN.md')

    def pr(self, name, *files):
        directory = self.root / '.project-memory' / 'pr' / name
        directory.mkdir(parents=True, exist_ok=True)
        for file in files:
            (directory / file).parent.mkdir(parents=True, exist_ok=True)
            (directory / file).write_text('synthetic\n')

    def assert_rejected(self, output, name, *reasons):
        self.assertIn('REJECTED DIRECTORY: %s' % name if not reasons or 'non-numeric' not in reasons[0]
                      else 'REJECTED: %s' % name, output)
        self.assertIn(' Rejected: %s' % name, output)
        for reason in reasons:
            self.assertIn('%s — %s' % (name, reason), output)

    def test_clean_tree_passes_with_exit_zero(self):
        code, output = run_gate(self.root)
        self.assertEqual(code, 0, output)
        self.assertIn('Rejected directories:     0', output)
        self.assertIn('PASSED', output)

    def test_legacy_review_yaml_in_canonical_directory_fails_and_is_explained(self):
        self.pr('0070-journal-v2-review', 'reviews/plan-review.yml')
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assert_rejected(output, '0070-journal-v2-review', 'legacy reviews/plan-review.yml present',
                             'YAML/YML file not allowed')
        self.assertIn('FAILED', output)
        self.assertNotIn('PASSED', output)

    def test_missing_plan_artifacts_fail(self):
        self.pr('0038-override-only', 'CODER_REPORT.txt', 'OPERATOR_OVERRIDE.txt', 'reviews/PRECOMMIT_REVIEW.txt')
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assert_rejected(output, '0038-override-only', 'PRECOMMIT_COMPLETE but missing PLAN.txt',
                             'PRECOMMIT_COMPLETE but missing reviews/PLAN_REVIEW.txt')

    def test_legacy_plan_md_and_wrong_placement_fail(self):
        self.pr('0041-plan-md', 'PLAN.md')
        self.pr('0042-misplaced-review', 'PLAN.txt', 'PLAN_REVIEW.txt')
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assert_rejected(output, '0041-plan-md', 'legacy PLAN.md present')
        self.assert_rejected(output, '0042-misplaced-review', 'PLAN_REVIEW.txt in root directory')

    def test_unknown_non_numeric_directory_fails(self):
        self.pr('journal-v2', 'PLAN.txt')
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn('REJECTED: journal-v2 — unknown non-numeric PR directory', output)
        self.assertIn(' Rejected: journal-v2', output)

    def test_every_rejection_is_listed_and_counted(self):
        self.pr('0050-a', 'PLAN.md')
        self.pr('0051-b', 'reviews/precommit-review.yml')
        self.pr('unknown-c')
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn('Rejected directories:     3', output)
        self.assertIn('Errors:                   3', output)
        for name in ('0050-a', '0051-b', 'unknown-c'):
            self.assertIn(' Rejected: %s' % name, output)

    def test_canonical_planning_material_outside_pr_is_not_scanned(self):
        staging = self.root / '.project-memory' / 'pending' / 'journal-v2'
        staging.mkdir(parents=True)
        (staging / 'PLAN.txt').write_text('synthetic\n')
        code, output = run_gate(self.root)
        self.assertEqual(code, 0, output)

    # --- Grandfathered historical operator-override directories (AGENT_STANDARD 3a) ---

    GRANDFATHERED = ('0038-architect-dashboard-analytics-ui', '0039-output-power-out-w-fallback')

    def copy_original(self, name, target=None):
        target = self.root / '.project-memory' / 'pr' / (target or name)
        shutil.copytree(ROOT / '.project-memory' / 'pr' / name, target)
        return target

    def test_grandfathered_directories_pass_with_original_evidence(self):
        for name in self.GRANDFATHERED:
            self.copy_original(name)
        code, output = run_gate(self.root)
        self.assertEqual(code, 0, output)
        for name in self.GRANDFATHERED:
            self.assertIn('OK: %s [PRECOMMIT COMPLETE (grandfathered operator override)]' % name, output)

    def test_grandfathered_name_requires_unchanged_override_evidence(self):
        for artifact in ('OPERATOR_OVERRIDE.txt', 'CODER_REPORT.txt', 'reviews/PRECOMMIT_REVIEW.txt'):
            with self.subTest(artifact=artifact, change='modified'):
                directory = self.copy_original(self.GRANDFATHERED[0])
                with open(directory / artifact, 'a') as handle:
                    handle.write('tampered\n')
                code, output = run_gate(self.root)
                self.assertEqual(code, 1, output)
                self.assert_rejected(output, self.GRANDFATHERED[0], 'PRECOMMIT_COMPLETE but missing PLAN.txt')
                shutil.rmtree(directory)
        directory = self.copy_original(self.GRANDFATHERED[1])
        (directory / 'OPERATOR_OVERRIDE.txt').unlink()
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assert_rejected(output, self.GRANDFATHERED[1], 'PRECOMMIT_COMPLETE but missing PLAN.txt')

    def test_identical_evidence_under_another_name_still_fails(self):
        for target in ('0040-architect-dashboard-analytics-ui', '0038-architect-dashboard-analytics-ui-v2',
                       '0038a-architect-dashboard-analytics-ui', '0071-adaptive-decision-journal-v2'):
            self.copy_original(self.GRANDFATHERED[0], target)
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn('Rejected directories:     4', output)

    def test_grandfathered_directory_rejects_retrospective_or_legacy_artifacts(self):
        directory = self.copy_original(self.GRANDFATHERED[0])
        (directory / 'PLAN.txt').write_text('retrospective\n')
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assert_rejected(output, self.GRANDFATHERED[0], 'PRECOMMIT_COMPLETE but missing reviews/PLAN_REVIEW.txt')
        (directory / 'PLAN.txt').unlink()
        (directory / 'reviews' / 'plan-review.yml').write_text('verdict: approved\n')
        code, output = run_gate(self.root)
        self.assertEqual(code, 1, output)
        self.assert_rejected(output, self.GRANDFATHERED[0], 'legacy reviews/plan-review.yml present')

    def test_archived_artifacts_are_not_scanned(self):
        archive = self.root / '.project-memory' / 'archive' / '0070-journal-v2-review' / 'reviews'
        archive.mkdir(parents=True)
        (archive / 'plan-review.yml').write_text('verdict: CHANGES REQUIRED\n')
        code, output = run_gate(self.root)
        self.assertEqual(code, 0, output)

    def test_repository_archive_preserves_original_0070_review(self):
        archived = ROOT / '.project-memory' / 'archive' / '0070-journal-v2-review' / 'reviews' / 'plan-review.yml'
        self.assertEqual(hashlib.sha256(archived.read_bytes()).hexdigest(),
                         'bc73d22c5ca5228e342a7efa9c67e1d610c8728ed9a547626284c92282bf5d70')
        self.assertFalse((ROOT / '.project-memory' / 'pr' / '0070-journal-v2-review').exists())

    def test_repository_gate_passes(self):
        code, output = run_gate(ROOT)
        self.assertEqual(code, 0, output)
        self.assertIn('Rejected directories:     0', output)

    def test_repository_gate_exit_status_matches_its_rejections(self):
        code, output = run_gate(ROOT)
        rejected = re.findall(r'^ Rejected: (\S+)$', output, re.MULTILINE)
        self.assertEqual(code, 1 if rejected else 0, output)
        self.assertEqual(len(rejected), int(re.search(r'Rejected directories:\s+(\d+)', output).group(1)))


if __name__ == '__main__':
    unittest.main()
