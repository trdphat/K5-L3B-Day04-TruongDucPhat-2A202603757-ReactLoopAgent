"""Resume only unscored attempts; never cherry-pick low scored results."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
import run_evals


class ResumeTests(unittest.TestCase):
    def report(self, bonus=False, unscored=None):
        path = ROOT / ('bonus/cases.json' if bonus else 'evals/cases.json')
        cases = json.loads(path.read_text(encoding='utf-8'))
        items = []
        for case in cases:
            failed = case['id'] == unscored
            items.append({
                'id': case['id'], 'question': case['question'], 'expected': case['expected'],
                'result': {'status': 'completed', 'answer': 'Offline stub', 'trace': [], 'usage': []},
                'grade': {'status': 'unscored' if failed else 'scored',
                          'score': None if failed else 80},
            })
        return {
            'config': {'provider': 'openai', 'model': 'test', 'judge_provider': 'openai',
                       'judge_model': 'test', 'max_steps': 16 if bonus else 8,
                       'dataset': 'bonus' if bonus else 'core',
                       'request_interval': 5.0, 'http_retries': 3},
            'agent': {'version': 'after', 'file': 'agent.py',
                      'sha256': hashlib.sha256((ROOT / 'agent.py').read_bytes()).hexdigest()},
            'cases': items, 'summary': run_evals.summarize(items),
            'transport': {'http_attempts': 7, 'http_retries': 1, 'http_errors': []},
        }

    def invoke(self, report, bonus=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps(report), encoding='utf-8')
            original = path.read_bytes()
            args = ['run_evals.py', '--provider', 'openai', '--model', 'test',
                    '--judge-provider', 'openai', '--judge-model', 'test',
                    '--resume', '--output', str(path)]
            if bonus:
                args.append('--bonus')

            def retry(case, options):
                item = next(row for row in report['cases'] if row['id'] == case['id'])
                return {**item, 'grade': {'status': 'scored', 'score': 80}}

            with patch.object(sys, 'argv', args), patch.object(run_evals, 'load_env', return_value=False), \
                    patch.object(run_evals, 'run_case', side_effect=retry) as calls, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = run_evals.main()
            return status, json.loads(path.read_text(encoding='utf-8')), calls, original == path.read_bytes()

    def test_only_unscored_case_is_reexecuted_with_provenance(self):
        previous = self.report(unscored='A4')
        status, report, calls, unchanged = self.invoke(previous)
        self.assertEqual(status, 0)
        self.assertFalse(unchanged)
        self.assertEqual([call.args[0]['id'] for call in calls.call_args_list], ['A4'])
        self.assertEqual(report['summary']['scored_cases'], 22)
        self.assertEqual(report['summary']['score_0_100'], 80)
        self.assertEqual(report['resume_history'][0]['rerun_case_ids'], ['A4'])
        self.assertIsNone(report['resume_history'][0]['previous_unscored_cases'][0]['grade']['score'])
        self.assertEqual(report['transport']['http_attempts'], 7)
        self.assertEqual(report['transport']['http_retries'], 1)
        self.assertEqual(report['cases'][0], previous['cases'][0])

    def test_different_code_cannot_be_merged(self):
        report = self.report(unscored='A4')
        report['agent']['sha256'] = 'different-code'
        status, _, calls, unchanged = self.invoke(report)
        self.assertEqual(status, 1)
        calls.assert_not_called()
        self.assertTrue(unchanged)

    def test_different_config_cannot_be_merged(self):
        report = self.report(unscored='A4')
        report['config']['judge_model'] = 'different-judge'
        status, _, calls, unchanged = self.invoke(report)
        self.assertEqual(status, 1)
        calls.assert_not_called()
        self.assertTrue(unchanged)

    def test_low_or_zero_scored_cases_are_never_retried(self):
        report = self.report()
        report['cases'][0]['grade']['score'] = 0
        report['summary'] = run_evals.summarize(report['cases'])
        status, _, calls, unchanged = self.invoke(report)
        self.assertEqual(status, 0)
        calls.assert_not_called()
        self.assertTrue(unchanged)

    def test_bonus_points_require_all_ten_cases_after_resume(self):
        status, report, calls, _ = self.invoke(self.report(bonus=True, unscored='B3'), bonus=True)
        self.assertEqual(status, 0)
        self.assertEqual([call.args[0]['id'] for call in calls.call_args_list], ['B3'])
        self.assertEqual(report['summary']['scored_cases'], 10)
        self.assertEqual(report['summary']['bonus_points_0_15'], 12)


if __name__ == '__main__':
    unittest.main()
