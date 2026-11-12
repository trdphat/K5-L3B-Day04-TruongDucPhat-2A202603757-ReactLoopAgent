"""Giới hạn nhịp request và retry HTTP tạm thời cho cả agent lẫn judge."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import sys
import threading
import time
import urllib.error

QUOTA_CODES = {
    'insufficient_quota', 'credit_balance_exhausted', 'billing_hard_limit_reached',
    'organization_spend_limit_exceeded', 'project_spend_limit_exceeded',
    'organization_usage_limit_exceeded',
}


def error_payload(error):
    if not hasattr(error, '_lab_error_payload'):
        try:
            error._lab_error_payload = json.load(error)
        except (ValueError, TypeError, OSError, AttributeError):
            error._lab_error_payload = {}
    payload = error._lab_error_payload
    return payload if isinstance(payload, list) else [payload]


def error_diagnostic(error):
    """Chỉ lấy mã HTTP/code/type; bỏ message và thông tin tài khoản."""
    diagnostic = {'http_status': error.code}
    for item in error_payload(error):
        if not isinstance(item, dict):
            continue
        body = item.get('error', item)
        if not isinstance(body, dict):
            continue
        for name in ('code', 'type'):
            value = body.get(name)
            if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}', value):
                diagnostic[name] = value
    return diagnostic


def retry_delay(error, fallback):
    """Ưu tiên Retry-After/RetryInfo; quota bằng 0 không tự retry."""
    diagnostic = error_diagnostic(error)
    if diagnostic.get('code') in QUOTA_CODES or diagnostic.get('type') == 'insufficient_quota':
        return None
    header = error.headers.get('Retry-After') if error.headers else None
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            try:
                target = parsedate_to_datetime(header)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    # Chỉ dùng các trường thời gian/quota; không ghi response body ra log.
    try:
        entries = error_payload(error)
        delay = fallback
        for item in entries:
            if not isinstance(item, dict):
                continue
            detail_error = item.get('error', item)
            if not isinstance(detail_error, dict):
                continue
            for detail in detail_error.get('details', []):
                if not isinstance(detail, dict):
                    continue
                for violation in detail.get('violations', []):
                    if str(violation.get('quotaValue')) == '0':
                        return None
                value = detail.get('retryDelay')
                if isinstance(value, str) and value.endswith('s'):
                    delay = max(delay, float(value[:-1]))
        return delay
    except (ValueError, TypeError, OSError, AttributeError):
        return fallback


class ProviderRuntime:
    def __init__(self, interval=0, retries=0):
        self.interval, self.retries = interval, retries
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.attempts = 0
        self.retry_count = 0
        self.failures = []

    def complete(self, provider, messages, schemas):
        for attempt in range(self.retries + 1):
            with self.lock:
                remaining = self.next_request - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                self.next_request = time.monotonic() + self.interval
                self.attempts += 1
            try:
                return provider.complete(messages, schemas)
            except RuntimeError as exc:
                error = exc.__cause__
                if isinstance(error, urllib.error.HTTPError):
                    diagnostic = error_diagnostic(error)
                    with self.lock:
                        self.failures.append(diagnostic)
                    labels = ', '.join(f'{name}={diagnostic[name]}'
                                       for name in ('code', 'type') if name in diagnostic)
                    print(f'LLM API HTTP {error.code}' + (f' ({labels})' if labels else ''),
                          file=sys.stderr, flush=True)
                if (not isinstance(error, urllib.error.HTTPError) or
                        error.code not in (429, 500, 502, 503, 504) or
                        attempt == self.retries):
                    raise
                delay = retry_delay(error, 10 * (2 ** attempt))
                if delay is None or delay > 60:
                    raise  # Hạn mức không dùng được hoặc vượt budget chờ.
                with self.lock:
                    self.next_request = max(self.next_request, time.monotonic() + delay)
                    self.retry_count += 1
                print(f'HTTP {error.code}: chờ {delay:g}s trước lần thử {attempt + 2}.',
                      file=sys.stderr, flush=True)


class ManagedProvider:
    def __init__(self, provider, runtime):
        self.provider, self.runtime = provider, runtime

    def complete(self, messages, schemas):
        return self.runtime.complete(self.provider, messages, schemas)
