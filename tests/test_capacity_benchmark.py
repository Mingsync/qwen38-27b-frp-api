import unittest

from capacity_benchmark import generation_overlap, parse_metrics, summarize_telemetry


class CapacityBenchmarkTests(unittest.TestCase):
    def test_generation_overlap_excludes_waiting_and_failed_requests(self):
        results = [
            {'first_answer_at': 10, 'finished_at': 15},
            {'first_answer_at': 12, 'finished_at': 17},
            {'first_answer_at': 17, 'finished_at': 19},
            {'error': 'HTTPError', 'status': 503},
        ]
        self.assertEqual(generation_overlap(results), 2)
        self.assertEqual(generation_overlap([]), 0)

    def test_metrics_sum_ranks_and_ignore_similar_histogram_names(self):
        metrics = parse_metrics(
            'vllm:num_requests_running{engine="0"} 2\n'
            'vllm:num_requests_running{engine="1"} 3\n'
            'vllm:num_requests_running_created 999\n'
            'vllm:kv_cache_usage_perc{engine="0"} 5e-1\n')
        self.assertEqual(metrics['vllm:num_requests_running'], 5)
        self.assertEqual(metrics['vllm:kv_cache_usage_perc'], 0.5)
        self.assertEqual(len(metrics), 2)

    def test_missing_telemetry_is_not_reported_as_zero_usage(self):
        summary = summarize_telemetry([{'time': 1}])
        self.assertIsNone(summary['peak_running'])
        self.assertIsNone(summary['peak_kv_usage_fraction'])
        self.assertIsNone(summary['preemptions_delta'])

    def test_preemption_counter_reset_invalidates_delta(self):
        samples = [{'metrics': {'vllm:num_preemptions_total': count}}
                   for count in [10, 12, 0, 2]]
        summary = summarize_telemetry(samples)
        self.assertTrue(summary['preemption_counter_reset'])
        self.assertIsNone(summary['preemptions_delta'])

    def test_pressure_peaks_are_per_gpu_and_counter_delta_is_bounded(self):
        samples = [
            {'metrics': {'vllm:num_preemptions_total': 5}, 'gpus': [[100, 40], [120, 42]]},
            {'metrics': {'vllm:num_preemptions_total': 7}, 'gpus': [[130, 41], [125, 39]]},
        ]
        summary = summarize_telemetry(samples)
        self.assertEqual(summary['preemptions_delta'], 2)
        self.assertEqual(summary['peak_gpu_memory_mib'], [130, 125])
        self.assertEqual(summary['peak_gpu_temperature_c'], [41, 42])
