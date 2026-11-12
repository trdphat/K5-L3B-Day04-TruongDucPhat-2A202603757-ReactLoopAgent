"""Kiểm tra nạp key từ file và retry HTTP mà không dùng mạng hay chờ thật."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
from provider_runtime import ProviderRuntime, retry_delay
from runtime_config import load_env
from save_lab_config import save_config


def http_failure(code=429, headers=None, body=None):
    stream = io.BytesIO(json.dumps(body or {}).encode())
    http = urllib.error.HTTPError('https://example.invalid', code, 'HTTP error', headers or {}, stream)
    failure = RuntimeError(f'HTTP {code}')
    failure.__cause__ = http
    return failure


class ConfigurationTests(unittest.TestCase):
    def test_export_quotes_comments_and_file_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = Path(directory) / '.env'
            config.write_text('export PROVIDER=gemini\nMODEL="model-name" # comment\n'
                              "GEMINI_API_KEY='test-value'\nPATH=ignored\n", encoding='utf-8')
            os.environ['MODEL'] = 'old-value'
            self.assertTrue(load_env(config))
            self.assertEqual(os.environ['MODEL'], 'model-name')
            self.assertEqual(os.environ['GEMINI_API_KEY'], 'test-value')
            self.assertNotIn('PATH', os.environ)

    def test_malformed_file_does_not_partially_update_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = Path(directory) / '.env'
            config.write_text('MODEL=new-model\nGEMINI_API_KEY="private-value\n', encoding='utf-8')
            with self.assertRaises(ValueError) as error:
                load_env(config)
            self.assertNotIn('private-value', str(error.exception))
            self.assertNotIn('MODEL', os.environ)

    def test_save_from_cmd_round_trips_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {'GEMINI_API_KEY': 'test-value'}, clear=True):
            config = Path(directory) / '.env'
            save_config('gemini', 'test-model', config)
            del os.environ['GEMINI_API_KEY']
            self.assertTrue(load_env(config))
            self.assertEqual(os.environ['GEMINI_API_KEY'], 'test-value')
            self.assertEqual(os.environ['PROVIDER'], 'gemini')

    def test_missing_key_does_not_write_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = Path(directory) / '.env'
            with self.assertRaises(ValueError):
                save_config('gemini', 'test-model', config)
            self.assertFalse(config.exists())


class ProviderRetryTests(unittest.TestCase):
    def test_retry_after_then_success(self):
        failure = http_failure(headers={'Retry-After': '3'})
        provider = Mock()
        provider.complete.side_effect = [failure, {'finish': 'stop'}]
        runtime = ProviderRuntime(retries=2)
        with patch('provider_runtime.time.monotonic', return_value=100), \
                patch('provider_runtime.time.sleep') as sleep, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runtime.complete(provider, [], []), {'finish': 'stop'})
        sleep.assert_called_once_with(3)
        self.assertEqual(runtime.attempts, 2)
        self.assertEqual(runtime.retry_count, 1)
        failure.__cause__.close()

    def test_persistent_errors_have_bounded_attempts(self):
        failures = [http_failure() for _ in range(4)]
        provider = Mock()
        provider.complete.side_effect = failures
        runtime = ProviderRuntime(retries=2)
        with patch('provider_runtime.time.monotonic', return_value=100), \
                patch('provider_runtime.time.sleep'), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                runtime.complete(provider, [], [])
        self.assertEqual(provider.complete.call_count, 3)
        for failure in failures:
            failure.__cause__.close()

    def test_authentication_errors_are_not_retried(self):
        failure = http_failure(401)
        provider = Mock()
        provider.complete.side_effect = failure
        with self.assertRaises(RuntimeError):
            ProviderRuntime(retries=3).complete(provider, [], [])
        self.assertEqual(provider.complete.call_count, 1)
        failure.__cause__.close()

    def test_zero_quota_and_wait_above_budget_are_not_retried(self):
        quota = {'error': {'details': [{'violations': [{'quotaValue': '0'}]}]}}
        failures = [http_failure(body=quota), http_failure(headers={'Retry-After': '120'})]
        for failure in failures:
            with self.subTest(headers=failure.__cause__.headers):
                provider = Mock()
                provider.complete.side_effect = failure
                with self.assertRaises(RuntimeError):
                    ProviderRuntime(retries=3).complete(provider, [], [])
                self.assertEqual(provider.complete.call_count, 1)
                failure.__cause__.close()

    def test_google_retry_info_with_list_response(self):
        payload = [{'error': {'details': [{'retryDelay': '14.5s'}]}}]
        failure = http_failure(body=payload)
        self.assertEqual(retry_delay(failure.__cause__, 10), 14.5)
        failure.__cause__.close()

    def test_billing_quota_errors_are_not_retried(self):
        for code in ('insufficient_quota', 'credit_balance_exhausted',
                     'project_spend_limit_exceeded', 'organization_usage_limit_exceeded'):
            with self.subTest(code=code):
                failure = http_failure(body={'error': {'code': code, 'type': 'insufficient_quota'}})
                provider = Mock()
                provider.complete.side_effect = failure
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(RuntimeError):
                    ProviderRuntime(retries=3).complete(provider, [], [])
                self.assertEqual(provider.complete.call_count, 1)
                failure.__cause__.close()

    def test_http_diagnostics_do_not_expose_response_message(self):
        failure = http_failure(body={'error': {
            'code': 'rate_limit_exceeded', 'type': 'rate_limit_error',
            'message': 'private-value', 'project': 'private-project',
        }})
        provider = Mock()
        provider.complete.side_effect = failure
        runtime = ProviderRuntime()
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(RuntimeError):
            runtime.complete(provider, [], [])
        self.assertEqual(runtime.failures, [{
            'http_status': 429, 'code': 'rate_limit_exceeded', 'type': 'rate_limit_error',
        }])
        self.assertIn('HTTP 429', output.getvalue())
        self.assertNotIn('private-value', output.getvalue() + json.dumps(runtime.failures))
        self.assertNotIn('private-project', output.getvalue() + json.dumps(runtime.failures))
        failure.__cause__.close()


if __name__ == '__main__':
    unittest.main()
