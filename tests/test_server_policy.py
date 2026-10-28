import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServerPolicyTests(unittest.TestCase):
    def launch(self, changes=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model').mkdir()
            (root / 'model/config.json').write_text('{}')
            (root / 'key').write_text('test-only-server-policy-secret-123456\n')
            (root / 'validator.py').write_text('pass\n')
            (root / 'bin').mkdir()
            stub = root / 'bin/vllm'
            stub.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            stub.chmod(0o755)
            script = (ROOT / 'launch.sh').read_text()
            script = script.replace('/run/secrets/qwen_api_key', str(root / 'key'))
            script = script.replace('/opt/qwen/validate_model.py', str(root / 'validator.py'))
            script = script.replace('/model', str(root / 'model'))
            path = root / 'launch.sh'
            path.write_text(script)
            env = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                       MODEL_ID='test/model', MODEL_REVISION='a' * 40)
            for name in ['MAX_OUTPUT_TOKENS', 'MAX_NUM_QUEUED_REQS', 'ENABLE_THINKING',
                         'SSE_KEEP_ALIVE_INTERVAL','PREFIX_CACHING','EAGER','TEXT_ONLY']:
                env.pop(name, None)
            env.update(changes or {})
            return subprocess.run(['bash', str(path)], env=env, capture_output=True, text=True)

    def test_defaults_bound_output_and_queue_and_disable_thinking(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        def value(flag): return args[args.index(flag) + 1]
        self.assertEqual(json.loads(value('--override-generation-config')), {'max_new_tokens': 1024})
        self.assertEqual(value('--max-num-queued-reqs'), '8')
        self.assertEqual(json.loads(value('--default-chat-template-kwargs')), {'enable_thinking': False})
        self.assertEqual(value('--sse-keep-alive-interval'), '15')
        self.assertNotIn('test-only-server-policy-secret-123456', result.stdout)

    def test_operator_policy_changes_reach_server(self):
        result = self.launch({'MAX_OUTPUT_TOKENS': '2048', 'ENABLE_THINKING': '1'})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        self.assertEqual(json.loads(args[args.index('--override-generation-config') + 1]), {'max_new_tokens': 2048})
        self.assertEqual(json.loads(args[args.index('--default-chat-template-kwargs') + 1]), {'enable_thinking': True})

    def test_invalid_budgets_fail_before_server_exec(self):
        for env in [{'MAX_OUTPUT_TOKENS': '0'}, {'MAX_OUTPUT_TOKENS': '1,"other":2'},
                    {'MAX_NUM_QUEUED_REQS': '-1'}, {'ENABLE_THINKING': 'false'},
                    {'SSE_KEEP_ALIVE_INTERVAL': 'invalid'}]:
            with self.subTest(env=env):
                result = self.launch(env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_performance_flags_can_be_compared_without_editing_launcher(self):
        result=self.launch({'EAGER':'0','PREFIX_CACHING':'1'})
        self.assertEqual(result.returncode,0,result.stderr)
        args=json.loads(result.stdout)
        self.assertNotIn('--enforce-eager',args)
        self.assertIn('--enable-prefix-caching',args)
        self.assertNotIn('--no-enable-prefix-caching',args)
