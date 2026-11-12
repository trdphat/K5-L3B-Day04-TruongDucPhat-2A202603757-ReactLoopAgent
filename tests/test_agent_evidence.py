"""Regression tests for dependent investigation and per-provider citations, offline."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import _Evidence, run_agent
from status_tools import StatusTools
from test_lab import FakeProvider, final, tool_call


def details(provider, incident_id, related_url=None):
    host = {'github': 'www.githubstatus.com',
            'cloudflare': 'www.cloudflarestatus.com'}[provider]
    return {
        'ok': True, 'provider': provider, 'id': incident_id,
        'url': f'https://{host}/incidents/{incident_id}',
        'updates': ([{'body': 'Please refer to the combined summary: ' + related_url}]
                    if related_url else []),
    }


class MemoryTools:
    def __init__(self, observations):
        self.observations = observations

    def execute(self, name, arguments):
        return self.observations[(arguments['provider'], arguments['incident_id'])]


def read_call(call_id, provider, incident_id):
    return tool_call(call_id, 'get_incident_details', json.dumps({
        'provider': provider, 'incident_id': incident_id,
    }))


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.child = details('github', 'combined')
        self.parent = details('github', 'initial', self.child['url'])
        self.tools = MemoryTools({('github', 'initial'): self.parent,
                                  ('github', 'combined'): self.child})

    def test_premature_final_requests_related_incident(self):
        provider = FakeProvider([
            read_call('parent', 'github', 'initial'),
            final('Cause not checked. ' + self.parent['url']),
            read_call('child', 'github', 'combined'),
            final('Combined summary checked. ' + self.child['url']),
        ])
        result = run_agent('Follow the related incident to find the cause.',
                           provider, self.tools, max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 4)
        self.assertTrue(any(event['event'] == 'validation_pending_incidents'
                            for event in result['trace']))
        self.assertIn('combined', provider.histories[2][-1]['content'])

    def test_unread_related_incident_cannot_bypass_budget(self):
        provider = FakeProvider([read_call('parent', 'github', 'initial'),
                                 final('Done. ' + self.parent['url'])])
        result = run_agent('Investigate the cause.', provider, self.tools, max_steps=2)
        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['answer'], '')

    def test_related_incident_already_in_batch_needs_no_revision(self):
        batch = read_call('parent', 'github', 'initial')
        child = read_call('child', 'github', 'combined')
        batch['calls'] += child['calls']
        batch['message']['tool_calls'] += child['message']['tool_calls']
        provider = FakeProvider([batch, final('Done. ' + self.child['url'])])
        result = run_agent('Investigate.', provider, self.tools)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 2)

    def test_failed_related_incident_does_not_force_infinite_retry(self):
        self.tools.observations[('github', 'combined')] = {
            'ok': False, 'error': 'INCIDENT_NOT_FOUND',
        }
        provider = FakeProvider([
            read_call('parent', 'github', 'initial'),
            read_call('child', 'github', 'combined'),
            final('Cannot verify cause; related incident unavailable. ' + self.parent['url']),
        ])
        result = run_agent('Investigate.', provider, self.tools, max_steps=3)
        self.assertEqual(result['status'], 'completed')

    def test_cycles_do_not_create_more_work(self):
        self.child['updates'] = [{'body': 'Related incident: ' + self.parent['url']}]
        provider = FakeProvider([
            read_call('parent', 'github', 'initial'),
            read_call('child', 'github', 'combined'),
            final('Done. ' + self.child['url']),
        ])
        result = run_agent('Investigate.', provider, self.tools, max_steps=3)
        self.assertEqual(result['status'], 'completed')

    def test_untrusted_or_malformed_link_is_not_an_investigation_target(self):
        for url in ('https://example.invalid/incidents/extra',
                    'https://www.githubstatus.com.evil.invalid/incidents/extra',
                    'https://www.githubstatus.com@evil.invalid/incidents/extra',
                    'https://[broken/incidents/extra'):
            with self.subTest(url=url):
                self.parent['updates'] = [{'body': 'Related incident: ' + url}]
                provider = FakeProvider([read_call('parent', 'github', 'initial'),
                                         final('Done. ' + self.parent['url'])])
                result = run_agent('Investigate.', provider, self.tools, max_steps=2)
                self.assertEqual(result['status'], 'completed')


class ProviderCitationTests(unittest.TestCase):
    def test_two_providers_require_a_source_for_each(self):
        github = details('github', 'deployment')
        cloudflare = details('cloudflare', 'network')
        tools = MemoryTools({('github', 'deployment'): github,
                             ('cloudflare', 'network'): cloudflare})
        batch = read_call('github', 'github', 'deployment')
        other = read_call('cloudflare', 'cloudflare', 'network')
        batch['calls'] += other['calls']
        batch['message']['tool_calls'] += other['message']['tool_calls']
        provider = FakeProvider([
            batch, final('Only one source: ' + github['url']),
            final('Both sources: ' + github['url'] + ' ' + cloudflare['url']),
        ])
        result = run_agent('Compare providers.', provider, tools, max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(cloudflare['url'], result['answer'])
        self.assertEqual(len(provider.histories), 3)

    def test_url_prefix_is_not_a_citation_of_the_observed_url(self):
        incident = details('github', 'deployment')
        tools = MemoryTools({('github', 'deployment'): incident})
        provider = FakeProvider([
            read_call('details', 'github', 'deployment'),
            final('Wrong source: ' + incident['url'] + '-invented'),
            final('[Correct source](' + incident['url'] + ').'),
        ])
        result = run_agent('Check source.', provider, tools, max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 3)


class EvidenceQualityTests(unittest.TestCase):
    def test_historical_incident_does_not_replace_current_status_lookup(self):
        tools = StatusTools()
        args = '{"provider":"github","incident_id":"t89g2q1800fr"}'
        url = tools.get_incident_details('github', 't89g2q1800fr')['url']
        provider = FakeProvider([
            tool_call('incident', 'get_incident_details', args),
            final('Resolved, so GitHub has no current outage. ' + url),
            tool_call('status', 'get_status', '{"provider":"github","component":"Actions"}'),
            final('GitHub Actions operational in the snapshot; this historical incident resolved. ' + url),
        ])
        result = run_agent('Is GitHub Actions currently in outage because of this incident?',
                           provider, tools, max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(any(event['event'] == 'validation_pending_status' for event in result['trace']))
        self.assertEqual(len(provider.histories), 4)

    def test_no_forced_status_lookup_for_poem_or_unsupported_provider(self):
        for question in ('Viết thơ, đừng kiểm tra GitHub hiện tại.',
                         'Is Slack currently in outage?'):
            with self.subTest(question=question):
                provider = FakeProvider([final('No status claims.')])
                self.assertEqual(run_agent(question, provider, StatusTools())['status'], 'completed')
                self.assertEqual(len(provider.histories), 1)

    def test_still_running_after_timeout_is_an_unsupported_claim(self):
        from evals.scenarios import ScenarioTools
        batch = tool_call('github', 'get_status', '{"provider":"github","component":"Actions"}')
        other = tool_call('cloudflare', 'get_status', '{"provider":"cloudflare","component":"Spectrum"}')
        batch['calls'] += other['calls']
        batch['message']['tool_calls'] += other['message']['tool_calls']
        provider = FakeProvider([
            batch,
            final('GitHub hoạt động bình thường. Cloudflare không thể truy cập được, '
                  'nhưng có thể kết luận rằng nó vẫn đang hoạt động. https://www.githubstatus.com'),
            final('GitHub operational theo snapshot. Cloudflare chưa xác minh do timeout. '
                  'https://www.githubstatus.com'),
        ])
        result = run_agent('Kiểm tra GitHub và Cloudflare.', provider,
                           ScenarioTools('cloudflare_timeout'), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('vẫn đang hoạt động', result['answer'])
        self.assertTrue(any(event['event'] == 'validation_factual_conflict' for event in result['trace']))

    def test_out_of_year_incidents_are_not_matches_for_historical_request(self):
        incident = details('github', 'summary')
        incident['created_at'] = '2026-01-01T00:00:00Z'
        provider = FakeProvider([
            read_call('details', 'github', 'summary'),
            final('Trong năm 2024, có một số sự cố:\n| Incident | 2026-01-01 | '
                  + incident['url'] + ' |'),
            final('Không đủ dữ liệu để xác nhận danh sách đầy đủ cho năm 2024. ' + incident['url']),
        ])
        result = run_agent('Tìm toàn bộ incident GitHub trong năm 2024.', provider,
                           MemoryTools({('github', 'summary'): incident}), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('2026-01-01', result['answer'])
        self.assertTrue(any(event['event'] == 'validation_factual_conflict' for event in result['trace']))

    def test_snapshot_timestamp_is_preserved_in_final_presentation(self):
        tools = StatusTools()
        captured = tools.get_status('github')['captured_at']
        provider = FakeProvider([
            tool_call('status', 'get_status', '{"provider":"github"}'),
            final('GitHub operational. https://www.githubstatus.com'),
        ])
        result = run_agent('Check GitHub now.', provider, tools, max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(captured, result['answer'])

    def test_counts_and_timestamps_are_visible_without_changing_tool_result(self):
        incident = details('github', 'summary')
        incident.update(updates_total=9, status='resolved',
                        created_at='2026-01-01T01:00:00Z',
                        updated_at='2026-01-03T05:00:00Z',
                        updates=[
                            {'status': 'resolved', 'created_at': '2026-01-01T02:00:00Z',
                             'body': 'Cause: a routing configuration change.', 'truncated': True},
                            {'status': 'monitoring', 'body': 'Monitoring.', 'truncated': False},
                            {'status': 'investigating', 'body': 'Investigating.', 'truncated': False},
                        ])
        provider = FakeProvider([read_call('details', 'github', 'summary'),
                                 final('Resolved. ' + incident['url'])])
        result = run_agent('Check this incident.', provider,
                           MemoryTools({('github', 'summary'): incident}))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(json.loads(provider.histories[1][-1]['content']), incident)
        note = provider.histories[1][0]['content']
        brief = json.loads(note.split('EVIDENCE_STATE\n', 1)[1])
        facts = brief['incidents'][0]
        self.assertEqual(facts['updates_returned'], 3)
        self.assertEqual(facts['updates_omitted'], 6)
        self.assertEqual(facts['returned_bodies_truncated'], 1)
        self.assertEqual(facts['resolved_update_times'], ['2026-01-01T02:00:00Z'])
        self.assertEqual(facts['record_updated_at'], '2026-01-03T05:00:00Z')

    def test_unsupported_status_claim_gets_factual_correction(self):
        provider = FakeProvider([
            tool_call('status', 'get_status', '{"provider":"cloudflare","component":"Spectrum"}'),
            final('Cloudflare Spectrum có thể coi là đang operational.'),
            final('Cloudflare Spectrum: không thể xác minh trạng thái vì tool timeout.'),
        ])
        result = run_agent('Check Cloudflare Spectrum.', provider,
                           StatusTools(fail=True), max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 3)
        self.assertEqual(sum(event['event'] == 'validation_factual_conflict'
                             for event in result['trace']), 1)
        self.assertNotIn('operational', result['answer'])

    def test_factual_correction_preserves_the_original_question(self):
        question = 'Compare providers and give next diagnostic steps.'
        provider = FakeProvider([
            tool_call('status', 'get_status', '{"provider":"cloudflare"}'),
            final('Cloudflare is operational.'),
            final('Cloudflare: cannot verify status. Check logs and routing.'),
        ])
        result = run_agent(question, provider, StatusTools(fail=True), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(question, provider.histories[2][-1]['content'])

    def test_historical_coverage_gets_a_review_not_an_assertion_of_absence(self):
        question = 'Find all GitHub Actions incidents in year 2024.'
        provider = FakeProvider([
            tool_call('status', 'get_status', '{"provider":"github","component":"Actions"}'),
            final('No incidents in 2024. https://www.githubstatus.com'),
            final('Cannot establish full coverage for 2024 from this recent window. '
                  'https://www.githubstatus.com'),
        ])
        result = run_agent(question, provider, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertIn('Cannot provide a complete incident list', result['answer'])
        self.assertTrue(any(event['event'] == 'validation_historical_gap'
                            for event in result['trace']))

    def test_historical_gap_finishes_before_repeating_unrelated_tool_calls(self):
        provider = FakeProvider([
            tool_call('status', 'get_status', '{"provider":"github","component":"Actions"}'),
            final('Trong năm 2025 có incident GitHub Actions; xem các sự cố 2026 như sau: '
                  '| Incident | 2026-11-05 |'),
        ])
        result = run_agent('Tìm danh sách đầy đủ incident GitHub Actions năm 2025.',
                           provider, StatusTools(), max_steps=8)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 2)
        self.assertIn('Không thể cung cấp danh sách đầy đủ', result['answer'])
        self.assertIn('https://www.githubstatus.com', result['answer'])

    def test_unrelated_recent_incidents_do_not_force_wrong_year_citations(self):
        tools = StatusTools()
        provider = FakeProvider([
            read_call('details', 'github', 't89g2q1800fr'),
            final('Không đủ dữ liệu trong cửa sổ gần đây để lập danh sách đầy đủ năm 2025.'),
        ])
        result = run_agent('Liệt kê đầy đủ incident GitHub năm 2025.',
                           provider, tools, max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(any(event['event'] == 'validation_missing_source'
                             for event in result['trace']))

    def test_historical_uncertainty_with_markdown_heading_is_not_a_false_claim(self):
        tools = StatusTools()
        evidence = _Evidence('Tôi cần danh sách đầy đủ tất cả incident GitHub Actions trong năm 2025.')
        evidence.observe('get_status', {'provider': 'github', 'component': 'Actions'},
                         tools.get_status('github', 'Actions'))
        draft = ('Không có bằng chứng nào về các sự cố GitHub Actions trong năm 2025 được thu thập. '
                 'Dữ liệu hiện có chỉ cho thấy các sự cố xảy ra trong năm 2026.\n\n'
                 '**Độ đầy đủ kết quả**: Chỉ có 50 sự cố gần nhất; không thể xác định '
                 'khả năng bao phủ đầy đủ cho năm 2025.')
        self.assertEqual(evidence.factual_issues(draft), [])
        cautious = ('Tôi không thể cung cấp danh sách đầy đủ các sự cố trong năm 2025 do dữ liệu '
                    'hiện tại không cho phép xác định liệu có sự cố nào xảy ra trong năm đó hay không. '
                    'Trong 50 sự cố gần nhất không có sự cố năm 2025 được tìm thấy. '
                    'Điều này không chứng minh rằng cả năm không có sự cố.')
        self.assertEqual(evidence.factual_issues(cautious), [])

    def test_repeated_unsupported_timeout_claim_uses_grounded_fallback(self):
        batch = tool_call('github', 'get_status', '{"provider":"github"}')
        other = tool_call('cloudflare', 'get_status', '{"provider":"cloudflare"}')
        batch['calls'] += other['calls']
        batch['message']['tool_calls'] += other['message']['tool_calls']
        unsafe = final('Cloudflare vẫn đang hoạt động bình thường.')
        provider = FakeProvider([batch, unsafe, unsafe, unsafe])
        from evals.scenarios import ScenarioTools
        result = run_agent('Kiểm tra GitHub và Cloudflare hiện tại.', provider,
                           ScenarioTools('cloudflare_timeout'), max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertIn('chưa xác minh', result['answer'])
        self.assertIn('github:', result['answer'])
        self.assertTrue(any(event['event'] == 'validation_safe_fallback'
                            for event in result['trace']))

    def test_status_summary_does_not_authorize_invented_incident_urls(self):
        provider = FakeProvider([
            tool_call('status', 'get_status', '{"provider":"github","component":"Actions"}'),
            final('Resolved. https://www.githubstatus.com/incidents/invented-evidence'),
            final('According to the status snapshot: operational. https://www.githubstatus.com'),
        ])
        result = run_agent('Check GitHub Actions.', provider, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(any(event['event'] == 'validation_unknown_source'
                            for event in result['trace']))

    def test_safe_timeout_answer_needs_no_additional_model_call(self):
        provider = FakeProvider([
            tool_call('status', 'get_status', '{"provider":"cloudflare"}'),
            final('Cloudflare: unknown; cannot verify status because the tool timed out.'),
        ])
        result = run_agent('Check Cloudflare.', provider, StatusTools(fail=True), max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 2)

    def test_refusing_to_assume_operational_is_not_an_operational_claim(self):
        provider = FakeProvider([
            tool_call('status', 'get_status', '{"provider":"cloudflare"}'),
            final('Cloudflare: chưa xác minh do timeout. Không khẳng định operational.'),
        ])
        result = run_agent('Check Cloudflare.', provider, StatusTools(fail=True), max_steps=2)
        self.assertEqual(result['status'], 'completed')

    def test_markdown_emphasis_does_not_trap_source_validation(self):
        incident = details('github', 'summary')
        provider = FakeProvider([
            read_call('details', 'github', 'summary'),
            final('Source: **' + incident['url'] + '**'),
        ])
        result = run_agent('Check source.', provider,
                           MemoryTools({('github', 'summary'): incident}), max_steps=2)
        self.assertEqual(result['status'], 'completed')

    def test_markdown_url_used_as_link_label_does_not_join_two_urls(self):
        incident = details('github', 'summary')
        url = incident['url']
        provider = FakeProvider([
            read_call('details', 'github', 'summary'),
            final('Source: [' + url + '](' + url + ')'),
        ])
        result = run_agent('Check source.', provider,
                           MemoryTools({('github', 'summary'): incident}), max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(any(event['event'] == 'validation_missing_source'
                             for event in result['trace']))


if __name__ == '__main__':
    unittest.main()
