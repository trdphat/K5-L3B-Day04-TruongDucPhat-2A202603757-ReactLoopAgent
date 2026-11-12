"""Nạp cấu hình lab từ .env; không chạy shell hoặc ghi API key vào log."""
import os
from pathlib import Path
import re
import shlex

ENV_PATH = Path(__file__).resolve().parent / '.env'
ALLOWED_NAMES = {
    'PROVIDER', 'MODEL', 'JUDGE_PROVIDER', 'JUDGE_MODEL', 'STATUS_USER_AGENT',
    'OPENAI_API_KEY', 'GEMINI_API_KEY', 'ANTHROPIC_API_KEY', 'OPENROUTER_API_KEY',
}


def load_env(path=ENV_PATH):
    """Đọc NAME=value hoặc export NAME=value; file được ưu tiên hơn env cũ."""
    path = Path(path)
    if not path.is_file():
        return False
    settings = {}
    for line_number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*', line)
        if not match:
            raise ValueError(f'Cấu hình .env dòng {line_number} không hợp lệ.')
        name, raw_value = match.groups()
        if name not in ALLOWED_NAMES:
            continue
        try:
            tokens = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f'Dấu nháy trong .env dòng {line_number} không hợp lệ.') from None
        if len(tokens) > 1:
            raise ValueError(f'Giá trị trong .env dòng {line_number} cần đặt trong dấu nháy.')
        settings[name] = tokens[0] if tokens else ''
    os.environ.update(settings)
    return True
