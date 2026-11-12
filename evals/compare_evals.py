"""So sánh hai lần eval đầy đủ, cùng cấu hình; không sửa điểm hay kết quả."""
import argparse
import json
from pathlib import Path
import sys


def token_count(usage):
    if type(usage.get('total_tokens')) is int:
        return usage['total_tokens']
    incoming = usage.get('input_tokens', usage.get('prompt_tokens'))
    outgoing = usage.get('output_tokens', usage.get('completion_tokens'))
    if type(incoming) is int and type(outgoing) is int:
        return incoming + outgoing
    return None


def metrics(report):
    traces = [event for case in report['cases'] for event in case['result']['trace']]
    agent_usage = [usage for case in report['cases'] for usage in case['result']['usage']]
    judge_usage = [case['grade'].get('judge_usage', {}) for case in report['cases']
                   if case['result']['status'] == 'completed']

    def total_tokens(records):
        values = [token_count(usage) for usage in records]
        return sum(values) if all(value is not None for value in values) else None

    all_usage = agent_usage + judge_usage
    costs = [usage.get('cost') for usage in all_usage]
    cost = (sum(costs) if costs and all(type(value) in (int, float) for value in costs)
            else None)
    return {
        'model_calls': sum(event['event'] == 'model' or
                           (event['event'] == 'error' and event.get('error') == 'PROVIDER_ERROR')
                           for event in traces),
        'tool_calls': sum(event['event'] == 'tool' for event in traces),
        'revisions': sum(event['event'] == 'validation_missing_source' for event in traces),
        'agent_tokens': total_tokens(agent_usage),
        'judge_tokens': total_tokens(judge_usage),
        'reported_cost': cost,
    }


def compare(before, after):
    if before['config'] != after['config']:
        raise ValueError('Hai lần eval khác config; cần giữ cùng provider/model/max_steps/dataset.')
    if before['config'].get('dataset') != 'core':
        raise ValueError('Script này so sánh bộ bài chính, không phải bonus.')
    expected_ids = {case['id'] for case in json.loads(
        (Path(__file__).parent / 'cases.json').read_text(encoding='utf-8'))}
    for label, report in (('before', before), ('after', after)):
        ids = [case['id'] for case in report['cases']]
        if len(ids) != len(expected_ids) or set(ids) != expected_ids:
            raise ValueError(f'{label} chưa có đủ {len(expected_ids)} case riêng biệt.')
        if (report['summary'].get('score_0_100') is None or
                any(case['grade'].get('score') is None or
                    case['grade'].get('status') != 'scored' for case in report['cases'])):
            raise ValueError(f'{label} còn case unscored; không báo điểm tổng so sánh.')

    previous, current = metrics(before), metrics(after)
    lines = ['# So sánh eval trước và sau cải tiến', '',
             'Cùng cấu hình, cùng bộ case bài chính. Điểm judge là ước lượng.', '',
             '| Chỉ số | Trước | Sau |', '|---|---:|---:|',
             f"| Điểm /100 | {before['summary']['score_0_100']} | {after['summary']['score_0_100']} |"]
    for key, label in (
        ('model_calls', 'Model calls của agent'), ('tool_calls', 'Tool calls'),
        ('revisions', 'Lần yêu cầu sửa nguồn'), ('agent_tokens', 'Token agent'),
        ('judge_tokens', 'Token judge'), ('reported_cost', 'Chi phí provider trả, không quy đổi'),
    ):
        values = [report[key] if report[key] is not None else 'Không được cung cấp'
                  for report in (previous, current)]
        lines.append(f'| {label} | {values[0]} | {values[1]} |')
    lines += ['', '## Từng case', '',
              '| Case | Trước | Sau | Chênh lệch | Trạng thái trước → sau |',
              '|---|---:|---:|---:|---|']
    by_id = {case['id']: case for case in after['cases']}
    for case in before['cases']:
        other = by_id[case['id']]
        old_score, new_score = case['grade']['score'], other['grade']['score']
        status = case['result']['status'] + ' → ' + other['result']['status']
        lines.append(f"| {case['id']} | {old_score} | {new_score} | {new_score - old_score:+} | {status} |")
    lines += ['', 'Xem `grade.reason`, `grade.improvement`, `grade.mechanical` và trace trong '
              'hai file JSON để xác định lỗi đã sửa hoặc còn lại. Một lần chạy mỗi '
              'phiên bản chưa chứng minh tính ổn định.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', default='eval_before.json')
    parser.add_argument('--after', default='eval_after.json')
    parser.add_argument('--output', default='eval_comparison.md')
    args = parser.parse_args()
    try:
        before = json.loads(Path(args.before).read_text(encoding='utf-8'))
        after = json.loads(Path(args.after).read_text(encoding='utf-8'))
        output = compare(before, after)
        Path(args.output).write_text(output, encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Không thể so sánh: {exc}', file=sys.stderr)
        return 1
    print(f'Đã ghi so sánh vào {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
