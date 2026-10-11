"""Read-only Chinese research browser for the frozen v2 and revision studies.

Standard library only. No solver, model call, arbitrary file endpoint, or write
operation is exposed. Missing/partial files remain visibly unavailable.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from dashboard import DashboardData, _event_view, iter_events


VERSIONS = {
    "v2": {"label": "第一轮 · V2", "run": "runs/live_reference", "results": "results/benchmark"},
    "v3": {"label": "第二轮 · Revision", "run": "runs/revision_reference", "results": "results/revision_benchmark"},
}
SPLITS = ("main", "stress", "edge")


def safe_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "infinity" if value > 0 else "-infinity" if value < 0 else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def model_label(row):
    raw = row.get("diagnostics", {}).get("selected_model", row.get("selected_model", "unknown"))
    return "velocity" if raw == "dynamic" else raw


class RevisionDashboardData:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._cache = {}
        self._lock = threading.RLock()

    def config(self, version):
        if version not in VERSIONS:
            raise ValueError("version must be v2 or v3")
        item = VERSIONS[version]
        return self.root / item["run"], self.root / item["results"]

    def versions(self):
        return [{"id": key, **value,
                 "run_available": (self.root / value["run"] / "state.json").is_file(),
                 "results_available": (self.root / value["results"] / "summary.json").is_file()}
                for key, value in VERSIONS.items()]

    def state(self, version):
        run, results = self.config(version)
        state = DashboardData(run, results).state()
        summary = safe_json(results / "summary.json", {}) or {}
        status = summary.get("status") if isinstance(summary, dict) else None
        if status in ("failed", "running", "incomplete"):
            state["benchmark"]["status"] = status
        if summary and not summary.get("metrics"):
            state["benchmark"]["status"] = status or "unrecognized"
        state.update(version=version, version_label=VERSIONS[version]["label"],
                     paths=VERSIONS[version], versions=self.versions())
        # The dedicated calibration endpoint returns aggregates, not thousands
        # of duplicated coverage rows with every overview refresh.
        state["benchmark"].pop("conformal", None)
        state["benchmark"]["aliases"] = summary.get("aliases", summary.get("protocol", {}).get("aliases", {}))
        state["benchmark"]["comparisons"] = summary.get("comparisons", {})
        return json_safe(state)

    def events(self, version, before=0, limit=150):
        run, _ = self.config(version)
        items = deque(maxlen=limit)
        total = 0
        for event in iter_events(run / "events.jsonl"):
            total += 1
            if before == 0 or event["sequence"] < before:
                items.append(_event_view(event))
        first = items[0]["sequence"] if items else None
        return {"events": list(items), "total_events": total,
                "has_older": first is not None and first > 1, "oldest_sequence": first}

    def _allowed_file(self, path):
        try:
            return path.is_file() and path.resolve().is_relative_to(self.root)
        except OSError:
            return False

    def _files(self, version):
        _, directory = self.config(version)
        files = []
        for split in SPLITS:
            input_path = directory / f"{split}_inputs.json"
            if self._allowed_file(input_path):
                files.append((split, "inputs", input_path))
            for path in sorted(directory.glob(f"{split}_*.json")):
                if path != input_path and self._allowed_file(path):
                    files.append((split, path.stem[len(split) + 1:], path))
        if version == "v2":
            directory = self.root / "results" / "sensitivity"
            for name, kind in (("inputs.json", "inputs"), ("predictions.json", "flat")):
                path = directory / name
                if self._allowed_file(path):
                    files.append(("sensitivity", kind, path))
        return files

    def archive(self, version):
        with self._lock:
            files = self._files(version)
            signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for _, _, p in files)
            if self._cache.get(version, {}).get("signature") == signature:
                return self._cache[version]
            cases = {}
            warnings = []
            for split, kind, path in files:
                obj = safe_json(path)
                if obj is None:
                    warnings.append(f"{path.name} 尚未写入完整 JSON，暂不展示。")
                    continue
                if kind == "inputs":
                    inputs = obj if isinstance(obj, list) else obj.get("cases", obj.get("inputs", [])) if isinstance(obj, dict) else []
                    for value in inputs:
                        if not isinstance(value, dict) or not isinstance(value.get("case_id"), str):
                            continue
                        key = split + ":" + value["case_id"]
                        entry = cases.setdefault(key, {"key": key, "case_id": value["case_id"], "dataset": split, "predictions": {}})
                        # The browser exposes only saved offline evaluation observations.
                        entry["observation"] = {k: value[k] for k in ("anchors", "times", "ranges", "sigma", "truth", "velocity") if k in value}
                        entry["scenario"] = value.get("scenario", "unknown")
                        entry["observation_n"] = len(value.get("anchors", []))
                        meta = value.get("meta", {})
                        entry["contamination"] = meta.get("actual_outlier_fraction")
                    continue
                rows = obj.get("rows", []) if isinstance(obj, dict) else obj if isinstance(obj, list) else []
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
                        continue
                    name = row.get("method") if kind == "flat" else kind
                    if not isinstance(name, str):
                        continue
                    key = split + ":" + row["case_id"]
                    entry = cases.setdefault(key, {"key": key, "case_id": row["case_id"], "dataset": split, "predictions": {}})
                    position = row.get("position")
                    if position is None and finite(row.get("x")) and finite(row.get("y")):
                        position = [row["x"], row["y"]]
                    entry["scenario"] = row.get("scenario", entry.get("scenario", "unknown"))
                    entry["observation_n"] = row.get("observation_n", row.get("n_observations", entry.get("observation_n")))
                    entry["predictions"][name] = {
                        "method": name, "position": position, "velocity": row.get("velocity"),
                        "error_m": row.get("error_m"), "work_units": row.get("work_units"),
                        "elapsed_seconds": row.get("elapsed_seconds"), "selected_model": model_label(row),
                        "alias_of": obj.get("alias_of") if isinstance(obj, dict) else None,
                        "timing_reused": bool(obj.get("timing_reused", False)) if isinstance(obj, dict) else False,
                        "diagnostics": row.get("diagnostics", {}),
                    }
            archive = {"signature": signature, "cases": cases, "warnings": warnings,
                       "methods": sorted({method for case in cases.values() for method in case["predictions"]}),
                       "scenarios": sorted({case.get("scenario", "unknown") for case in cases.values()}),
                       "datasets": sorted({case["dataset"] for case in cases.values()})}
            self._cache[version] = archive
            return archive

    def cases(self, version, scene="", method="", dataset="", query="", order="desc", offset=0, limit=80):
        archive = self.archive(version)
        preferred = "revision_selected" if version == "v3" else "llm_selected"
        method = method or (preferred if preferred in archive["methods"] else next(iter(archive["methods"]), ""))
        rows = []
        for item in archive["cases"].values():
            if scene and item.get("scenario") != scene or dataset and item["dataset"] != dataset:
                continue
            if query.casefold() not in item["case_id"].casefold():
                continue
            if method and method not in item["predictions"]:
                continue
            prediction = item["predictions"].get(method, {})
            rows.append({"key": item["key"], "case_id": item["case_id"], "dataset": item["dataset"],
                         "scenario": item.get("scenario", "unknown"), "observation_n": item.get("observation_n"),
                         "error_m": prediction.get("error_m"), "selected_model": prediction.get("selected_model"),
                         "has_observations": bool(item.get("observation")), "method_count": len(item["predictions"])})
        if order == "id":
            rows.sort(key=lambda row: (row["case_id"], row["dataset"]))
        else:
            # Missing errors always remain last, in either numeric ordering.
            rows.sort(key=lambda row: (not finite(row["error_m"]),
                                      (-1 if order == "desc" else 1) * row["error_m"] if finite(row["error_m"]) else 0,
                                      row["case_id"]))
        return {"rows": rows[offset:offset + limit], "total": len(rows), "offset": offset,
                "limit": limit, "method": method, "methods": archive["methods"],
                "scenarios": archive["scenarios"], "datasets": archive["datasets"], "warnings": archive["warnings"]}

    def case(self, version, key):
        item = self.archive(version)["cases"].get(key)
        if item is None:
            raise KeyError("Case is not present in the saved evaluation archive")
        return {**item, "truth_notice": "真值仅为离线合成评测标签，不提供给定位求解器或科研决策工具。"}

    def calibration(self, version):
        _, results = self.config(version)
        summary = safe_json(results / "summary.json", {}) or {}
        raw = summary.get("conformal") or safe_json(results / "conformal.json", {}) or {}
        modes = {"global": raw.get("results", {})} if "results" in raw else {k: raw[k] for k in ("global", "mondrian") if isinstance(raw.get(k), dict)}
        rows = []
        for mode, records in modes.items():
            if "results" in records:
                records = records["results"]
            for alpha, record in records.items():
                if not isinstance(record, dict) or not isinstance(record.get("populations"), dict):
                    continue
                for population, values in record["populations"].items():
                    if not isinstance(values, dict):
                        continue
                    rows.append({"mode": mode, "alpha": record.get("alpha", alpha), "population": population,
                                 "n": values.get("n"), "coverage": values.get("coverage"),
                                 "coverage_wilson95": values.get("coverage_wilson95"),
                                 "mean_radius_m": values.get("mean_radius_m"), "median_radius_m": values.get("median_radius_m"),
                                 "by_scenario": values.get("by_scenario", {}), "by_group": values.get("by_group", {}),
                                 "rejection_curve": values.get("rejection_curve", []),
                                 "calibration": record.get("calibration", {key: value for key, value in record.items() if key != "populations"}),
                                 "unbounded": values.get("unbounded", values.get("unbounded_rate")),
                                 "unbounded_n": values.get("unbounded_n")})
        return {"rows": json_safe(rows), "available": bool(rows),
                "notice": "Global 的边际覆盖与 Mondrian 的分组覆盖依赖各自交换性条件；拒绝后子集和分布偏移不自动继承保证。"}

    def boundary(self, version):
        groups = {}
        for case in self.archive(version)["cases"].values():
            if case["dataset"] != "edge":
                continue
            n = case.get("observation_n")
            if n not in range(6, 13):
                continue
            for method, row in case["predictions"].items():
                key = (case.get("scenario", "unknown"), n, method)
                groups.setdefault(key, []).append(row)
        rows = []
        for (scene, n, method), predictions in sorted(groups.items()):
            errors = [p["error_m"] for p in predictions if finite(p.get("error_m"))]
            labels = Counter(p.get("selected_model", "unknown") for p in predictions)
            admissible = [p["diagnostics"]["cv"].get("dynamic_admissible") for p in predictions
                          if isinstance(p["diagnostics"].get("cv"), dict)]
            admissible = [v for v in admissible if isinstance(v, bool)]
            rows.append({"scenario": scene, "observation_n": n, "method": method, "n": len(predictions),
                         "valid_error_n": len(errors), "mean_m": sum(errors) / len(errors) if errors else None,
                         "p90_m": percentile(errors, .9), "velocity_rate": labels["velocity"] / len(predictions),
                         "cv_admissible_rate": sum(admissible) / len(admissible) if admissible else None})
        return {"rows": rows, "available": bool(rows),
                "notice": "仅汇总保存的 edge 逐例预测；缺少某个观测数时不插值或补造案例。"}


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CrowdLocateLab · 研究浏览器</title>
<style>
:root{--ink:#19364a;--teal:#087e80;--bg:#f0f4f6;--line:#dce5eb;--muted:#647986;--red:#ad483d;--gold:#94611b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 system-ui,"Microsoft YaHei",sans-serif}button,input,select{font:inherit}button,select{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #d4a943;outline-offset:2px}button{border:1px solid var(--line);background:white;border-radius:7px;padding:7px 12px;color:var(--ink)}button:hover{border-color:var(--teal)}header{background:var(--ink);color:white;padding:20px max(24px,calc((100vw - 1500px)/2));display:flex;align-items:center;justify-content:space-between;gap:20px}header strong{font-size:24px}header small{display:block;color:#c3d5df;font-size:12px}.versions{display:flex;gap:6px}.versions button{background:#2b485b;color:white;border-color:#597080}.versions button.active{background:white;color:var(--ink)}main{max-width:1500px;margin:auto;padding:24px}.bar{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:17px;flex-wrap:wrap}h1{font-size:24px;line-height:1.35;margin:0 0 6px}h2{font-size:18px;margin:0 0 12px}h3{font-size:14px;margin:12px 0 6px}p{margin:7px 0}.muted{font-size:12px;color:var(--muted)}.tag{background:#e3f1ee;color:#076660;padding:4px 9px;border-radius:5px;font-size:12px;display:inline-block}.tag.warn{background:#fff0d4;color:#80580d}.tag.fail{background:#f9e6e1;color:var(--red)}.tabs{display:flex;gap:5px;border-bottom:1px solid var(--line);margin-bottom:18px;flex-wrap:wrap}.tabs button{border:0;border-bottom:3px solid transparent;border-radius:0;background:transparent;padding:10px 16px}.tabs button.active{border-color:var(--teal);color:var(--teal);font-weight:700}.page{display:none}.page.active{display:block}.panel,.stat{background:white;border:1px solid var(--line);border-radius:10px;padding:20px}.panel+.panel{margin-top:16px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}.stat strong{display:block;font-size:30px;margin:4px 0}.stat small{color:var(--muted)}.grid{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}.filters{display:flex;align-items:end;gap:10px;flex-wrap:wrap}.filters label{font-size:12px;color:var(--muted);display:grid;gap:3px}.filters select,.filters input{border:1px solid #cddbe3;border-radius:6px;padding:7px;background:white;color:var(--ink);max-width:280px}.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{color:var(--muted);background:#f3f7f9;font-weight:600}tr.chosen{background:#e8f6f1}.negative{color:var(--red)}.note{padding:12px 14px;background:#edf6f5;border-left:3px solid var(--teal);border-radius:4px;font-size:13px}.note.warn{background:#fff6e9;border-color:#b77e2a}.empty{padding:25px;color:var(--muted);text-align:center;border:1px dashed #c9d7df;border-radius:8px}.method{border:1px solid var(--line);border-radius:7px;padding:12px;margin:10px 0}.method strong{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:12px;font:11px/1.55 Consolas,monospace;max-height:350px;overflow:auto;border-radius:5px}summary{cursor:pointer;color:var(--teal);font-size:12px}.events{max-height:850px;overflow:auto;padding-right:6px}.event{border-left:3px solid #d9e4e9;padding:3px 14px 16px;margin:0 0 16px 4px}.event.model{border-color:var(--teal)}.event.critic{border-color:#bb8728}.event.failed{border-color:var(--red)}.event p{white-space:pre-wrap;overflow-wrap:anywhere}.event .title{font-weight:650}.kv{display:grid;grid-template-columns:110px 1fr;gap:6px 10px;font-size:12px}.kv dt{color:var(--muted)}.kv dd{margin:0;overflow-wrap:anywhere}.casegrid{display:grid;grid-template-columns:minmax(290px,.8fr) minmax(500px,1.7fr);gap:18px;margin-top:16px}.caselist{max-height:780px;overflow:auto}.caseitem{display:block;width:100%;text-align:left;margin:0 0 7px;font-size:12px;padding:10px;overflow-wrap:anywhere}.caseitem.active{border-color:var(--teal);background:#edf7f4}.caseitem strong{display:block}.caseitem .id{font:10px/1.5 Consolas,monospace;color:var(--muted);overflow-wrap:anywhere}.casehead{font-size:11px;overflow-wrap:anywhere;color:var(--muted);margin-bottom:10px}.plot{width:100%;height:470px;display:block;border:1px solid var(--line);border-radius:7px;background:#fafcfd}.plotcontrols{display:flex;gap:15px;flex-wrap:wrap;margin:10px 0;font-size:12px}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;margin:8px 0}.legend span:before{content:"";display:inline-block;width:10px;height:10px;background:var(--color);border-radius:50%;margin-right:5px}.casehelp{font-size:12px;color:var(--muted)}.rangecontrol{display:flex;align-items:center;gap:8px;font-size:12px}#circleRange{flex:1;max-width:340px}.error{background:#f8e8e4;color:#913d33;padding:12px 16px;border-radius:7px;margin-bottom:15px;display:none}.paging{display:flex;align-items:center;justify-content:space-between;margin:12px 0;font-size:12px}.chart{width:100%;height:380px;background:#fafcfd;border:1px solid var(--line);border-radius:7px}.checkmethods{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;margin:10px 0}.allgood{white-space:pre-wrap}.noscript{padding:30px;background:#fff6e9}footer{font-size:11px;color:var(--muted);margin-top:24px}.hidden{display:none!important}@media(max-width:1000px){.grid,.casegrid{grid-template-columns:1fr}.caselist{max-height:320px}.stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){header{padding:15px;align-items:start;flex-direction:column}main{padding:15px}.panel{padding:14px}.stats{gap:10px}.stat{padding:12px}.versions{width:100%}.versions button{flex:1}.plot{height:380px}.filters label,.filters select,.filters input{max-width:100%}}
</style></head><body><header><div><strong>CrowdLocateLab</strong><small>研究浏览器 · 从真实迭代追溯到每一个失败案例</small></div><div class="versions" aria-label="选择研究版本"><button data-version="v2">第一轮 · V2</button><button data-version="v3" class="active">第二轮 · Revision</button></div></header><noscript><div class="noscript">请启用浏览器 JavaScript 以查看交互式图表；此界面不使用外部脚本。</div></noscript><main><div id="error" class="error" role="alert"></div><div class="bar"><div><h1 id="heading">第二轮研究</h1><div id="source" class="muted">正在读取本地保存的证据</div></div><div><span id="mode" class="tag">等待记录</span> <span id="status" class="tag warn">尚未开始</span> <button id="refresh">刷新</button></div></div>
<nav class="tabs" aria-label="研究视图"><button class="active" data-tab="overview">研究概览</button><button data-tab="timeline">智能体迭代</button><button data-tab="cases">失败案例</button><button data-tab="calibration">校准与拒绝</button><button data-tab="boundary">6–12 份观测边界</button></nav>
<section id="overview" class="page active"><div class="stats"><div class="stat"><small>实际研究动作</small><strong id="steps">—</strong><small id="stepnote">等待读取记录</small></div><div class="stat"><small>模型请求 · 含批评与重试</small><strong id="calls">—</strong><small id="callnote">没有可用记录</small></div><div class="stat"><small>所选方法 · 主测试平均误差</small><strong id="mean">—</strong><small id="meannote">等待独立评测</small></div><div class="stat"><small>独立批评记录</small><strong id="critics">—</strong><small>角色与提示分离，不代表不同模型</small></div></div><div class="grid"><div><section class="panel"><h2>独立测试与负面结果</h2><p class="muted">测试指标只展示已保存结果。平均误差低于某个基线不代表所有场景不劣化。</p><div id="metrics"></div><div id="comparisons"></div></section><section class="panel"><h2>冻结结论与证据边界</h2><div id="conclusion"></div></section></div><section class="panel"><h2>研究状态</h2><dl id="runtime" class="kv"></dl><details><summary>查看本轮目标</summary><p id="goal" class="allgood"></p></details><h3>注册方法与可检验假设</h3><div id="methods"></div></section></div></section>
<section id="timeline" class="page"><div class="grid"><section class="panel"><div class="bar"><h2>真实模型动作与工具观察</h2><label>筛选 <select id="eventFilter"><option value="all">全部记录</option><option value="model">模型决策</option><option value="tool">工具执行</option><option value="critic">独立批评</option><option value="failure">失败记录</option></select></label></div><p id="eventCount" class="muted"></p><div id="events" class="events"></div><button id="older">加载更早的事件</button></section><section class="panel"><h2>如何核对一次真实迭代</h2><ol><li>模型响应中的假设、动作与参数。</li><li>紧随其后的工具调用及实际返回。</li><li>失败诊断或批评是否导致下一步修订。</li><li>验证证据与最终冻结是否对应。</li></ol><div class="note">公开决策摘要不是模型的私有思维链。REPLAY 是读取已保存决策；只有真实 provider 调用记录才能作为新模型运行证据。</div><h3>最近的独立审阅</h3><div id="critique"></div><p class="muted">这里的“独立”指单独提示和角色调用，不表示来自另一家模型或独立实验室。</p></section></div></section>
<section id="cases" class="page"><section class="panel"><h2>从误差定位失败案例</h2><div class="filters"><label>数据集合<select id="dataset"><option value="">全部</option></select></label><label>场景<select id="scene"><option value="">全部</option></select></label><label>按此方法筛选 / 排序<select id="caseMethod"></select></label><label>排序<select id="caseOrder"><option value="desc">误差从大到小</option><option value="asc">误差从小到大</option><option value="id">案例编号</option></select></label><label>搜索 case_id<input id="caseSearch" type="search" placeholder="输入编号、种子或 n8" autocomplete="off"></label><button id="search">查看案例</button></div><p id="archiveNote" class="muted">正在读取逐例证据。</p><div class="casegrid"><div><div id="caseCount" class="muted"></div><div id="caseList" class="caselist"></div><div class="paging"><button id="prevCases">上一页</button><button id="nextCases">下一页</button></div></div><div><div id="caseTitle" class="casehead">请选择案例</div><canvas id="casePlot" class="plot" aria-label="定位观测与估计位置图"></canvas><div class="plotcontrols"><label><input type="checkbox" id="showCircles"> 显示历史测距圆</label><label><input type="checkbox" id="showTruth" checked> 显示离线评测真值</label><label><input type="checkbox" id="showVelocity" checked> 显示速度</label></div><div class="rangecontrol"><label for="circleRange">测距圆：</label><input id="circleRange" type="range" min="0" max="0" value="0"><span id="circleLabel">全部</span></div><div id="plotLegend" class="legend"></div><p class="casehelp">三角形为上报锚点，× 为参考时刻真值，圆点为方法估计。速度箭头显示 5 秒位移。每个测距圆对应不同历史时刻，不应假设所有圆交于当前点；图中超出视野的圆会被裁切。</p><div class="note warn">真值只在离线合成评测中可见，不提供给定位求解器。图表与诊断帮助解释失败，不构成新实验或精度保证。</div><div id="caseMetrics" class="tablewrap"></div><div id="caseDiagnostics"></div></div></div></section></section>
<section id="calibration" class="page"><section class="panel"><h2>独立校准：Global 与 Mondrian</h2><p class="muted">同时查看覆盖率与半径。没有 Mondrian 记录的版本会明确显示缺失，不拿 Global 结果替代。</p><div id="calibrationTable"></div><div id="calibrationDetails"></div><div class="note warn">覆盖定理依赖冻结估计器与相应的交换性条件。分组覆盖、按场景覆盖、拒绝之后的覆盖，以及分布偏移后的覆盖是不同问题。无限半径不是高精度定位。</div></section></section>
<section id="boundary" class="page"><section class="panel"><h2>少量观测下的模型选择边界</h2><p id="boundaryNote" class="muted">读取 edge 集合；没有保存结果时保持空白。</p><div class="filters"><label>场景<select id="boundaryScene"></select></label><label>纵轴<select id="boundaryMetric"><option value="mean_m">平均误差 / m</option><option value="p90_m">CEP90 / m</option><option value="velocity_rate">选择运动比例</option><option value="cv_admissible_rate">CV 运动可用比例</option></select></label></div><div id="boundaryMethods" class="checkmethods"></div><canvas id="boundaryPlot" class="chart" aria-label="观测数 6 至 12 的边界比较图"></canvas><div id="boundaryTable" class="tablewrap"></div><div class="note">连线仅辅助观察各个离散样本数。缺失点不会插值补齐；选择静态可能来自样本数门控，也可能来自预测得分，应结合诊断区分。</div></section></section>
<footer>仅监听 127.0.0.1 · 只读保存的研究文件 · 不调用模型或求解器 · 无上传、命令执行或写入 API · <span id="updated">尚未刷新</span></footer></main>
<script>
'use strict';const $=id=>document.getElementById(id);const mk=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};const num=(v,d=3)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}):v==='infinity'?'∞':'—';const pct=v=>typeof v==='number'?num(v*100,1)+'%':'—';const when=v=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'—';
const sceneNames={static_clean:'静态 · 正常',static_outliers:'静态 · 污染',moving_clean:'运动 · 正常',moving_outliers:'运动 · 污染',poor_geometry:'狭窄几何',accelerating:'加速运动',sparse:'稀疏观测',calibration_shift:'标定偏移',high_contamination:'高污染'};const sceneName=s=>sceneNames[s]||s;const datasetNames={main:'主测试',stress:'压力测试',edge:'观测数边界',sensitivity:'V2 敏感性网格'};const colors=['#098583','#bf6543','#7555a2','#246bba','#a08012','#bf4f83','#557b51','#384c70','#9b693c','#546d73'];let version='v3',epoch=0,currentState=null,events=[],olderAvailable=false,caseData=null,caseOffset=0,caseTotal=0,caseBusy=false,boundaryRows=[],boundarySelected=new Set(),boundaryInitialized=false,eventOpen=new Set();
async function api(path,params={}){const query=new URLSearchParams({version,...params});const response=await fetch(path+'?'+query,{cache:'no-store'});if(!response.ok){let e=await response.json().catch(()=>({error:'请求失败'}));throw Error(e.error||response.status)}return response.json()}
function error(e){$('error').textContent='读取失败：'+e.message+'。可稍后刷新，已有结果不会被替换为演示数据。';$('error').style.display='block'}function empty(host,text){host.replaceChildren(mk('div',text,'empty'))}function details(title,obj){const d=mk('details');d.append(mk('summary',title),mk('pre',typeof obj==='string'?obj:JSON.stringify(obj,null,2)));return d}function table(host,headers,rows){const wrap=mk('div',undefined,'tablewrap'),t=mk('table'),h=mk('tr');headers.forEach(x=>h.append(mk('th',x)));const th=mk('thead');th.append(h);t.append(th);const body=mk('tbody');rows.forEach(r=>{const tr=mk('tr');r.forEach(x=>tr.append(mk('td',String(x??'—'))));body.append(tr)});t.append(body);wrap.append(t);host.append(wrap)}function options(id,items,label=(s=>s),blank=false){const s=$(id),old=s.value;s.replaceChildren();if(blank)s.append(new Option('全部',''));items.forEach(v=>s.append(new Option(label(v),v)));if([...s.options].some(x=>x.value===old))s.value=old}
function pair(dl,k,v){dl.append(mk('dt',k),mk('dd',v))}function metricRecord(metrics,name,split){const v=metrics?.[name]?.[split];return v?.overall||v||{}}
function renderState(s){currentState=s;$('heading').textContent=s.version_label+' · 研究与独立验证';$('source').textContent=s.paths.run+'  →  '+s.paths.results;$('mode').textContent=s.is_replay?'REPLAY · 保存决策回放':s.provider==='codex_cli'?'LIVE · 真实模型证据':'暂无真实模型记录';$('mode').className='tag'+(s.is_replay?' warn':'');const states={waiting:'等待研究开始',running:'研究运行中',frozen:'方法已冻结',stopped:'研究已停止',provider_failed:'模型调用失败',budget_exhausted:'预算耗尽'};$('status').textContent=states[s.status]||s.status;$('status').className='tag'+(s.available?'':' warn');$('steps').textContent=s.available?String(s.steps):'—';$('stepnote').textContent=s.available?'动作预算 '+(s.budgets.max_steps??'未记录'):'未生成 state.json';$('calls').textContent=s.available?String(s.model_calls):'—';$('callnote').textContent='成功响应 '+(s.available?s.successful_model_calls:'—')+' 次';$('critics').textContent=s.available?String(s.critiques.length):'—';const metrics=s.benchmark.metrics||{},selected=Object.keys(metrics).find(n=>n==='revision_selected')||Object.keys(metrics).find(n=>n==='llm_selected'),m=metricRecord(metrics,selected,'main');$('mean').textContent=num(m.mean_m)+' m';$('meannote').textContent=selected?selected+' · '+(m.n??'—')+' 例':'尚无冻结后的主测试结果';const host=$('metrics');host.replaceChildren();if(Object.keys(metrics).length){table(host,['方法','主测试均值 / m','主测试 CEP90 / m','压力均值 / m','边界均值 / m'],Object.keys(metrics).map(name=>[name,num(metricRecord(metrics,name,'main').mean_m),num(metricRecord(metrics,name,'main').p90_m),num(metricRecord(metrics,name,'stress').mean_m),num(metricRecord(metrics,name,'edge').mean_m)]));host.append(mk('p','评测状态：'+s.benchmark.status+'；预测数 '+num(s.benchmark.prediction_count,0)+'。','muted'))}else empty(host,s.benchmark.status==='running'?'评测已开始；尚未保存完整汇总。':'本轮尚无独立评测汇总。');if(Object.keys(s.benchmark.aliases||{}).length)host.append(mk('p','配置别名 / 共享计算：'+Object.entries(s.benchmark.aliases).map(([a,b])=>a+' = '+b).join('；')+'。这些不是独立重复试验。','muted'));const cp=$('comparisons');cp.replaceChildren();const comparisons=s.benchmark.comparisons||{};function compare(name,c){if(typeof c?.mean_reduction_m!=='number')return;if(c.mean_reduction_m<0)cp.append(mk('p',name+'：所选方法的平均误差较对照高 '+num(-c.mean_reduction_m)+' m。','negative'));else if(c.ci95_m?.[0]<=0&&c.ci95_m?.[1]>=0)cp.append(mk('p',name+'：配对改善区间包含 0。','muted'))}for(const [key,c]of Object.entries(comparisons)){if(typeof c.mean_reduction_m==='number')compare(key,c);else for(const [n,x]of Object.entries(c||{}))compare((datasetNames[key]||key)+' / '+n,x)}const runtime=$('runtime');runtime.replaceChildren();pair(runtime,'模型标识',s.model_identifiers?.join(' / ')||'CLI 未报告');pair(runtime,'推理设置',s.provider_settings?.reasoning_effort||'未报告');pair(runtime,'实验 / 消融',String(s.experiment_calls??'—'));pair(runtime,'验证调用',String(s.tool_counts?.validation_calls??'—'));pair(runtime,'输入 tokens',num(s.usage?.input_tokens,0));pair(runtime,'输出 tokens',num(s.usage?.output_tokens,0));pair(runtime,'响应记录耗时',num(s.recorded_model_latency_seconds,1)+' 秒');pair(runtime,'当前等待',s.pending_request?.request_id||s.pending_tool?.action||'无挂起请求');$('goal').textContent=s.goal||'尚无目标记录。';const conclusion=$('conclusion');conclusion.replaceChildren();const frozen=s.frozen_method?.scientific_conclusion||s.benchmark.frozen?.scientific_conclusion;conclusion.append(mk('p',frozen||'本轮尚未保存冻结结论。','allgood'),mk('p','合成子问题结果不构成原工业问题验收。模型用量缺失不等于零；保存日志也不是第三方认证。','muted'));const methods=$('methods');methods.replaceChildren();for(const x of s.methods||[]){const card=mk('article',undefined,'method');card.append(mk('strong',x.method_id+(x.is_baseline?' · 基线':'')),mk('p',x.hypothesis||'未记录假设','muted'),details('配置与数学定义',{spec:x.spec,definition:x.mathematical_definition}));methods.append(card)}if(!s.methods?.length)empty(methods,'尚无注册方法。');const review=$('critique');review.replaceChildren();const last=s.critiques?.at(-1);if(last){(last.issues||[]).forEach(x=>review.append(mk('p',x,'negative')));review.append(details('完整审阅记录',last))}else empty(review,'尚无独立批评记录。');$('updated').textContent='最近读取 '+when(s.loaded_at)}
function renderEvents(){const filter=$('eventFilter').value,host=$('events');host.replaceChildren();const visible=events.filter(e=>filter==='all'||filter==='model'&&e.type.startsWith('model_')||filter==='tool'&&e.type.startsWith('tool_')||filter==='critic'&&(e.type==='critique'||e.purpose==='critic')||filter==='failure'&&(e.failed||e.title?.includes('失败'))).sort((a,b)=>b.sequence-a.sequence);for(const e of visible){const card=mk('article',undefined,'event'+(e.failed?' failed':e.type==='critique'||e.purpose==='critic'?' critic':e.type==='model_response'?' model':''));card.append(mk('div','#'+e.sequence+' · '+when(e.time_utc)+(e.replay?' · REPLAY':''),'muted'),mk('div',e.title,'title'));if(e.summary)card.append(mk('p',e.summary));if(e.hypothesis)card.append(mk('p','假设：'+e.hypothesis,'note'));if(e.expected_result)card.append(mk('p','预期：'+e.expected_result,'muted'));const d=details('参数、工具返回与诊断',e.details||{});d.open=eventOpen.has(e.sequence);d.addEventListener('toggle',()=>d.open?eventOpen.add(e.sequence):eventOpen.delete(e.sequence));card.append(d);host.append(card)}if(!visible.length)empty(host,'该类别尚无保存记录。');$('older').disabled=!olderAvailable;$('eventCount').textContent='已载入 '+events.length+' 条保存事件；当前筛选 '+visible.length+' 条。新事件在上方。'}
async function loadEvents(older=false){const token=epoch,before=older&&events.length?Math.min(...events.map(e=>e.sequence)):0;const r=await api('/api/events',{before,limit:150});if(token!==epoch)return;const map=new Map(events.map(e=>[e.sequence,e]));r.events.forEach(e=>map.set(e.sequence,e));events=[...map.values()];if(older||events.length===r.events.length)olderAvailable=r.has_older;renderEvents()}
async function loadCases(reset=false){if(caseBusy&&!reset)return;caseBusy=true;const token=epoch;if(reset)caseOffset=0;try{const r=await api('/api/cases',{scene:$('scene').value,method:$('caseMethod').value,dataset:$('dataset').value,q:$('caseSearch').value,order:$('caseOrder').value,offset:caseOffset,limit:60});if(token!==epoch)return;options('scene',r.scenarios,sceneName,true);options('dataset',r.datasets,s=>datasetNames[s]||s,true);options('caseMethod',r.methods);$('caseMethod').value=r.method;caseTotal=r.total;const host=$('caseList');host.replaceChildren();r.rows.forEach(x=>{const b=mk('button',undefined,'caseitem'+(caseData?.key===x.key?' active':''));b.append(mk('strong',num(x.error_m)+' m · '+sceneName(x.scenario)),mk('div',(datasetNames[x.dataset]||x.dataset)+' · '+(x.observation_n??'—')+' 份观测 · '+(x.selected_model==='velocity'?'选择运动':x.selected_model==='static'?'选择静态':'模型未报告'),'muted'),mk('div',x.case_id,'id'));b.dataset.key=x.key;b.onclick=()=>loadCase(x.key);host.append(b)});$('caseCount').textContent='匹配 '+r.total+' 例 · '+(r.rows.length?caseOffset+1:0)+'–'+(caseOffset+r.rows.length);$('prevCases').disabled=caseOffset===0;$('nextCases').disabled=caseOffset+r.rows.length>=r.total;$('archiveNote').textContent=r.warnings.length?r.warnings.join('；'):'按 '+r.method+' 的实际误差排序，点击案例对比保存的多种方法。';if(!r.rows.length){empty(host,'没有匹配的已保存案例。');if(!caseData)drawCase()}else if(!caseData||reset)await loadCase(r.rows[0].key)}catch(e){if(token===epoch)error(e)}finally{caseBusy=false}}
async function loadCase(key){const token=epoch;try{const r=await api('/api/case',{key});if(token!==epoch)return;caseData=r;document.querySelectorAll('.caseitem').forEach(b=>b.classList.toggle('active',b.dataset.key===key));$('caseTitle').textContent=(datasetNames[r.dataset]||r.dataset)+' · '+sceneName(r.scenario)+' · '+r.case_id;const n=r.observation?.anchors?.length||0;$('circleRange').max=n;$('circleRange').value=n;const host=$('caseMetrics');host.replaceChildren();table(host,['方法','误差 / m','选择模型','速度 / m·s⁻¹','工作量'],Object.values(r.predictions).map(p=>[p.method,num(p.error_m),p.selected_model==='velocity'?'恒速':p.selected_model==='static'?'静态':p.selected_model,Array.isArray(p.velocity)?p.velocity.map(x=>num(x,2)).join(', '):'—',num(p.work_units,0)]));const d=$('caseDiagnostics');d.replaceChildren();Object.values(r.predictions).forEach(p=>d.append(details(p.method+' · 数值诊断与 CV 证据',{alias_of:p.alias_of,timing_reused:p.timing_reused,...p.diagnostics})));drawCase()}catch(e){error(e)}}
function canvasContext(id){const canvas=$(id),rect=canvas.getBoundingClientRect(),ratio=devicePixelRatio||1;canvas.width=Math.max(1,Math.round(rect.width*ratio));canvas.height=Math.max(1,Math.round(rect.height*ratio));const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.clearRect(0,0,rect.width,rect.height);return {ctx,w:rect.width,h:rect.height}}
function point(p){return Array.isArray(p)&&p.length>=2&&p.slice(0,2).every(x=>typeof x==='number'&&Number.isFinite(x))}function arrow(ctx,a,b,color){ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();const angle=Math.atan2(b[1]-a[1],b[0]-a[0]);ctx.beginPath();ctx.moveTo(...b);ctx.lineTo(b[0]-8*Math.cos(angle-.45),b[1]-8*Math.sin(angle-.45));ctx.lineTo(b[0]-8*Math.cos(angle+.45),b[1]-8*Math.sin(angle+.45));ctx.closePath();ctx.fill()}
function drawCase(){const {ctx,w,h}=canvasContext('casePlot');ctx.font='12px system-ui';if(!caseData){ctx.fillStyle='#647986';ctx.fillText('请选择一个已保存的评测案例。',25,45);return}const obs=caseData.observation||{},anchors=(obs.anchors||[]).filter(point),pred=Object.values(caseData.predictions),truth=obs.truth,showTruth=$('showTruth').checked,showV=$('showVelocity').checked,pts=[...anchors,...pred.map(p=>p.position).filter(point)];if(showTruth&&point(truth))pts.push(truth);if(showV)for(const p of pred)if(point(p.position)&&point(p.velocity))pts.push(p.position.map((x,i)=>x+5*p.velocity[i]));if(!pts.length){ctx.fillText('该案例尚无可绘制的位置 / 观测数组。',25,45);return}let xmin=Math.min(...pts.map(p=>p[0])),xmax=Math.max(...pts.map(p=>p[0])),ymin=Math.min(...pts.map(p=>p[1])),ymax=Math.max(...pts.map(p=>p[1]));const span=Math.max(xmax-xmin,ymax-ymin,10),cx=(xmin+xmax)/2,cy=(ymin+ymax)/2,scale=Math.min(w-80,h-70)/(span*1.2),map=p=>[w/2+(p[0]-cx)*scale,h/2-(p[1]-cy)*scale];ctx.strokeStyle='#e1e9ee';ctx.lineWidth=1;ctx.fillStyle='#708591';for(let j=0;j<=4;j++){const x=40+(w-80)*j/4,y=35+(h-70)*j/4;ctx.beginPath();ctx.moveTo(x,25);ctx.lineTo(x,h-25);ctx.moveTo(25,y);ctx.lineTo(w-25,y);ctx.stroke();ctx.fillText(((x-w/2)/scale+cx).toFixed(0),x-10,h-9);ctx.fillText(((h/2-y)/scale+cy).toFixed(0),4,y+4)}ctx.fillText('x / m',w-43,16);ctx.fillText('y / m',8,16);const circleIndex=Number($('circleRange').value),all=circleIndex>=anchors.length;$('circleLabel').textContent=all?'全部 '+anchors.length+' 圆':'第 '+(circleIndex+1)+' 圆 · t='+num(obs.times?.[circleIndex],1)+' s';ctx.save();ctx.beginPath();ctx.rect(26,24,w-52,h-49);ctx.clip();if($('showCircles').checked){anchors.forEach((a,i)=>{if(!all&&i!==circleIndex)return;const r=obs.ranges?.[i];if(typeof r!=='number'||!Number.isFinite(r)||r<0)return;ctx.strokeStyle=all?'rgba(31,124,158,.16)':'rgba(31,124,158,.7)';ctx.lineWidth=all?1:2;ctx.beginPath();ctx.arc(...map(a),r*scale,0,Math.PI*2);ctx.stroke()})}anchors.forEach((p,i)=>{const a=map(p);ctx.fillStyle='#6d8d9e';ctx.beginPath();ctx.moveTo(a[0],a[1]-4);ctx.lineTo(a[0]-4,a[1]+4);ctx.lineTo(a[0]+4,a[1]+4);ctx.closePath();ctx.fill();if(anchors.length<=16)ctx.fillText(String(i),a[0]+5,a[1]-4)});if(showTruth&&point(truth)){const a=map(truth);ctx.strokeStyle='#152c3c';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(a[0]-6,a[1]-6);ctx.lineTo(a[0]+6,a[1]+6);ctx.moveTo(a[0]-6,a[1]+6);ctx.lineTo(a[0]+6,a[1]-6);ctx.stroke();ctx.fillStyle='#152c3c';ctx.fillText('评测真值',a[0]+8,a[1]-8);if(showV&&point(obs.velocity))arrow(ctx,a,map(truth.map((x,i)=>x+5*obs.velocity[i])),'#152c3c')}const legend=$('plotLegend');legend.replaceChildren();pred.forEach((p,i)=>{const color=colors[i%colors.length],label=mk('span',p.method);label.style.setProperty('--color',color);legend.append(label);if(!point(p.position))return;const a=map(p.position),focus=p.method===$('caseMethod').value;ctx.fillStyle=color;ctx.beginPath();ctx.arc(...a,focus?6:4,0,Math.PI*2);ctx.fill();if(focus){ctx.strokeStyle='white';ctx.lineWidth=2;ctx.stroke();ctx.fillText(p.method,a[0]+8,a[1]+15)}if(showV&&point(p.velocity))arrow(ctx,a,map(p.position.map((x,j)=>x+5*p.velocity[j])),color)});ctx.restore()}
async function loadCalibration(){const token=epoch,r=await api('/api/calibration');if(token!==epoch)return;const host=$('calibrationTable'),d=$('calibrationDetails');host.replaceChildren();d.replaceChildren();if(!r.rows.length){empty(host,'本轮尚无可识别的独立校准结果。');return}const modes=new Set(r.rows.map(x=>x.mode));if(!modes.has('mondrian'))host.append(mk('p','本版本未保存 Mondrian 校准；以下只显示 Global。','note warn'));const pops={iid_test:'同分布覆盖测试',shifted_stress:'偏移压力集',edge:'观测数边界'};table(host,['校准方式','名义覆盖','评估总体','案例数','实际覆盖','95% Wilson 区间','均值半径 / m','无限半径数'],r.rows.map(x=>[x.mode==='global'?'Global · 总体':'Mondrian · 分组',pct(1-Number(x.alpha)),pops[x.population]||x.population,num(x.n,0),pct(x.coverage),Array.isArray(x.coverage_wilson95)?x.coverage_wilson95.map(pct).join(' – '):'不适用 / 未报告',num(x.mean_radius_m),num(x.unbounded_n,0)]));for(const r0 of r.rows){const entry=mk('details');entry.append(mk('summary',(r0.mode==='global'?'Global':'Mondrian')+' · '+pct(1-Number(r0.alpha))+' · '+(pops[r0.population]||r0.population)+'：分场景 / 分组 / 拒绝'));const sub=mk('div');if(r0.rejection_curve?.length)table(sub,['半径阈值 / m','接受数','接受率','接受后误差 / m','接受后覆盖'],r0.rejection_curve.map(x=>[num(x.radius_threshold_m),num(x.accepted_n,0),pct(x.acceptance_rate),num(x.accepted_mean_error_m),pct(x.accepted_coverage)]));sub.append(details('分场景、分组与校准顺序统计量',{by_scenario:r0.by_scenario,by_group:r0.by_group,calibration:r0.calibration,unbounded:r0.unbounded,unbounded_n:r0.unbounded_n,median_radius_m:r0.median_radius_m}));entry.append(sub);d.append(entry)}}
async function loadBoundary(){const token=epoch,r=await api('/api/boundary');if(token!==epoch)return;boundaryRows=r.rows;$('boundaryNote').textContent=r.notice;options('boundaryScene',[...new Set(r.rows.map(x=>x.scenario))],sceneName);const methods=[...new Set(r.rows.map(x=>x.method))],host=$('boundaryMethods');host.replaceChildren();if(!boundaryInitialized&&methods.length){boundarySelected=new Set(methods.filter(x=>['revision_selected','v2_frozen','dynamic_robust','ablate_k3'].includes(x)));boundaryInitialized=true;}methods.forEach((name,i)=>{const label=mk('label'),c=mk('input');c.type='checkbox';c.checked=boundarySelected.has(name);c.onchange=()=>{c.checked?boundarySelected.add(name):boundarySelected.delete(name);drawBoundary()};label.append(c,document.createTextNode(' '+name));label.style.color=colors[i%colors.length];host.append(label)});drawBoundary()}
function drawBoundary(){const {ctx,w,h}=canvasContext('boundaryPlot'),metric=$('boundaryMetric').value,scene=$('boundaryScene').value,host=$('boundaryTable');host.replaceChildren();if(!boundaryRows.length){ctx.fillStyle='#647986';ctx.font='13px system-ui';ctx.fillText('本轮尚无 n=6…12 的独立边界结果。',25,45);return}const methods=[...new Set(boundaryRows.map(x=>x.method))],rows=boundaryRows.filter(x=>x.scenario===scene&&boundarySelected.has(x.method)),values=rows.map(x=>x[metric]).filter(x=>typeof x==='number'&&Number.isFinite(x)),max=metric.endsWith('_rate')?1:Math.max(...values,1)*1.1,left=55,right=w-25,top=25,bottom=h-45,map=(n,v)=>[left+(n-6)*(right-left)/6,bottom-v/max*(bottom-top)];ctx.font='11px system-ui';ctx.lineWidth=1;for(let j=0;j<=4;j++){const y=bottom-j*(bottom-top)/4;ctx.strokeStyle='#dee7ed';ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillStyle='#637b8a';ctx.fillText((max*j/4).toFixed(metric.endsWith('_rate')?2:1),4,y+4)}for(let n=6;n<=12;n++){ctx.fillText(String(n),map(n,0)[0]-4,bottom+20)}ctx.fillText('观测数 n',right-48,h-6);for(const name of methods){if(!boundarySelected.has(name))continue;const list=rows.filter(x=>x.method===name).sort((a,b)=>a.observation_n-b.observation_n),color=colors[methods.indexOf(name)%colors.length];ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=2;let prev=null;for(const r of list){if(typeof r[metric]!=='number'||!Number.isFinite(r[metric])){prev=null;continue}const p=map(r.observation_n,r[metric]);if(prev&&r.observation_n===prev.n+1){ctx.beginPath();ctx.moveTo(...prev.p);ctx.lineTo(...p);ctx.stroke()}ctx.beginPath();ctx.arc(...p,3,0,Math.PI*2);ctx.fill();prev={n:r.observation_n,p}}}table(host,['方法','n','案例数','平均误差 / m','CEP90 / m','运动选择','CV 可用'],rows.map(x=>[x.method,x.observation_n,x.n,num(x.mean_m),num(x.p90_m),pct(x.velocity_rate),pct(x.cv_admissible_rate)]))}
async function refresh(){const token=epoch;try{const s=await api('/api/state');if(token!==epoch)return;$('error').style.display='none';renderState(s);await loadEvents();await Promise.all([loadCalibration(),loadBoundary()]);if($('cases').classList.contains('active')&&!caseData)await loadCases(true)}catch(e){if(token===epoch)error(e)}}
function switchVersion(v){version=v;epoch++;currentState=null;events=[];olderAvailable=false;caseData=null;caseOffset=0;eventOpen.clear();boundaryRows=[];boundarySelected.clear();boundaryInitialized=false;for(const id of ['scene','dataset','caseMethod','boundaryScene'])$(id).replaceChildren();$('caseSearch').value='';document.querySelectorAll('[data-version]').forEach(b=>b.classList.toggle('active',b.dataset.version===v));$('caseTitle').textContent='请选择案例';$('caseMetrics').replaceChildren();$('caseDiagnostics').replaceChildren();$('plotLegend').replaceChildren();refresh();loadCases(true)}
document.querySelectorAll('[data-version]').forEach(b=>b.onclick=()=>switchVersion(b.dataset.version));document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('active',x===b));document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===b.dataset.tab));if(b.dataset.tab==='cases'){loadCases();setTimeout(drawCase,0)}if(b.dataset.tab==='boundary')setTimeout(drawBoundary,0)});$('refresh').onclick=()=>{refresh();if($('cases').classList.contains('active'))loadCases()};$('eventFilter').onchange=renderEvents;$('older').onclick=()=>loadEvents(true).catch(error);$('search').onclick=()=>loadCases(true);for(const id of ['scene','dataset','caseMethod','caseOrder'])$(id).onchange=()=>loadCases(true);$('caseSearch').onkeydown=e=>{if(e.key==='Enter')loadCases(true)};$('prevCases').onclick=()=>{caseOffset=Math.max(0,caseOffset-60);loadCases()};$('nextCases').onclick=()=>{caseOffset+=60;loadCases()};for(const id of ['showCircles','showTruth','showVelocity','circleRange'])$(id).oninput=drawCase;for(const id of ['boundaryScene','boundaryMetric'])$(id).onchange=drawBoundary;window.addEventListener('resize',()=>{if($('cases').classList.contains('active'))drawCase();if($('boundary').classList.contains('active'))drawBoundary()});switchVersion('v3');setInterval(refresh,8000);
</script></body></html>'''


def make_handler(data):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, value, html=False):
            body = (value if html else json.dumps(json_safe(value), ensure_ascii=False, allow_nan=False)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            port = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self.send(403, {"error": "仅允许本机 Host 请求"})
                return
            target = urlsplit(self.path)
            if target.path == "/" and not target.query:
                self.send(200, HTML, html=True)
                return
            routes = {"/api/state": set(), "/api/events": {"before", "limit"},
                      "/api/cases": {"scene", "method", "dataset", "q", "order", "offset", "limit"},
                      "/api/case": {"key"}, "/api/calibration": set(), "/api/boundary": set()}
            if target.path not in routes:
                self.send(404, {"error": "不存在该只读接口"})
                return
            try:
                query = parse_qs(target.query, keep_blank_values=True)
                if set(query) - routes[target.path] - {"version"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError("不允许未知或重复参数")
                values = {k: v[0] for k, v in query.items()}
                if any(len(v) > 600 for v in values.values()):
                    raise ValueError("查询参数过长")
                version = values.pop("version", "v3")
                data.config(version)
                if target.path == "/api/state":
                    result = data.state(version)
                elif target.path == "/api/events":
                    before, limit = int(values.get("before", 0)), int(values.get("limit", 150))
                    if before < 0 or not 1 <= limit <= 200:
                        raise ValueError("before 必须非负，limit 为 1–200")
                    result = data.events(version, before, limit)
                elif target.path == "/api/cases":
                    offset, limit = int(values.pop("offset", 0)), int(values.pop("limit", 80))
                    if offset < 0 or not 1 <= limit <= 200 or values.get("order", "desc") not in ("asc", "desc", "id"):
                        raise ValueError("分页或排序参数无效")
                    values["query"] = values.pop("q", "")
                    result = data.cases(version, offset=offset, limit=limit, **values)
                elif target.path == "/api/case":
                    result = data.case(version, values.get("key", ""))
                elif target.path == "/api/calibration":
                    result = data.calibration(version)
                else:
                    result = data.boundary(version)
                self.send(200, result)
            except ValueError as error:
                self.send(400, {"error": str(error)})
            except KeyError:
                self.send(404, {"error": "保存结果中没有此案例"})
            except (OSError, TypeError, AttributeError):
                self.send(503, {"error": "结果文件尚未完整或格式不兼容，请稍后刷新"})

        def deny_write(self):
            self.send(405, {"error": "只读界面，仅支持 GET"})

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = deny_write

        def log_message(self, *args):
            pass

    return Handler


def create_server(root, port=8766):
    return ThreadingHTTPServer(("127.0.0.1", int(port)), make_handler(RevisionDashboardData(root)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("--root 必须为项目目录")
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须为 1–65535")
    server = create_server(args.root, args.port)
    print(f"CrowdLocateLab 研究浏览器：http://127.0.0.1:{server.server_address[1]}", flush=True)
    print("仅显示保存的研究证据；按 Ctrl+C 关闭界面，不影响智能体任务。", flush=True)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
