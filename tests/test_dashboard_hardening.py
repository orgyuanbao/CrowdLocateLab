"""Malformed saved-data regressions; no research data, models or solver calls."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

import dashboard
import dashboard_revision


class DashboardHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / 'runs/revision_reference'
        self.results = self.root / 'results/revision_benchmark'
        self.data = dashboard_revision.RevisionDashboardData(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf8')
        return path

    def test_nonobject_and_nested_invalid_summaries_remain_unavailable(self):
        for value in ([], ['partial'], None, 7, 'partial', {},
                      {'metrics': None, 'protocol': None, 'aliases': []},
                      {'metrics': {'unfinished': None}, 'conformal': ['partial']}):
            with self.subTest(value=value):
                self.write(self.results / 'summary.json', value)
                state = self.data.state('v3')
                self.assertEqual(state['benchmark']['status'], 'unavailable')
                self.assertEqual(state['benchmark']['metrics'], {})
                self.assertIsNone(state['benchmark']['prediction_count'])
                self.assertFalse(self.data.calibration('v3')['available'])

    def test_nonfinite_duplicate_and_truncated_json_cannot_be_successful_metrics(self):
        path = self.write(self.results / 'summary.json', {})
        for text in ('{"metrics":', '{"metrics":{"m":{"main":{"overall":{"mean_m":NaN}}}}}',
                     '{"metrics":{"m":{"main":{"overall":{"mean_m":1e999}}}}}',
                     '{"metrics":{},"metrics":{"m":{"main":{"overall":{"mean_m":0}}}}}'):
            with self.subTest(text=text):
                path.write_text(text, encoding='utf8')
                state = self.data.state('v3')
                self.assertEqual(state['benchmark']['status'], 'unavailable')
                self.assertEqual(state['benchmark']['metrics'], {})

    def test_malformed_calibration_containers_do_not_become_zero_coverage(self):
        for value in ([], {'results': []}, {'global': {'results': []}},
                      {'mondrian': {'0.1': {'populations': []}}},
                      {'global': {'0.1': {'alpha': [], 'populations': {'iid_test': {'n': 3}}}}}):
            with self.subTest(value=value):
                self.write(self.results / 'conformal.json', value)
                calibration = self.data.calibration('v3')
                self.assertFalse(calibration['available'])
                self.assertEqual(calibration['rows'], [])

    def test_malformed_archive_containers_recover_after_complete_replacement(self):
        self.write(self.results / 'main_inputs.json', {'cases': None})
        self.write(self.results / 'main_selected.json', {'rows': None})
        first = self.data.cases('v3')
        self.assertEqual(first['rows'], [])
        self.assertTrue(first['warnings'])
        self.write(self.results / 'main_inputs.json', [{'case_id': 'fixture', 'anchors': None,
                    'scenario': {}, 'meta': None}])
        self.write(self.results / 'main_selected.json', {'rows': [{'case_id': 'fixture',
                    'scenario': [], 'error_m': None, 'diagnostics': None, 'observation_n': []}]})
        result = self.data.cases('v3')
        self.assertEqual(result['total'], 1)
        self.assertIsNone(result['rows'][0]['error_m'])
        self.assertIsNone(result['rows'][0]['observation_n'])
        self.assertEqual(result['rows'][0]['selected_model'], 'unknown')
        self.assertFalse(self.data.boundary('v3')['available'])

    def test_invalid_events_do_not_count_as_successful_model_calls(self):
        path = self.run / 'events.jsonl'
        path.parent.mkdir(parents=True)
        records = [{'sequence': 1, 'type': 'model_response', 'payload': None},
                   {'sequence': 2, 'type': 'model_response', 'payload': {'response': None}},
                   {'sequence': 3, 'type': 'tool_result', 'payload': {'result': []}},
                   {'sequence': True, 'type': 'model_request', 'payload': {}},
                   {'sequence': 4, 'type': 'model_response', 'payload': {'response': {'action': 'read_sources'},
                    'request_id': [], 'usage': [], 'elapsed_seconds': 'unfinished'}},
                   {'sequence': 5, 'type': 'tool_result', 'payload': {'action': 'run_experiment',
                    'result': {'metrics': {'method': None}}}}]
        path.write_text('\n'.join(json.dumps(row) for row in records) + '\n{"sequence":', encoding='utf8')
        state = self.data.state('v3')
        self.assertEqual(state['successful_model_calls'], 1)
        self.assertEqual([e['sequence'] for e in self.data.events('v3')['events']], [4, 5])

    def test_http_keeps_both_dashboards_alive_with_malformed_saved_files(self):
        self.write(self.run / 'state.json', {'identity': []})
        self.write(self.run / 'tools/tool_state.json', {'registry': [], 'evaluations': None})
        self.write(self.results / 'summary.json', ['partial'])
        for version in ('legacy', 'revision'):
            server = (dashboard.create_server(self.run, self.results, 0) if version == 'legacy'
                      else dashboard_revision.create_server(self.root, 0))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                for endpoint in ('/api/state', '/api/events'):
                    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                    connection.request('GET', endpoint)
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    self.assertEqual(response.status, 200, (version, endpoint, body))
                    if endpoint.endswith('state'):
                        self.assertFalse(body['available'])
                        self.assertEqual(body['benchmark']['status'], 'unavailable')
                        self.assertEqual(body['benchmark']['metrics'], {})
                    connection.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)

    def test_symlinked_known_evidence_cannot_escape_selected_study(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / 'summary.json'
            self.write(secret, {'metrics': {'private_outside_marker': {}}})
            link = self.results / 'summary.json'
            link.parent.mkdir(parents=True)
            try:
                link.symlink_to(secret)
            except (OSError, NotImplementedError):
                self.skipTest('Host does not permit creation of symlink fixtures')
            for state in (self.data.state('v3'), dashboard.DashboardData(self.run, self.results).state()):
                self.assertNotIn('private_outside_marker', json.dumps(state))
                self.assertEqual(state['benchmark']['metrics'], {})


if __name__ == '__main__':
    unittest.main()
