"""Chạy và chấm eval. expected chỉ cung cấp cho judge, không gửi cho agent."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from providers import Provider
from scenarios import ScenarioTools
from grading import grade_case, summarize, bonus_points
from bonus.tools import BonusTools
from runtime_config import load_env
from provider_runtime import ManagedProvider, ProviderRuntime


def make_provider(name, model, args):
    provider = Provider(name, model)
    runtime = getattr(args, 'provider_runtime', None)
    return ManagedProvider(provider, runtime) if runtime is not None else provider


def run_case(case, args):
    try:
        agent_runner = run_agent
        if getattr(args, 'agent_version', 'after') == 'before':
            from agent_before import run_agent as agent_runner
        result = agent_runner(case['question'], make_provider(args.provider, args.model, args),
                           (BonusTools('snapshot', scenario=case.get('scenario', 'normal')) if getattr(args, 'bonus', False) else ScenarioTools(case.get('scenario', 'normal'), case.get('fail', False))), max_steps=args.max_steps)
    except Exception as exc:
        result = {'status': 'error', 'answer': '', 'trace': [], 'usage': [], 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}
    if not isinstance(result, dict) or not isinstance(result.get('trace', []), list):
        result = {'status': 'error', 'answer': '', 'trace': [], 'usage': [], 'error': 'run_agent trả kết quả không hợp lệ.'}
    try:
        judge = make_provider(args.judge_provider, args.judge_model, args)
        grade = grade_case(case, result, judge)
    except Exception as exc:
        grade = {'score': None, 'status': 'unscored', 'reason': str(exc), 'judge_usage': {}}
    return {'id': case['id'], 'question': case['question'], 'expected': case['expected'],
            'scenario': case.get('scenario', 'normal'), 'provider': args.provider, 'model': args.model,
            'judge_provider': args.judge_provider, 'judge_model': args.judge_model, 'result': result, 'grade': grade}


def eval_config(args):
    return {'provider': args.provider, 'model': args.model,
            'judge_provider': args.judge_provider, 'judge_model': args.judge_model,
            'max_steps': args.max_steps, 'dataset': 'bonus' if args.bonus else 'core',
            'request_interval': args.request_interval, 'http_retries': args.http_retries}


def resumable_report(path, config, agent, cases):
    """Resume infrastructure/judge failures only, never retry a scored low/zero case."""
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    if report.get('config') != config or report.get('agent') != agent:
        raise ValueError('Resume cần đúng config và SHA-256 agent của report gốc.')
    expected = {case['id']: case for case in cases}
    items = report.get('cases', [])
    if len(items) != len(expected) or {item['id'] for item in items} != set(expected):
        raise ValueError('Resume cần report đầy đủ các case riêng biệt của dataset.')
    for item in items:
        original = expected[item['id']]
        if any(item.get(key) != original.get(key) for key in ('question', 'expected')):
            raise ValueError('Dataset khác report gốc; không resume trộn các lần chạy.')
        grade = item['grade']
        if (grade.get('status') == 'unscored') != (grade.get('score') is None):
            raise ValueError('Trạng thái chấm trong report không nhất quán.')
    return report


def main():
    try:
        load_env()
    except ValueError as exc:
        print(f'Lỗi: {exc}', file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description='Chạy agent và chấm eval 0–100')
    choices = ['openai', 'gemini', 'anthropic', 'openrouter']
    parser.add_argument('--provider', choices=choices, default=os.getenv('PROVIDER', 'openai'))
    parser.add_argument('--model', default=os.getenv('MODEL'))
    parser.add_argument('--judge-provider', choices=choices, default=os.getenv('JUDGE_PROVIDER'))
    parser.add_argument('--judge-model', default=os.getenv('JUDGE_MODEL'))
    parser.add_argument('--bonus', action='store_true', help='Chạy bộ bonus riêng; không ảnh hưởng điểm chính')
    parser.add_argument('--case', help='Ví dụ E2 hoặc H1; bỏ trống để chạy toàn bộ')
    parser.add_argument('--max-steps', type=int, default=None, help='Giới hạn model calls mỗi case: mặc định 8, bonus 16')
    parser.add_argument('--workers', type=int, default=1, help='Số case chạy đồng thời (mặc định 1)')
    parser.add_argument('--output', default='eval_results.json')
    parser.add_argument('--resume', action='store_true',
                        help='Chỉ chạy lại unscored của report --output, giữ nguyên các case đã chấm')
    parser.add_argument('--agent-version', choices=['before', 'after'], default='after',
                        help='before: bản lưu trước cải tiến; after: agent hiện tại')
    parser.add_argument('--request-interval', type=float, default=5,
                        help='Khoảng cách tối thiểu giữa request LLM, mặc định 5 giây')
    parser.add_argument('--http-retries', type=int, default=3,
                        help='Mặc định thử lại tối đa 3 lần cho HTTP 429/5xx, chờ tối đa 60s/lần')
    args = parser.parse_args()
    args.judge_provider = args.judge_provider or args.provider
    args.judge_model = args.judge_model or args.model
    if not args.model or not args.judge_model:
        parser.error('Thiếu --model hoặc MODEL')
    args.max_steps = args.max_steps if args.max_steps is not None else (16 if args.bonus else 8)
    if args.max_steps < 1 or not 1 <= args.workers <= 8:
        parser.error('--max-steps phải >= 1 và --workers phải từ 1 đến 8')
    if not 0 <= args.request_interval <= 60 or not 0 <= args.http_retries <= 3:
        parser.error('--request-interval phải từ 0 đến 60; --http-retries từ 0 đến 3')
    args.provider_runtime = ProviderRuntime(args.request_interval, args.http_retries)
    cases_path = (Path(__file__).resolve().parents[1] / 'bonus' / 'cases.json') if args.bonus else (Path(__file__).parent / 'cases.json')
    cases = json.loads(cases_path.read_text(encoding='utf-8'))
    selected = [c for c in cases if not args.case or c['id'] == args.case.upper()]
    if not selected:
        parser.error('Không tìm thấy case')
    if args.resume and args.case:
        parser.error('--resume dùng toàn bộ dataset, không kết hợp --case')
    agent_file = Path(__file__).resolve().parents[1] / ('agent_before.py' if args.agent_version == 'before' else 'agent.py')
    agent = {'version': args.agent_version, 'file': agent_file.name,
             'sha256': hashlib.sha256(agent_file.read_bytes()).hexdigest()}
    config = eval_config(args)
    previous = None
    to_run = selected
    if args.resume:
        try:
            previous = resumable_report(args.output, config, agent, cases)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f'Không thể resume: {exc}', file=sys.stderr)
            return 1
        unscored = {item['id'] for item in previous['cases']
                    if item['grade']['status'] == 'unscored'}
        to_run = [case for case in selected if case['id'] in unscored]
        if not to_run:
            print('Report đã chấm đầy đủ; không chạy lại hoặc thay đổi các điểm đã chấm.')
            return 0
    def run_and_print(case):
        print(f"Đang chạy {case['id']}", flush=True)
        item = run_case(case, args)
        print(f"{case['id']}: {item['grade'].get('score')} ({item['grade']['status']})", flush=True)
        return item
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_and_print, to_run))
    if previous:
        by_id = {item['id']: item for item in previous['cases']}
        by_id.update({item['id']: item for item in results})
        results = [by_id[case['id']] for case in selected]
    report = {'summary': summarize(results), 'config': config, 'cases': results, 'agent': agent}
    report['transport'] = {'http_attempts': args.provider_runtime.attempts,
                           'http_retries': args.provider_runtime.retry_count,
                           'http_errors': args.provider_runtime.failures}
    if previous:
        report['resume_history'] = previous.get('resume_history', []) + [{
            'rerun_case_ids': [case['id'] for case in to_run],
            'previous_summary': previous['summary'],
            'previous_unscored_cases': [item for item in previous['cases']
                                        if item['grade']['status'] == 'unscored'],
        }]
        old_transport = previous.get('transport', {})
        for key in ('http_attempts', 'http_retries'):
            report['transport'][key] += old_transport.get(key, 0)
        report['transport']['http_errors'] = old_transport.get('http_errors', []) + report['transport']['http_errors']
    summary = report['summary']
    if args.bonus:
        full_run = not args.case and len(selected) == len(cases)
        report['summary']['bonus_points_0_15'] = bonus_points(summary['score_0_100'], full_run)
        print(f"Điểm bonus tham khảo: {summary['bonus_points_0_15']}/15 (chỉ tính khi chấm đủ toàn bộ case).")
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Điểm: {summary['score_0_100']}/100; đã chấm {summary['scored_cases']}/{summary['total_cases']} case. File: {args.output}")
    if summary['score_0_100'] is None:
        print('Chưa có điểm tổng do có case chưa được chấm; xem reason trong file.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
