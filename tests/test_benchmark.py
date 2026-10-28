import io
import unittest
from benchmark import consume_sse, percentile


class BenchmarkTests(unittest.TestCase):
    def test_heartbeats_and_reasoning_are_not_first_answer_tokens(self):
        stream=io.BytesIO(b': keep-alive\n\ndata: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            b'data: {"choices":[{"delta":{"reasoning":"thinking"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":2}}\n\n'
            b'data: [DONE]\n\n')
        result=consume_sse(stream,10,clock=iter([12,15]).__next__)
        self.assertEqual(result,{'ttft_seconds':2,'elapsed_seconds':5,'completion_tokens':2,'prompt_tokens':8})

    def test_missing_usage_is_not_fabricated(self):
        stream=io.BytesIO(b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n')
        self.assertIsNone(consume_sse(stream,0,clock=lambda:1)['completion_tokens'])

    def test_empty_answer_is_a_failed_generation(self):
        with self.assertRaises(ValueError):
            consume_sse(io.BytesIO(b': heartbeat\ndata: [DONE]\n'),0)

    def test_percentiles_include_queued_latency(self):
        self.assertEqual(percentile([1,2,3,4],.5),2.5)
        self.assertAlmostEqual(percentile([1,2,3,4],.95),3.85)

    def test_partial_or_errored_stream_is_not_counted_as_success(self):
        for tail in [b'',b'data: {"error":{"message":"fixture failure"}}\ndata: [DONE]\n']:
            stream=io.BytesIO(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'+tail)
            with self.assertRaises(ValueError):
                consume_sse(stream,0)
