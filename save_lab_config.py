"""Lưu cấu hình từ đúng CMD đang giữ key vào .env, không in key."""
import argparse
import os
from pathlib import Path
import shlex
import sys

from providers import CONFIG
from runtime_config import ENV_PATH


def save_config(provider, model, path=ENV_PATH):
    if provider not in CONFIG or not model or model.startswith(('YOUR_', 'TEN_MODEL_')):
        raise ValueError('Cần provider và tên model hợp lệ.')
    key_name = CONFIG[provider][0]
    key = os.environ.get(key_name, '')
    if not key or key.startswith(('YOUR_', 'API_KEY_CUA_BAN', '<')):
        raise ValueError(f'Chưa có {key_name} trong cửa sổ CMD này.')
    settings = {'PROVIDER': provider, 'MODEL': model, key_name: key}
    if any('\n' in value or '\r' in value for value in settings.values()):
        raise ValueError('Giá trị cấu hình không được chứa ký tự xuống dòng.')
    content = '\n'.join(f'{name}={shlex.quote(value)}' for name, value in settings.items())
    Path(path).write_text(content + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=CONFIG, default=os.getenv('PROVIDER', 'gemini'))
    parser.add_argument('--model', default=os.getenv('MODEL', 'gemini-3.5-flash'))
    args = parser.parse_args()
    try:
        save_config(args.provider, args.model)
    except (ValueError, OSError) as exc:
        print(f'Không lưu được cấu hình: {exc}', file=sys.stderr)
        return 1
    print(f'Đã lưu .env: provider={args.provider}, model={args.model}. API key được ẩn.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
