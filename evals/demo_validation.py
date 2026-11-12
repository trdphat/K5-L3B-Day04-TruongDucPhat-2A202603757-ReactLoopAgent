"""Demo offline kiểm tra nguồn: model giả lập, StatusTools dùng snapshot thật."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from agent_before import run_agent as run_before
from status_tools import StatusTools


class ScriptedProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, schemas):
        self.calls += 1
        if self.calls == 1:
            arguments = json.dumps({'provider': 'github', 'incident_id': 'xr3myzj7ms51'})
            call = {'id': 'demo-details', 'name': 'get_incident_details', 'arguments': arguments}
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': call['id'], 'type': 'function',
                'function': {'name': call['name'], 'arguments': arguments},
            }]}
            return {'message': message, 'calls': [call], 'finish': 'tool_calls', 'usage': {}}
        answer = 'Incident đã xử lý.'
        if self.calls > 2:
            observation = next(json.loads(message['content']) for message in reversed(messages)
                               if message['role'] == 'tool')
            answer += ' Nguồn: ' + observation['url']
        return {'message': {'role': 'assistant', 'content': answer},
                'calls': [], 'finish': 'stop', 'usage': {}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='validation_demo.json')
    args = parser.parse_args()
    question = 'Xem incident GitHub xr3myzj7ms51, cho biết trạng thái và nguồn.'
    report = {'mode': 'offline_scripted_provider', 'uses_real_llm': False,
              'question': question, 'max_steps': 4,
              'before': run_before(question, ScriptedProvider(), StatusTools(), max_steps=4),
              'after': run_agent(question, ScriptedProvider(), StatusTools(), max_steps=4)}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
    for version in ('before', 'after'):
        result = report[version]
        calls = sum(event['event'] == 'model' for event in result['trace'])
        print(f"{version}: {result['status']}; {calls} model calls; {result['answer']}")
    print(f'Demo offline, không phải điểm eval LLM. File: {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
