import json
from pathlib import Path
import unittest

from benchmark import percentile
from capacity_benchmark import generation_overlap


class CapacityResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'capacity-results.json'
        cls.measurements = json.loads(path.read_text())['measurements']

    def test_recorded_counts_match_individual_streams(self):
        names = [row['name'] for row in self.measurements]
        self.assertEqual(len(names), len(set(names)))
        for row in self.measurements:
            with self.subTest(name=row['name']):
                passed = [item for item in row['results'] if 'error' not in item]
                self.assertEqual(row['requests'], len(row['results']))
                self.assertEqual(row['successful'], len(passed))
                self.assertEqual(row['failed'], row['requests'] - len(passed))
                self.assertEqual(row['marker_checks_passed'],
                                 sum(item['marker_check'] for item in passed))
                self.assertEqual(row['max_generation_overlap'],
                                 generation_overlap(row['results']))

    def test_usage_respects_recorded_prompt_output_and_context_budgets(self):
        for row in self.measurements:
            for item in row['results']:
                if 'error' in item:
                    continue
                with self.subTest(name=row['name']):
                    self.assertEqual(item['prompt_tokens'], row['prompt_tokens_expected'])
                    if row['force_length']:
                        self.assertEqual(item['completion_tokens'], row['output_budget'])
                    self.assertLessEqual(item['prompt_tokens'] + item['completion_tokens'],
                                         int(row['server_profile']['MAX_MODEL_LEN']))

    def test_percentiles_and_throughput_recompute_from_recorded_streams(self):
        for row in self.measurements:
            passed = [item for item in row['results'] if 'error' not in item]
            with self.subTest(name=row['name']):
                for field in ['ttft_seconds', 'elapsed_seconds']:
                    values = [item[field] for item in passed]
                    for label, fraction in [('p50', 0.5), ('p95', 0.95)]:
                        self.assertAlmostEqual(row[field][label], percentile(values, fraction))
                self.assertAlmostEqual(row['aggregate_output_tokens_per_second'],
                    sum(item['completion_tokens'] for item in passed) / row['wall_seconds'])
