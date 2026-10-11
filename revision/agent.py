"""Second real research round on fresh development data and disclosed v2 priors.

Only this extension changes the study policy. The original runtime, localization
operators and v2 tool implementation are reused without editing their bytes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from benchmark import validate_frozen_run
from localization import DEFAULT_SPEC, generate_suite
from localization.model import validate_spec
from tools.common import clean, digest, load, save
from tools.toolkit import BASELINES, CORE, TOOL_SPEC as ORIGINAL_TOOL_SPEC, ResearchToolkit

DISCOVERY_SEED = 1646191
VALIDATION_SEED = 1657383
SCENARIOS = [*CORE, "sparse"]
REFERENCE_IDS = (*BASELINES, "v2_frozen")
VALIDATION_REFERENCES = REFERENCE_IDS

TOOL_SPEC = copy.deepcopy(ORIGINAL_TOOL_SPEC)
TOOL_SPEC["register_method"]["arguments"]["spec"]["cv_folds"] = "integer 2..5"
TOOL_SPEC["register_method"]["description"] += (
    " cv_folds=2..5 is implemented. For n observations and K folds, "
    "n-ceil(n/K)>=6 is a necessary training-count condition for velocity CV. "
    "K=4 permits n=8 by count only, not a guarantee of rank, accuracy or lower cost. "
    "After a diagnosis, register an explicitly revised hypothesis; existing reference IDs do not count as new methods. "
    "Known inactive fields are normalized when rejecting duplicate compositions; this is not a proof of semantic novelty."
)
TOOL_SPEC["read_sources"]["description"] = (
    "Read original mathematical context, revision plan and already exposed v2 results. "
    "V2 test/sensitivity results are prior knowledge, not fresh validation. Never reads revision holdout."
)
TOOL_SPEC["run_experiment"]["description"] = (
    "Evaluate 1..4 methods on common fresh cases. Discovery: five scenarios x20, "
    "validation: five scenarios x30, at most four validation calls. Scenarios include sparse n=8. "
    "Validation must evaluate all four original baselines and v2_frozen, plus the selected candidate. "
    "The candidate and v2_frozen must share a validation call with at least one original baseline. "
    "Inspect scenario errors, tails, measured seconds and work units separately."
)
TOOL_SPEC["ablate"]["description"] = (
    "Change exactly one effective configuration field, register a distinct composed method and "
    "execute paired discovery evaluation. Mathematical record explicitly contains parent definition, "
    "changed field and final effective spec; the old formula is not asserted unchanged."
)
TOOL_SPEC["freeze"]["description"] = (
    "Freeze a NEW registered candidate after >=3 experiment calls, >=2 distinct new compositions "
    "excluding the five references, >=1 diagnosis, >=1 subsequent method revision, "
    ">=1 successful single-component ablation and >=1 mathematical check. Selected candidate and "
    "all five references must all have fresh validation evidence. "
    "Describe failures and tradeoffs; numerical superiority is not a freeze requirement."
)


def source_hashes(root):
    """Hash all preserved research sources plus the complete revision boundary."""
    root = Path(root).resolve()
    files = [root / "run_agent.py", root / "benchmark.py", root / "run_revision.py",
             root / "benchmark_revision.py", root / "research/revision_plan.json"]
    for folder in ("localization", "tools", "agent_runtime", "revision"):
        files.extend(sorted((root / folder).rglob("*.py")))
    missing = [str(p.relative_to(root)) for p in files if not p.is_file()]
    if missing:
        raise ValueError("Revision sources/plan must be complete before starting: " + ", ".join(missing))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(files))}


def active_spec(spec):
    """Normalize known inactive controls, not arbitrary solver equivalence."""
    cfg = validate_spec(spec)
    if cfg["model"] != "cv_select":
        cfg.pop("cv_folds")
        cfg.pop("gate_margin")
    if cfg["model"] == "static":
        cfg.pop("ridge")
    if cfg["residual"] != "log_range":
        cfg.pop("log_bias_correction")
        cfg.pop("log_sd")
    elif not cfg["log_bias_correction"]:
        cfg.pop("log_sd")
    if cfg["loss"] == "linear":
        cfg.pop("f_scale")
    if cfg["consensus_trials"] == 0:
        cfg.pop("seed")
    return cfg


class RevisionToolkit(ResearchToolkit):
    """Fresh-split research policy compatible with the original frozen-run audit."""
    def __init__(self, root, output, resume=False):
        root, output = Path(root).resolve(), Path(output).resolve()
        prior = validate_frozen_run(root, root / "runs/live_reference")
        prior_file = root / "runs/live_reference/tools/frozen_method.json"
        self.prior_hash = hashlib.sha256(prior_file.read_bytes()).hexdigest()
        self.prior_spec = copy.deepcopy(prior["frozen"]["spec"])
        super().__init__(root, output, resume=resume)
        if resume:
            if self.state.get("revision_version") != 3 or self.state.get("v2_frozen_sha256") != self.prior_hash:
                raise ValueError("Revision resume requires the same v2 prior and revision policy")
        else:
            self.state["registry"]["v2_frozen"] = {
                "spec": self.prior_spec,
                "hypothesis": "Previously frozen v2 method; exposed reference, not a new candidate.",
                "mathematical_definition": prior["frozen"]["mathematical_definition"],
                "registration_kind": "prior_reference",
                "prior_method_id": prior["frozen"]["method_id"],
                "prior_frozen_sha256": self.prior_hash,
            }
            self.state.update(revision_version=3, v2_frozen_sha256=self.prior_hash,
                reference_ids=list(REFERENCE_IDS), new_method_ids=[], revision_method_ids=[],
                single_component_ablations=[], diagnosis_history=[], validation_groups=[])
            self.persist()

    def catalog(self):
        result = super().catalog()
        result["revision_requirements"] = {
            "reference_ids": list(REFERENCE_IDS), "new_method_ids": self.state["new_method_ids"],
            "registered_after_diagnosis": self.state["revision_method_ids"],
            "successful_single_component_ablations": self.state["single_component_ablations"],
            "validation_references": list(VALIDATION_REFERENCES),
            "exposed_prior": "v2 outcomes have already been read; only revision splits are fresh",
        }
        return clean(result)

    def cases_for(self, split):
        if split not in ("discovery", "validation"):
            raise ValueError("Agent cannot access revision test/calibration/holdout splits")
        if split not in self.cases:
            seed, count = ((DISCOVERY_SEED, 20) if split == "discovery" else (VALIDATION_SEED, 30))
            self.cases[split] = generate_suite(seed, count, SCENARIOS)
            save(self.output / f"{split}_inputs.json", [case.to_dict() for case in self.cases[split]])
        return self.cases[split]

    def tool_read_sources(self):
        result = super().tool_read_sources()
        plan_file = self.root / "research/revision_plan.json"
        result["revision_plan"] = load(plan_file)
        result["prior_status"] = "The following v2 data are explicitly exposed prior knowledge, never fresh revision holdout."
        result["v2_frozen"] = {"spec": self.prior_spec, "sha256": self.prior_hash}
        old = load(self.root / "results/benchmark/summary.json")
        keys = ("mean_m", "p90_m", "mean_seconds", "mean_work_units")
        compact = lambda m: {key: m[key] for key in keys if key in m}
        result["exposed_v2_metrics"] = {
            name: {split: {"overall": compact(item[split]["overall"]),
                          "by_scenario": {scene: compact(metric) for scene, metric in item[split]["by_scenario"].items()
                                          if scene in ("static_clean", "moving_outliers", "sparse")}}
                   for split in ("main", "stress")}
            for name, item in old["metrics"].items() if name in ("static_wls", "dynamic_robust", "llm_selected")
        }
        result["revision_scope"] = (
            "Investigate v2 static-normal degradation, n=8 velocity exclusion and compute cost. "
            "Only development split generation is callable. CV training count is necessary, not sufficient. "
            "v2 work units and new-solver residual counts differ: do not infer speedup from their ratio."
        )
        return result

    def tool_register_method(self, method_id, hypothesis, mathematical_definition, spec):
        if not isinstance(spec, dict):
            raise ValueError("spec must be a JSON object")
        folds = spec.get("cv_folds", DEFAULT_SPEC["cv_folds"])
        if isinstance(folds, bool) or not isinstance(folds, int) or not 2 <= folds <= 5:
            raise ValueError("cv_folds must be an integer in 2..5")
        if not str(hypothesis).strip() or not str(mathematical_definition).strip():
            raise ValueError("A falsifiable hypothesis and mathematical definition are required")
        effective = active_spec(spec)
        if any(digest(active_spec(self.state["registry"][name]["spec"])) == digest(effective)
               for name in ["v2_frozen", *self.state["new_method_ids"]]):
            raise ValueError("New candidates must have distinct effective compositions")
        result = super().tool_register_method(method_id, hypothesis, mathematical_definition, copy.deepcopy(spec))
        record = self.state["registry"][method_id]
        record["registration_kind"] = "new_composition"
        record["registered_after_diagnosis_count"] = self.state["diagnoses"]
        record["diagnosis_evidence"] = copy.deepcopy(self.state["diagnosis_history"][-1:])
        self.state["new_method_ids"].append(method_id)
        if self.state["diagnoses"]:
            self.state["revision_method_ids"].append(method_id)
        save(self.output / "methods" / f"{method_id}.json", record)
        return {**result, "registration_kind": record["registration_kind"],
                "registered_after_diagnosis_count": record["registered_after_diagnosis_count"]}

    def tool_run_experiment(self, method_ids, split="discovery"):
        result = super().tool_run_experiment(method_ids, split)
        if split == "validation":
            self.state["validation_groups"].append(list(method_ids))
        return result

    def tool_diagnose(self, method_id, split="discovery", top_k=4):
        result = super().tool_diagnose(method_id, split, top_k)
        reference = self.state["evaluations"][f"{split}:{method_id}"]
        diagnosis = {"method_id": method_id, "split": split, "diagnosis_number": self.state["diagnoses"],
                     "evidence_path": reference["evidence_path"], "evidence_sha256": reference["evidence_sha256"]}
        self.state["diagnosis_history"].append(diagnosis)
        result["revision_diagnosis"] = diagnosis
        return result

    def tool_ablate(self, method_id, changes, new_id, split="discovery"):
        if split != "discovery":
            raise ValueError("Ablations during research use discovery split")
        if not isinstance(changes, dict) or len(changes) != 1:
            raise ValueError("A controlled ablation must change exactly one component field")
        if method_id not in self.state["registry"]:
            raise ValueError("Unknown parent method")
        parent = self.state["registry"][method_id]
        if "baseline" in parent["spec"]:
            raise ValueError("Ablation requires an explicit composed parent, not a baseline shorthand")
        field, value = next(iter(changes.items()))
        before = {**DEFAULT_SPEC, **parent["spec"]}
        if field not in DEFAULT_SPEC or digest(before[field]) == digest(value):
            raise ValueError("Ablation must change a known field to a different effective value")
        spec = {**parent["spec"], **changes}
        if digest(active_spec(parent["spec"])) == digest(active_spec(spec)):
            raise ValueError("Ablation changes only an inactive field for this model")
        definition = (
            f"Parent {method_id} definition (historical reference, replaced where the change applies): "
            f"{parent['mathematical_definition']}\n"
            f"Controlled change: {field}: {json.dumps(before[field], ensure_ascii=False)} -> "
            f"{json.dumps(value, ensure_ascii=False)}.\n"
            "The final objective and model are defined by localization/method_math.md using this complete "
            "effective specification, which overrides the corresponding parent formula: "
            + json.dumps({**DEFAULT_SPEC, **spec}, ensure_ascii=False, sort_keys=True)
        )
        self.tool_register_method(new_id, f"Single-component ablation of {method_id}: {changes}", definition, spec)
        record = self.state["registry"][new_id]
        record.update(registration_kind="single_component_ablation", parent_method_id=method_id,
                      controlled_changes=copy.deepcopy(changes))
        save(self.output / "methods" / f"{new_id}.json", record)
        result = self.tool_run_experiment([method_id, new_id], split)
        self.state["single_component_ablations"].append(new_id)
        result.update(controlled_changes=changes, parent_method_id=method_id, new_method_id=new_id,
                      mathematical_definition=definition)
        return result

    def tool_freeze(self, method_id, scientific_conclusion):
        if method_id not in self.state["new_method_ids"]:
            raise ValueError("Freeze a new revision candidate, not one of the five prior references")
        if (self.state["experiment_calls"] < 3 or len(self.state["new_method_ids"]) < 2
                or self.state["diagnoses"] < 1 or self.state["math_checks"] < 1
                or not self.state["revision_method_ids"] or not self.state["single_component_ablations"]):
            raise ValueError("Need >=3 experiments, >=2 new compositions, diagnosis then revision, a successful single-component ablation and math check")
        required = [method_id, *VALIDATION_REFERENCES]
        missing = [name for name in required if f"validation:{name}" not in self.state["evaluations"]]
        if missing:
            raise ValueError("Missing fresh validation comparisons: " + ", ".join(missing))
        if not any({method_id, "v2_frozen"}.issubset(group) and set(BASELINES).intersection(group)
                   for group in self.state["validation_groups"]):
            raise ValueError("Candidate and v2_frozen must share a validation call with an original baseline")
        if not str(scientific_conclusion).strip():
            raise ValueError("A scientific conclusion including remaining failures is required")
        return super().tool_freeze(method_id, scientific_conclusion)
