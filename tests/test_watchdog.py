import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('watchdog', Path(__file__).resolve().parents[1]/'ops/watchdog.py')
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.policy = SimpleNamespace(failures=3, cooldown=1800, window=3600, max_restarts=2)
        self.unhealthy = {'Status': 'running', 'Health': {'Status': 'unhealthy'}}

    def test_stopped_and_starting_are_never_restarted(self):
        for state in [{'Status':'exited'}, {'Status':'running','Health':{'Status':'starting'}},
                      {'Status':'running','Health':{'Status':'healthy'}}]:
            action, result = watchdog.decide({'failures':10}, state, 5000, self.policy)
            self.assertTrue(action.startswith('skip'))
            self.assertEqual(result['failures'], 0)

    def test_requires_consecutive_failures(self):
        state = {}
        for expected in ['observe','observe','restart']:
            action, state = watchdog.decide(state, self.unhealthy, 5000, self.policy)
            self.assertEqual(action, expected)

    def test_cooldown_prevents_repeated_restarts(self):
        action, _ = watchdog.decide({'failures':3,'attempts':[4900]}, self.unhealthy, 5000, self.policy)
        self.assertEqual(action, 'cooldown')

    def test_hourly_budget_is_a_hard_stop(self):
        action, _ = watchdog.decide({'failures':3,'attempts':[2000,4000]}, self.unhealthy, 5000, self.policy)
        self.assertEqual(action, 'alert-budget-exhausted')

    def test_old_attempts_expire_without_resetting_recent_budget(self):
        action, state = watchdog.decide({'failures':3,'attempts':[100,2000]}, self.unhealthy, 5000, self.policy)
        self.assertEqual(action, 'restart')
        self.assertEqual(state['attempts'], [2000])

    def test_maintenance_marker_prevents_all_docker_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'disabled').touch()
            with patch('sys.argv',['watchdog','--state-dir',directory,'--restart']), \
                    patch.object(watchdog.subprocess,'check_output') as docker:
                self.assertEqual(watchdog.main(),0)
                docker.assert_not_called()

    def test_cli_dry_run_does_not_mutate_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            path.write_text(json.dumps({'container':'fixture','failures':2}))
            with patch('sys.argv',['watchdog','--state-dir',directory]), \
                    patch.object(watchdog.subprocess,'check_output',side_effect=['fixture\n',json.dumps(self.unhealthy)]), \
                    patch.object(watchdog.subprocess,'run') as restart:
                self.assertEqual(watchdog.main(),0)
                restart.assert_not_called()
                self.assertEqual(json.loads(path.read_text())['attempts'],[])
