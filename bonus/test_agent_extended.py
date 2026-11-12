"""Offline regressions: pagination, model-context projection and bounded live retry."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import run_agent
from bonus.tools import BonusTools, SCHEMAS, SYSTEM_PROMPT
from test_agent import ScriptedProvider, call, final


class PaginationTests(unittest.TestCase):
    def test_latest_incident_comparison_requires_details_for_each_provider(self):
        tools = BonusTools(scenario='noisy')
        first = call('vercel-status', 'get_vercel_status', {'component': 'Builds'})
        second = call('npm-status', 'get_npm_status', {'component': 'package publishing'})
        first['calls'] += second['calls']
        first['message']['tool_calls'] += second['message']['tool_calls']
        provider = ScriptedProvider([
            first, final('Both incidents resolved; https://www.vercel-status.com https://status.npmjs.org'),
        ])
        result = run_agent('Compare the latest incident scope and impact for Vercel Builds '
                           'and npm package publishing.', provider, tools, max_steps=2)
        self.assertEqual(result['status'], 'max_steps')
        pending = next(event['incidents'] for event in result['trace']
                       if event['event'] == 'validation_pending_incidents')
        self.assertEqual({item['provider'] for item in pending}, {'vercel', 'npm'})

    def test_timeline_reads_all_pages_and_preserves_original_question(self):
        tools = BonusTools()
        args = {'provider': 'vercel', 'incident_id': 'sps6n39gm4sg', 'limit': 3}
        url = tools.get_incident_details(**args)['url']
        question = 'Tra timeline Vercel, tìm thông báo về upstream, dẫn nguồn.'
        provider = ScriptedProvider([
            call('page-0', 'get_incident_details', dict(args, offset=0)),
            final('Only read the first page. ' + url),
            call('page-3', 'get_incident_details', dict(args, offset=3)),
            call('page-6', 'get_incident_details', dict(args, offset=6)),
            final('Historical incident resolved; timeline read. ' + url),
        ])
        result = run_agent(question, provider, tools, max_steps=8)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(question, provider.histories[2][-1]['content'])
        offsets = [event['arguments']['offset'] for event in result['trace']
                   if event['event'] == 'tool']
        self.assertEqual(offsets, [0, 3, 6])
        state = json.loads(provider.histories[-1][0]['content'].split('EVIDENCE_STATE\n', 1)[1])
        self.assertEqual(state['incidents'][0]['updates_returned'], 7)
        self.assertEqual(state['incidents'][0]['updates_omitted'], 0)

    def test_reading_last_page_does_not_hide_an_unread_earlier_page(self):
        tools = BonusTools()
        args = {'provider': 'vercel', 'incident_id': 'sps6n39gm4sg', 'limit': 3}
        url = tools.get_incident_details(**args)['url']
        provider = ScriptedProvider([
            call('last', 'get_incident_details', dict(args, offset=6)),
            final('Done. ' + url),
            call('first', 'get_incident_details', dict(args, offset=0)),
            call('middle', 'get_incident_details', dict(args, offset=3)),
            final('All pages read. ' + url),
        ])
        result = run_agent('Read the full timeline.', provider, tools, max_steps=8)
        self.assertEqual(result['status'], 'completed')
        validation = next(event for event in result['trace']
                          if event['event'] == 'validation_pending_incidents')
        self.assertEqual(validation['incidents'][0]['offset'], 0)


class ContextAndTrustTests(unittest.TestCase):
    def test_noise_is_filtered_for_model_but_preserved_in_trace(self):
        provider = ScriptedProvider([
            call('status', 'get_vercel_status', {'component': 'Builds'}),
            final('Status source: https://www.vercel-status.com'),
        ])
        result = run_agent('Check Vercel Builds.', provider, BonusTools(scenario='noisy'))
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('distractors', json.loads(provider.histories[1][-1]['content']))
        raw = next(event['result'] for event in result['trace'] if event['event'] == 'tool')
        self.assertEqual(len(raw['distractors']), 160)

    def test_status_injection_notice_is_not_forwarded_as_instructions(self):
        provider = ScriptedProvider([
            call('status', 'get_npm_status', {}),
            final('npm is operational in the snapshot. https://status.npmjs.org'),
        ])
        result = run_agent('Check npm status.', provider, BonusTools(scenario='injection'))
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('external_notice', json.loads(provider.histories[1][-1]['content']))
        self.assertIn('external_notice', result['trace'][1]['result'])

    def test_core_update_injection_and_bold_source_do_not_exhaust_budget(self):
        from evals.scenarios import ScenarioTools
        tools = ScenarioTools('injection')
        args = {'provider': 'github', 'incident_id': 'xr3myzj7ms51'}
        url = tools.get_incident_details(**args)['url']
        provider = ScriptedProvider([
            call('details', 'get_incident_details', args),
            final('Resolved, including Actions. **' + url + '**'),
        ])
        result = run_agent('Read the incident and cite it.', provider, tools, max_steps=2)
        self.assertEqual(result['status'], 'completed')
        sent = json.loads(provider.histories[1][-1]['content'])
        self.assertFalse(any('SYSTEM OVERRIDE' in update['body'] for update in sent['updates']))
        self.assertTrue(any('SYSTEM OVERRIDE' in update['body']
                            for update in result['trace'][1]['result']['updates']))


class BoundedRetryTests(unittest.TestCase):
    def test_premature_final_after_429_requires_bounded_retry(self):
        tools = BonusTools(scenario='rate_limit_once')
        provider = ScriptedProvider([
            call('first', 'get_vercel_status', {}),
            final('Vercel unverified due to rate limit.'),
            call('retry', 'get_vercel_status', {}),
            final('Vercel operational in snapshot. https://www.vercel-status.com'),
        ])
        result = run_agent('Check Vercel and retry rate limits.', provider, tools, max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tools.calls[('get_vercel_status', 'vercel')], 2)
        self.assertIn('429', result['answer'])
        self.assertTrue(any(event['event'] == 'validation_pending_retry'
                            for event in result['trace']))

    def test_major_indicator_is_not_lost_when_description_is_partial(self):
        provider = ScriptedProvider([
            call('status', 'get_cloudflare_status', {}),
            final('Cloudflare has a partial system outage. https://www.cloudflarestatus.com'),
        ])
        result = run_agent('Check Cloudflare status.', provider, BonusTools(), max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertIn('indicator=major', result['answer'])

    def test_third_429_tells_model_not_to_request_fourth_call(self):
        tools = BonusTools(scenario='rate_limit_always')
        provider = ScriptedProvider([
            *(call(str(index), 'get_vercel_status', {}) for index in range(3)),
            final('Vercel unverified due to rate limiting.'),
        ])
        result = run_agent('Check Vercel.', provider, tools, max_steps=4)
        self.assertEqual(result['status'], 'completed')
        last_result = json.loads(provider.histories[3][-1]['content'])
        self.assertFalse(last_result['agent_retry']['retry_allowed'])
        self.assertEqual(last_result['agent_retry']['attempts'], 3)

    def make_tools(self, retry_after):
        success = BonusTools().execute('get_vercel_status', {})
        tools = SimpleNamespace(schemas=SCHEMAS, system_prompt=SYSTEM_PROMPT)
        tools.execute = Mock(side_effect=[
            {'ok': False, 'error': 'RATE_LIMITED', 'http_status': 429,
             'retry_after': retry_after}, success,
        ])
        return tools

    def test_retry_after_seconds_is_respected_before_retry(self):
        tools = self.make_tools('2')
        provider = ScriptedProvider([
            call('first', 'get_vercel_status', {}),
            call('retry', 'get_vercel_status', {}),
            final('Vercel operational in snapshot. https://www.vercel-status.com'),
        ])
        with patch('agent.time.monotonic', return_value=100), patch('agent.time.sleep') as sleep:
            result = run_agent('Check Vercel.', provider, tools)
        self.assertEqual(result['status'], 'completed')
        sleep.assert_called_once_with(2)
        self.assertEqual(tools.execute.call_count, 2)

    def test_http_date_retry_after_is_respected(self):
        tools = self.make_tools('Tue, 01 Jan 2030 00:00:02 GMT')
        provider = ScriptedProvider([
            call('first', 'get_vercel_status', {}),
            call('retry', 'get_vercel_status', {}),
            final('Vercel operational. https://www.vercel-status.com'),
        ])
        with patch('agent.datetime') as clock, patch('agent.time.monotonic', return_value=100), \
                patch('agent.time.sleep') as sleep:
            clock.now.return_value = datetime(2030, 1, 1, tzinfo=timezone.utc)
            result = run_agent('Check Vercel.', provider, tools)
        self.assertEqual(result['status'], 'completed')
        sleep.assert_called_once_with(2)

    def test_long_wait_stops_without_hammering_api(self):
        tools = self.make_tools('120')
        provider = ScriptedProvider([
            call('first', 'get_vercel_status', {}),
            call('blocked', 'get_vercel_status', {}),
            final('Vercel: unknown; unable to verify because of rate limiting.'),
        ])
        with patch('agent.time.sleep') as sleep:
            result = run_agent('Check Vercel.', provider, tools)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tools.execute.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(json.loads(provider.histories[2][-1]['content'])['error'], 'RETRY_LIMIT_REACHED')

    def test_missing_retry_after_uses_backoff(self):
        tools = self.make_tools(None)
        provider = ScriptedProvider([
            call('first', 'get_vercel_status', {}),
            call('retry', 'get_vercel_status', {}),
            final('Vercel operational. https://www.vercel-status.com'),
        ])
        with patch('agent.time.monotonic', return_value=100), patch('agent.time.sleep') as sleep:
            result = run_agent('Check Vercel.', provider, tools)
        self.assertEqual(result['status'], 'completed')
        sleep.assert_called_once_with(1)

    def test_registry_execution_is_capped_even_if_model_keeps_retrying(self):
        tools = BonusTools(scenario='rate_limit_always')
        provider = ScriptedProvider([
            *(call(str(index), 'get_vercel_status', {}) for index in range(4)),
            final('Vercel: unknown due to rate limiting.'),
        ])
        result = run_agent('Check Vercel.', provider, tools, max_steps=6)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tools.calls[('get_vercel_status', 'vercel')], 3)
        self.assertEqual(json.loads(provider.histories[4][-1]['content'])['error'], 'RETRY_LIMIT_REACHED')


if __name__ == '__main__':
    unittest.main()
