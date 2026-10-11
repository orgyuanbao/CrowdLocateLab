"""Read-only dashboard contracts, using explicit fixtures rather than experiments."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from dashboard_revision import HTML, RevisionDashboardData, create_server, json_safe


class RevisionDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = RevisionDashboardData(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def input(self, case_id="fixture-a", n=8, scene="moving_outliers"):
        return {"case_id": case_id, "scenario": scene, "anchors": [[float(i), 1.] for i in range(n)],
                "times": [-float(i) for i in range(n)], "ranges": [2.] * n, "sigma": [.5] * n,
                "truth": [1., 2.], "velocity": [.1, .2], "meta": {"private_unused_field": "not exposed"}}

    def prediction(self, case_id="fixture-a", error=3., selected="velocity", admissible=True):
        return {"case_id": case_id, "scenario": "moving_outliers", "position": [2., 3.],
                "velocity": [.2, .3], "error_m": error, "work_units": 7, "elapsed_seconds": .01,
                "diagnostics": {"selected_model": selected, "cv": {"dynamic_admissible": admissible}}}

    def test_missing_revision_has_no_invented_metric_or_case(self):
        state = self.data.state("v3")
        self.assertFalse(state["available"])
        self.assertEqual(state["benchmark"]["metrics"], {})
        self.assertEqual(state["benchmark"]["status"], "not_started")
        self.assertEqual(self.data.cases("v3")["total"], 0)
        self.assertFalse(self.data.calibration("v3")["available"])
        self.assertFalse(self.data.boundary("v3")["available"])

    def test_two_versions_use_distinct_roots_and_keep_partial_status(self):
        self.write("results/benchmark/summary.json", {"metrics": {"v2_only": {}}})
        self.write("results/revision_benchmark/summary.json", {"status": "failed", "metrics": {"revision_only": {}}})
        self.assertEqual(set(self.data.state("v2")["benchmark"]["metrics"]), {"v2_only"})
        state = self.data.state("v3")
        self.assertEqual(set(state["benchmark"]["metrics"]), {"revision_only"})
        self.assertEqual(state["benchmark"]["status"], "failed")
        with self.assertRaises(ValueError):
            self.data.state("../../elsewhere")

    def test_cases_join_multiple_methods_and_expose_only_observation_fields(self):
        self.write("results/benchmark/main_inputs.json", [self.input()])
        self.write("results/benchmark/main_llm_selected.json", {"rows": [self.prediction()]})
        self.write("results/benchmark/main_dynamic_robust.json", {"rows": [self.prediction(error=1., selected="dynamic")]})
        result = self.data.cases("v2")
        self.assertEqual(result["method"], "llm_selected")
        self.assertEqual(result["rows"][0]["method_count"], 2)
        case = self.data.case("v2", "main:fixture-a")
        self.assertEqual(case["observation_n"], 8)
        self.assertEqual(case["predictions"]["dynamic_robust"]["selected_model"], "velocity")
        self.assertIn("truth", case["observation"])
        self.assertNotIn("meta", case["observation"])
        self.assertIn("离线", case["truth_notice"])

    def test_flat_sensitivity_geometry_and_method_pairing(self):
        self.write("results/sensitivity/inputs.json", [self.input()])
        row = self.prediction()
        row.update(method="llm_selected", x=5., y=6., n_observations=8)
        row.pop("position")
        self.write("results/sensitivity/predictions.json", {"complete": True, "rows": [row]})
        result = self.data.case("v2", "sensitivity:fixture-a")
        self.assertEqual(result["predictions"]["llm_selected"]["position"], [5., 6.])
        self.assertEqual(self.data.cases("v2", dataset="sensitivity")["total"], 1)

    def test_alias_provenance_is_preserved_without_new_trial_claim(self):
        self.write("results/revision_benchmark/main_ablate_k3.json", {
            "alias_of": "v2_frozen", "timing_reused": True, "rows": [self.prediction()]})
        prediction = self.data.case("v3", "main:fixture-a")["predictions"]["ablate_k3"]
        self.assertEqual(prediction["alias_of"], "v2_frozen")
        self.assertTrue(prediction["timing_reused"])

    def test_filter_sort_page_and_missing_values(self):
        values = [self.prediction("fixture-a", 3), self.prediction("fixture-b", 8), self.prediction("fixture-c", None)]
        self.write("results/benchmark/main_llm_selected.json", {"rows": values})
        self.assertEqual([r["case_id"] for r in self.data.cases("v2")["rows"]], ["fixture-b", "fixture-a", "fixture-c"])
        self.assertEqual([r["case_id"] for r in self.data.cases("v2", order="asc")["rows"]], ["fixture-a", "fixture-b", "fixture-c"])
        self.assertEqual(self.data.cases("v2", query="FIXTURE-B")["total"], 1)
        self.assertEqual(self.data.cases("v2", scene="static_clean")["total"], 0)
        self.assertEqual(self.data.cases("v2", offset=1, limit=1)["rows"][0]["case_id"], "fixture-a")

    def test_incomplete_json_is_skipped_then_refreshed_when_complete(self):
        path = self.write("results/revision_benchmark/main_revision_selected.json", {})
        path.write_text('{"rows":', encoding="utf-8")
        self.assertEqual(self.data.cases("v3")["total"], 0)
        self.assertTrue(self.data.cases("v3")["warnings"])
        self.write("results/revision_benchmark/main_revision_selected.json", {"rows": [self.prediction()]})
        self.assertEqual(self.data.cases("v3")["total"], 1)

    def test_global_and_mondrian_keep_infinite_radii_explicit(self):
        population = {"n": 4, "coverage": .75, "coverage_wilson95": [.3, .95],
                      "mean_radius_m": 3., "by_group": {"low": {"coverage": .5}}}
        record = {"alpha": .1, "populations": {"iid_test": population}, "calibration": {"n": 99}}
        unbounded = {"alpha": .1, "populations": {"edge": {"n": 4, "coverage": 1., "mean_radius_m": "infinity", "unbounded": True}}}
        self.write("results/revision_benchmark/conformal.json", {"global": {"0.1": record}, "mondrian": {"0.1": unbounded}})
        rows = self.data.calibration("v3")["rows"]
        self.assertEqual({r["mode"] for r in rows}, {"global", "mondrian"})
        self.assertEqual(next(r for r in rows if r["mode"] == "mondrian")["mean_radius_m"], "infinity")
        self.assertEqual(next(r for r in rows if r["mode"] == "global")["by_group"], population["by_group"])
        self.assertEqual(next(r for r in rows if r["mode"] == "global")["coverage_wilson95"], [.3, .95])
        self.assertEqual(json_safe({"a": float("inf"), "b": float("nan")}), {"a": "infinity", "b": None})

    def test_v2_global_only_is_not_fabricated_as_mondrian(self):
        self.write("results/benchmark/conformal.json", {"results": {"0.1": {"alpha": .1, "populations": {"iid_test": {"n": 3, "coverage": 1.}}}}})
        self.assertEqual([r["mode"] for r in self.data.calibration("v2")["rows"]], ["global"])

    def test_boundary_uses_actual_counts_and_does_not_fill_missing_n(self):
        self.write("results/revision_benchmark/edge_inputs.json", [self.input("fixture-a", 6), self.input("fixture-b", 6), self.input("fixture-c", 9)])
        self.write("results/revision_benchmark/edge_revision_selected.json", {"rows": [self.prediction("fixture-a", 2, "static", False), self.prediction("fixture-b", 6), self.prediction("fixture-c", 1)]})
        rows = self.data.boundary("v3")["rows"]
        self.assertEqual({r["observation_n"] for r in rows}, {6, 9})
        group = next(r for r in rows if r["observation_n"] == 6)
        self.assertEqual(group["n"], 2)
        self.assertEqual(group["mean_m"], 4)
        self.assertAlmostEqual(group["p90_m"], 5.6)
        self.assertEqual(group["velocity_rate"], .5)
        self.assertEqual(group["cv_admissible_rate"], .5)

    def test_event_pagination_retains_real_response_and_critic_fields(self):
        events = [{"sequence": i, "type": "model_response", "time_utc": "2026-01-01T00:00:00Z", "payload": {"purpose": "critic" if i == 3 else "planner", "response": {"action": "diagnose", "analysis_summary": f"fixture {i}"}, "replay": False}} for i in range(1, 6)]
        path = self.root / "runs/revision_reference/events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
        latest = self.data.events("v3", limit=2)
        self.assertEqual([e["sequence"] for e in latest["events"]], [4, 5])
        older = self.data.events("v3", before=4, limit=2)
        self.assertEqual([e["sequence"] for e in older["events"]], [2, 3])
        self.assertEqual(older["events"][-1]["purpose"], "critic")

    def test_stored_text_is_not_inserted_as_html_or_used_as_code(self):
        self.assertNotIn("innerHTML", HTML)
        self.assertNotIn("eval(", HTML)
        self.assertNotIn("cdn.", HTML)
        self.assertIn("textContent", HTML)
        self.assertIn("真值只在离线合成评测中可见", HTML)

    def test_http_is_loopback_read_only_and_rejects_paths_and_bad_queries(self):
        server = create_server(self.root, 0)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def request(method, path, host=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            headers = {} if host is None else {"Host": host}
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            result = response.status, response.read(), dict(response.getheaders())
            connection.close()
            return result
        try:
            self.assertEqual(request("GET", "/")[0], 200)
            self.assertEqual(request("GET", "/api/state?version=v3")[0], 200)
            self.assertEqual(request("POST", "/api/state")[0], 405)
            self.assertEqual(request("GET", "/api/state?version=v2&version=v3")[0], 400)
            self.assertEqual(request("GET", "/api/state?path=../../secret")[0], 400)
            self.assertEqual(request("GET", "/api/case?key=../../secret")[0], 404)
            self.assertEqual(request("GET", "/api/cases?order=unknown")[0], 400)
            self.assertEqual(request("GET", "/api/events?limit=999")[0], 400)
            self.assertEqual(request("GET", "/README.md")[0], 404)
            self.assertEqual(request("GET", "/api/state", "external.invalid")[0], 403)
            self.assertNotIn("Access-Control-Allow-Origin", request("GET", "/")[2])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
