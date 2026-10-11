"""Pre-holdout evaluator branch fixtures; never generate a benchmark dataset."""
import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import benchmark as original
import benchmark_v2_1 as amended
from tools.common import digest


def original_method_builder():
    """Extract only the original registry-construction block, not its main run."""
    tree = ast.parse(Path(original.__file__).read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    body, copying = [], False
    for node in main.body:
        name = node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None
        if name == "methods": copying = True
        if name == "codefiles": break
        if copying: body.append(node)
    self_check = {node.name for node in body if isinstance(node, ast.FunctionDef)}
    if not body or self_check != {"ablate"}:
        raise AssertionError("Original method construction block changed; inspect the amendment manually.")
    body.append(ast.Return(value=ast.Tuple(elts=[ast.Name(id="methods", ctx=ast.Load()),
                                               ast.Name(id="ablations", ctx=ast.Load())], ctx=ast.Load())))
    function = ast.FunctionDef(name="fixture_build", args=ast.arguments(posonlyargs=[],
        args=[ast.arg(arg="chosen"), ast.arg(arg="registry")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=body, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {"BASELINES": original.BASELINES, "digest": digest}
    exec(compile(module, "<original-registry-block-only>", "exec"), namespace)
    return namespace["fixture_build"]


class EvaluatorAmendmentTests(unittest.TestCase):
    def setUp(self):
        self.registry = {name: {"spec": spec} for name, spec in original.BASELINES.items()}
        self.registry.update(fixture_first={"spec": {"model": "static", "loss": "huber"}},
                             fixture_second={"spec": {"model": "velocity", "residual": "log_range"}},
                             fixture_third={"spec": {"model": "cv_select"}})

    def test_baseline_shorthand_remains_unchanged_and_has_no_invalid_component_ablation(self):
        with patch("benchmark_v2_1.generate_suite") as generation, patch("benchmark_v2_1.evaluate") as evaluation:
            for spec in original.BASELINES.values():
                with self.subTest(spec=spec):
                    before = copy.deepcopy(spec)
                    methods, ablations = amended.build_methods(spec, self.registry)
                    self.assertEqual(ablations, [])
                    self.assertEqual(methods["llm_selected"], before)
                    self.assertEqual(spec, before)
                    self.assertFalse(any(name.startswith("ablate_") for name in methods))
                    self.assertIn("predecessor_fixture_first", methods)
                    self.assertIn("predecessor_fixture_second", methods)
                    self.assertNotIn("predecessor_fixture_third", methods)
                    for candidate in methods.values():
                        if "baseline" in candidate:
                            self.assertEqual(set(candidate), {"baseline"})
        generation.assert_not_called()
        evaluation.assert_not_called()

    def test_compositional_branch_is_exactly_the_original_method_construction(self):
        build_old = original_method_builder()
        fixtures = [
            {"model": "cv_select", "residual": "log_range", "scale": "constant", "loss": "cauchy",
             "f_scale": 1.5, "ridge": .05, "trim_fraction": .2, "consensus_trials": 3,
             "log_bias_correction": True, "gate_margin": .05},
            {"model": "static", "residual": "range", "scale": "reported", "trim_fraction": 0.},
            {"model": "velocity", "residual": "log_range", "log_bias_correction": False},
            {"model": "static", "loss": "huber"},
        ]
        for spec in fixtures:
            with self.subTest(spec=spec):
                self.assertEqual(amended.build_methods(spec, self.registry), build_old(spec, self.registry))

    def test_statistics_calibration_generation_and_validation_are_reused(self):
        for name in ("paired_ci", "iid_mixture", "conformal", "evaluate", "generate_suite", "validate_frozen_run"):
            self.assertIs(getattr(amended, name), getattr(original, name))

    def test_cli_options_and_declared_dataset_seeds_are_unchanged(self):
        def inspect(path):
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
            main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
            options = [ast.dump(node.value, include_attributes=False) for node in main.body
                       if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                       and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "add_argument"]
            protocol = next(node.value for node in main.body if isinstance(node, ast.Assign)
                            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "protocol")
            values = {key.value: value for key, value in zip(protocol.keys, protocol.values)}
            seeds = {key: ast.literal_eval(values[key]) for key in
                     ("main_seeds", "stress_seed", "calibration_seed", "coverage_seed")}
            return options, seeds
        self.assertEqual(inspect(amended.__file__), inspect(original.__file__))


if __name__ == "__main__":
    unittest.main()
