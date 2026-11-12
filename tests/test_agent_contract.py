"""Bổ sung kiểm tra lỗi, history và việc chọn phiên bản khi chạy eval."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))

from agent import SYSTEM_PROMPT, run_agent
from status_tools import SCHEMAS, StatusTools
from test_lab import FakeProvider, final, tool_call
import run_evals


class AgentContractTests(unittest.TestCase):
    def test_history_keeps_original_assistant_and_usage(self):
        first = tool_call('first', 'get_status', '{"provider":"github"}')
        first['message']['provider_metadata'] = {'keep': True}
        first['usage'] = {'total_tokens': 20}
        last = final('Done')
        last['usage'] = {'total_tokens': 30}
        provider = FakeProvider([first, last])

        result = run_agent('Check status', provider, StatusTools())

        self.assertEqual(provider.histories[0], [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': 'Check status'},
        ])
        self.assertIs(provider.histories[1][-2], first['message'])
        self.assertEqual(provider.histories[1][-1]['tool_call_id'], 'first')
        self.assertEqual(result['usage'], [{'total_tokens': 20}, {'total_tokens': 30}])
        tools = [event for event in result['trace'] if event['event'] == 'tool']
        self.assertEqual(tools[0]['arguments'], {'provider': 'github'})
        self.assertEqual(tools[0]['step'], 1)

    def test_provider_gets_schemas(self):
        provider = Mock()
        provider.complete.return_value = final()
        run_agent('Question', provider, StatusTools())
        self.assertIs(provider.complete.call_args.args[1], SCHEMAS)

    def test_non_object_arguments_are_observations(self):
        for arguments in ('[]', 'null', '42', '"text"'):
            with self.subTest(arguments=arguments):
                provider = FakeProvider([tool_call('bad', 'get_status', arguments), final()])
                tools = Mock()
                result = run_agent('Question', provider, tools)
                tools.execute.assert_not_called()
                observation = json.loads(provider.histories[1][-1]['content'])
                self.assertEqual(observation['error'], 'INVALID_ARGUMENTS')
                self.assertEqual(result['status'], 'completed')

    def test_registry_errors_return_to_model(self):
        for name, arguments, error in (
            ('missing', '{}', 'UNKNOWN_TOOL'),
            ('get_status', '{"provider":"invalid"}', 'ValueError'),
        ):
            with self.subTest(name=name):
                provider = FakeProvider([tool_call('err', name, arguments), final()])
                result = run_agent('Question', provider, StatusTools())
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(json.loads(provider.histories[1][-1]['content'])['error'], error)
                self.assertEqual(result['trace'][1]['result']['error'], error)

    def test_raised_tool_timeout_is_returned_as_observation(self):
        tools = Mock()
        tools.execute.side_effect = TimeoutError('private diagnostic')
        provider = FakeProvider([tool_call('timeout', 'get_status', '{}'), final()])
        result = run_agent('Question', provider, tools)
        self.assertEqual(result['status'], 'completed')
        observation = json.loads(provider.histories[1][-1]['content'])
        self.assertEqual(observation['error'], 'TimeoutError')
        self.assertNotIn('private diagnostic', json.dumps(result))

    def test_provider_exception_returns_error_without_sensitive_text(self):
        provider = Mock()
        provider.complete.side_effect = RuntimeError('private credential')
        result = run_agent('Question', provider, StatusTools())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error'], 'PROVIDER_ERROR')
        self.assertEqual(result['trace'][0]['type'], 'RuntimeError')
        self.assertNotIn('private credential', json.dumps(result))

    def test_unexpected_finish_is_not_completed(self):
        for finish in ('length', 'content_filter', None, 'tool_calls'):
            with self.subTest(finish=finish):
                response = final('Partial answer')
                response['finish'] = finish
                result = run_agent('Question', FakeProvider([response]), StatusTools())
                self.assertEqual(result['status'], 'error')

    def test_zero_budget_does_not_call_provider(self):
        provider = Mock()
        result = run_agent('Question', provider, StatusTools(), max_steps=0)
        provider.complete.assert_not_called()
        self.assertEqual(result['status'], 'max_steps')


class SourceGateTests(unittest.TestCase):
    def test_invented_url_does_not_pass_source_gate(self):
        source = 'https://stspg.io/pjgptg3cfc6n'
        provider = FakeProvider([
            tool_call('details', 'get_incident_details',
                      '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
            final('Source: https://example.invalid/invented'),
            final('Resolved. Source: ' + source),
        ])
        result = run_agent('Question', provider, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(source, result['answer'])
        validation = next(event for event in result['trace']
                          if event['event'] == 'validation_missing_source')
        self.assertEqual(validation['step'], 2)
        self.assertEqual(validation['observed_urls'], [source])
        self.assertEqual(len(provider.histories), 3)
        self.assertEqual(provider.histories[2][-1]['role'], 'user')
        self.assertIn(source, provider.histories[2][-1]['content'])

    def test_repeated_missing_source_respects_budget(self):
        provider = FakeProvider([
            tool_call('details', 'get_incident_details',
                      '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
            final('No source'), final('Still no source'),
        ])
        result = run_agent('Question', provider, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['answer'], '')
        self.assertEqual(len(provider.histories), 3)


class BaselineRunnerTests(unittest.TestCase):
    def test_before_version_uses_preserved_agent(self):
        args = SimpleNamespace(provider='openai', model='test', judge_provider='openai',
                               judge_model='test', max_steps=8, agent_version='before')
        case = {'id': 'T', 'question': 'Question', 'expected': 'Judge only'}
        baseline_result = {'status': 'completed', 'answer': 'Baseline', 'trace': [], 'usage': []}
        with patch('agent_before.run_agent', return_value=baseline_result) as baseline, \
                patch.object(run_evals, 'run_agent') as current, \
                patch.object(run_evals, 'Provider') as provider, \
                patch.object(run_evals, 'grade_case', return_value={'status': 'scored', 'score': 80}):
            result = run_evals.run_case(case, args)
        baseline.assert_called_once()
        current.assert_not_called()
        self.assertEqual(baseline.call_args.args[0], 'Question')
        self.assertEqual(provider.call_count, 2)
        self.assertIs(result['result'], baseline_result)


if __name__ == '__main__':
    unittest.main()
