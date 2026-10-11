"""Serve a read-only, standard-library dashboard of saved research evidence.

Only 127.0.0.1 is bound. There is no file-serving endpoint, command endpoint,
mutation API, model call, or external browser dependency.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def as_object(value):
    return value if isinstance(value, dict) else {}


def as_list(value):
    return value if isinstance(value, list) else []


def object_members(value):
    return {key: item for key, item in as_object(value).items() if isinstance(item, dict)}


def strict_json(text):
    """Treat duplicate keys and non-finite JSON numbers as unavailable evidence."""
    def number(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON number")
        return value
    def constant(token):
        raise ValueError("Non-standard JSON number")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, parse_float=number, parse_constant=constant, object_pairs_hook=pairs)


def path_allowed(path, boundary=None):
    try:
        return boundary is None or Path(path).resolve().is_relative_to(Path(boundary).resolve())
    except (OSError, RuntimeError):
        return False


def read_object(path: Path, boundary=None):
    """An absent or temporarily incomplete known JSON file is not a fatal UI error."""
    try:
        if not path_allowed(path, boundary):
            return {}
        value = strict_json(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return {}


def iter_events(path: Path, boundary=None):
    try:
        if not path_allowed(path, boundary):
            return
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = strict_json(line)
                    if (isinstance(event, dict) and type(event.get("sequence")) is int
                            and event["sequence"] > 0 and isinstance(event.get("type"), str)
                            and isinstance(event.get("payload"), dict)):
                        required = {"model_response": "response", "tool_result": "result"}.get(event["type"])
                        if required and not isinstance(event["payload"].get(required), dict):
                            continue
                        yield event
                except ValueError:
                    # The live writer may be between bytes of its final JSONL row.
                    continue
    except (FileNotFoundError, PermissionError, OSError, UnicodeError):
        return


def _short(value, limit=12000):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return text if len(text) <= limit else text[:limit] + "\n……界面摘要已截断，完整结果保存在运行日志。"


def _event_view(event):
    """Expose research evidence, never raw prompts, CLI stderr, or arbitrary files."""
    kind, payload = event.get("type", "unknown"), as_object(event.get("payload"))
    result = {"sequence": event["sequence"], "time_utc": event.get("time_utc"),
              "type": kind, "event_sha256": event.get("event_sha256"), "details": ""}
    if kind == "model_request":
        result.update(title="请求独立批评" if payload.get("purpose") == "critic" else "请求模型选择下一步",
                      summary=f"{payload.get('request_id', '')} · 第 {payload.get('attempt', 1)} 次尝试",
                      purpose=payload.get("purpose"), request_id=payload.get("request_id"))
    elif kind == "model_response":
        response = as_object(payload.get("response"))
        result.update(title="独立批评模型已响应" if payload.get("purpose") == "critic" else "模型选择：" + str(response.get("action", "")),
                      summary=response.get("analysis_summary", response.get("recommended_next_step", "")),
                      hypothesis=response.get("hypothesis", ""), expected_result=response.get("expected_result", ""),
                      memory_note=response.get("memory_note", ""), action=response.get("action"),
                      purpose=payload.get("purpose"), replay=bool(payload.get("replay")),
                      request_id=payload.get("request_id"), details=_short({"response": response,
                      "usage": payload.get("usage", {}), "elapsed_seconds": payload.get("elapsed_seconds"),
                      "model_identifier": payload.get("model_identifier"),
                      "unexpected_tools": payload.get("unexpected_tools", []), "replay": payload.get("replay", False)}))
    elif kind == "tool_call":
        result.update(title="调用工具：" + str(payload.get("action", "")),
                      action=payload.get("action"), summary="等待实际工具返回。",
                      details=_short(payload.get("arguments", {})))
    elif kind == "tool_result":
        observation = as_object(payload.get("result"))
        failed = observation.get("ok") is False or bool(observation.get("error"))
        action = payload.get("action", "")
        result.update(title=("工具失败：" if failed else "工具返回：") + str(action),
                      action=action, failed=failed, summary=_tool_summary(action, observation),
                      details=_short(observation), executed=payload.get("executed", True))
    elif kind == "critique":
        result.update(title="独立批评记录", summary=payload.get("recommended_next_step", ""),
                      issues=as_list(payload.get("issues")), scope_valid=payload.get("scope_valid"),
                      details=_short(payload))
    else:
        decision = payload.get("decision")
        if not isinstance(decision, str):
            decision = None
        titles = {"initialize": "研究运行初始化", "resume": "恢复既有研究",
                  "development_frozen": "研究方法已冻结", "manual_stop": "手动停止",
                  "budget_exhausted": "研究预算已耗尽", "provider_failed": "模型提供方调用失败",
                  "model_request_failed": "一次模型调用失败", "unexpected_provider_tools_detected": "检测到 CLI 自身工具调用"}
        result.update(title=titles.get(decision, "研究决策：" + str(payload.get("action", decision or "记录"))),
                      summary=payload.get("reason", payload.get("error", payload.get("analysis_summary", ""))),
                      action=payload.get("action"), hypothesis=payload.get("hypothesis", ""),
                      details=_short(payload))
    return result


def _tool_summary(action, observation):
    if observation.get("error"):
        return str(observation["error"])
    if action in ("run_experiment", "ablate"):
        metrics = object_members(observation.get("metrics"))
        parts = []
        for name, values in metrics.items():
            overall = as_object(values.get("overall"))
            mean = overall.get("mean_m")
            parts.append(f"{name}: 平均误差 {mean:.3f} m" if type(mean) in (int, float) and math.isfinite(mean) else name)
        return f"{observation.get('split', '')} · " + "；".join(parts)
    if action == "register_method":
        return "已注册 " + str(observation.get("registered", observation.get("method_id", "新方法")))
    if action == "diagnose":
        cases = as_list(observation.get("worst_cases"))
        return f"检查了 {len(cases)} 个失败个案；保留 case_id、误差与模型诊断。"
    if action == "read_sources":
        return "已读取 " + "、".join(as_object(observation.get("sources")).keys())
    if action == "freeze":
        return str(observation.get("scientific_conclusion", "配置冻结，后续测试由独立评估程序执行。"))
    return str(observation.get("note", observation.get("warning", "结果已写入完整事件记录。")))


class DashboardData:
    def __init__(self, run: Path, results: Path):
        self.run, self.results = Path(run).resolve(), Path(results).resolve()
        # These exact paths are the only filesystem data sources exposed to HTTP.
        self.state_path = self.run / "state.json"
        self.events_path = self.run / "events.jsonl"
        self.tools_path = self.run / "tools" / "tool_state.json"
        self.frozen_path = self.run / "tools" / "frozen_method.json"
        self.summary_path = self.results / "summary.json"
        self.conformal_path = self.results / "conformal.json"
        self.benchmark_protocol_path = self.results / "protocol.json"

    def state(self):
        state, tools = read_object(self.state_path, self.run), read_object(self.tools_path, self.run)
        if "identity" in state and not isinstance(state["identity"], dict):
            state = {}
        identity = as_object(state.get("identity"))
        usage, model_ids, latest_request, completed_requests = {}, set(), None, set()
        model_latency = 0.
        successful_calls = 0
        for event in iter_events(self.events_path, self.run):
            payload = event.get("payload", {})
            if event.get("type") == "model_request":
                latest_request = {"request_id": payload.get("request_id"),
                                  "purpose": payload.get("purpose"), "time_utc": event.get("time_utc")}
            elif event.get("type") == "model_response":
                successful_calls += 1
                if isinstance(payload.get("request_id"), str):
                    completed_requests.add(payload["request_id"])
                latency = payload.get("elapsed_seconds")
                if type(latency) in (int, float) and math.isfinite(latency) and latency >= 0:
                    model_latency += latency
                for name, value in as_object(payload.get("usage")).items():
                    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                        usage[name] = usage.get(name, 0) + value
                if payload.get("model_identifier"):
                    model_ids.add(str(payload["model_identifier"]))
            elif event.get("type") == "decision" and payload.get("decision") == "model_request_failed":
                if isinstance(payload.get("request_id"), str):
                    completed_requests.add(payload["request_id"])
        if latest_request and (not isinstance(latest_request["request_id"], str) or latest_request["request_id"] in completed_requests):
            latest_request = None
        methods = []
        for method_id, record in object_members(tools.get("registry")).items():
            methods.append({"method_id": method_id, **record,
                            "spec": as_object(record.get("spec")),
                            "is_baseline": "baseline" in as_object(record.get("spec")),
                            "evaluations": {split: object_members(tools.get("evaluations")).get(f"{split}:{method_id}")
                                            for split in ("discovery", "validation")}})
        methods.sort(key=lambda item: item["is_baseline"])
        benchmark = read_object(self.summary_path, self.results)
        metrics = object_members(benchmark.get("metrics"))
        has_measurements = any(type(as_object(split.get("overall")).get("mean_m")) in (int, float)
                               for method in metrics.values() for split in object_members(method).values())
        benchmark_status = "complete" if has_measurements else ("unavailable" if self.summary_path.exists() else
                            "running" if self.benchmark_protocol_path.exists() else "not_started")
        if benchmark.get("status") in ("failed", "running", "incomplete"):
            benchmark_status = benchmark["status"]
        critiques = [{**item, "issues": as_list(item.get("issues")),
                      "evidence_caveats": as_list(item.get("evidence_caveats"))}
                     for item in as_list(state.get("critiques")) if isinstance(item, dict)]
        conformal = as_object(benchmark.get("conformal")) or read_object(self.conformal_path, self.results)
        if "results" in conformal:
            conformal = {**conformal, "results": object_members(conformal["results"])}
        return {"loaded_at": datetime.now(timezone.utc).isoformat(), "run_name": self.run.name,
                "available": bool(state), "status": state.get("status", "waiting"),
                "is_replay": bool(state.get("is_replay", identity.get("provider") == "replay")),
                "provider": identity.get("provider", "unknown"), "frozen": bool(state.get("frozen")),
                "steps": state.get("steps", 0), "model_calls": state.get("model_calls", 0),
                "successful_model_calls": successful_calls, "experiment_calls": state.get("experiment_calls", 0),
                "successful_experiments": state.get("successful_experiments", 0),
                "budgets": {key: identity.get(key) for key in ("max_steps", "max_model_calls", "max_experiments")},
                "goal": identity.get("goal", ""), "provider_settings": as_object(identity.get("provider_settings")),
                "identity_sha256": state.get("identity_sha256"), "seed_hash": identity.get("seed_hash"),
                "updated_at": state.get("updated_at"), "last_event_sequence": state.get("last_event_sequence", 0),
                "pending_request": latest_request, "pending_tool": state.get("pending_tool"),
                "methods": methods, "tool_counts": {key: tools.get(key, 0)
                for key in ("experiment_calls", "validation_calls", "diagnoses", "math_checks")},
                "usage": usage, "recorded_model_latency_seconds": model_latency,
                "model_identifiers": sorted(model_ids), "unexpected_tool_events": len(as_list(state.get("unexpected_tool_events"))),
                "critiques": critiques, "frozen_method": read_object(self.frozen_path, self.run),
                "benchmark": {"status": benchmark_status, "metrics": metrics,
                "comparisons": object_members(benchmark.get("comparisons")),
                "conformal": conformal,
                "prediction_count": benchmark.get("prediction_count"), "wall_seconds": benchmark.get("wall_seconds"),
                "frozen": as_object(benchmark.get("frozen"))}}

    def events(self, after=0, limit=100):
        latest = deque(maxlen=min(max(int(limit), 1), 100))
        latest_sequence = 0
        for event in iter_events(self.events_path, self.run):
            latest_sequence = max(latest_sequence, event["sequence"])
            if event["sequence"] > after:
                latest.append(_event_view(event))
        return {"events": list(latest), "latest_sequence": latest_sequence,
                "limit": latest.maxlen, "after": after}


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CrowdLocateLab · 科研智能体观测台</title>
<style>
:root{--navy:#18364d;--teal:#0c827b;--muted:#617587;--line:#dce5eb;--bg:#eff4f7;--gold:#9c680c;--red:#a64540;--white:#fff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--navy);font:15px/1.55 system-ui,-apple-system,"Microsoft YaHei",sans-serif}button{font:inherit;cursor:pointer}
.top{background:var(--navy);color:white;padding:24px max(28px,calc((100vw - 1440px)/2));display:flex;justify-content:space-between;align-items:center;gap:20px}.brand{font-size:25px;font-weight:750;letter-spacing:.2px}.brand small{display:block;font-size:13px;font-weight:400;opacity:.75;margin-top:4px}.topright{text-align:right;font-size:12px;opacity:.9}.topright button{display:block;margin:7px 0 0 auto;border:1px solid #69808f;background:transparent;color:white;padding:4px 13px;border-radius:6px}
main{max-width:1440px;margin:auto;padding:24px 28px 45px}.hero{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:18px}.hero h1{font-size:22px;margin:0 0 5px}.hero p{margin:0;color:var(--muted);font-size:13px}.badges{display:flex;gap:8px;flex-wrap:wrap}.badge{display:inline-block;padding:5px 11px;border-radius:6px;background:#dceeea;color:#075f5a;font-size:12px;font-weight:650}.badge.replay{background:#fff0ce;color:#825706}.badge.neutral{background:#e2eaf0;color:#425d72}.badge.failed{background:#f5e3df;color:var(--red)}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}.stat,.panel{background:white;border:1px solid var(--line);border-radius:10px}.stat{padding:18px 20px}.stat .label{font-size:12px;color:var(--muted)}.stat strong{font-size:31px;display:inline-block;margin:3px 0}.stat strong span{font-size:15px;font-weight:400;color:var(--muted)}.stat .sub{font-size:12px;color:var(--muted)}.grid{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(325px,1fr);gap:18px;align-items:start}.stack{display:grid;gap:18px}.panel{padding:20px}.panel h2{font-size:17px;margin:0 0 14px}.panel h3{font-size:13px;margin:18px 0 8px}.muted{color:var(--muted);font-size:12px}.panelhead{display:flex;align-items:center;justify-content:space-between;gap:12px}.filters{display:flex;gap:5px;flex-wrap:wrap}.filters button{border:0;border-radius:5px;background:#eef3f6;padding:4px 8px;font-size:12px;color:var(--muted)}.filters button.active{background:#dceeea;color:var(--teal)}
.timeline{max-height:920px;overflow:auto;padding-right:6px}.event{position:relative;margin:0 0 16px 7px;padding:0 0 16px 18px;border-left:2px solid var(--line)}.event:before{position:absolute;content:"";width:8px;height:8px;border-radius:50%;background:#8aa1b1;left:-5px;top:7px}.event.model_response:before{background:var(--teal)}.event.critique:before{background:var(--gold)}.event.failed:before{background:var(--red)}.event:last-child{border-bottom:0;margin-bottom:0}.event-title{font-weight:650;font-size:14px;overflow-wrap:anywhere}.event-meta{font-size:11px;color:var(--muted);margin-bottom:4px}.event p{font-size:13px;margin:7px 0;white-space:pre-wrap;overflow-wrap:anywhere}.hypothesis{background:#f0f7f6;border-left:3px solid var(--teal);padding:8px 10px}.expected{color:var(--muted)}details{font-size:12px}summary{cursor:pointer;color:var(--teal);margin-top:6px}pre{font:11px/1.55 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-word;background:#f5f7f9;padding:12px;border-radius:6px;max-height:330px;overflow:auto}.kv{display:grid;grid-template-columns:95px 1fr;gap:8px 10px;font-size:12px}.kv dt{color:var(--muted)}.kv dd{margin:0;overflow-wrap:anywhere}.progress{height:5px;background:#e7eef2;border-radius:4px;margin-top:8px;overflow:hidden}.progress i{height:100%;display:block;background:var(--teal)}
.method{border:1px solid var(--line);border-left:3px solid var(--teal);border-radius:6px;padding:12px;margin-top:10px}.method.baseline{border-left-color:#9aacb9;background:#fcfdfe}.methodname{font-size:13px;font-weight:700;overflow-wrap:anywhere}.method p{font-size:12px;margin:6px 0;color:var(--muted)}.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:7px}.chip{font:11px/1.3 ui-monospace,Consolas,monospace;background:#edf3f6;padding:3px 5px;border-radius:3px}.mini{font-size:11px;color:var(--teal);margin-top:8px}.section{margin-top:18px}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}th{text-align:left;color:var(--muted);font-weight:600;background:#f2f6f8}td,th{padding:10px 11px;border-bottom:1px solid var(--line)}tr.selected{background:#eaf5f2}td.number,th.number{text-align:right}.coverage{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:12px}.coverbox{border:1px solid var(--line);border-radius:7px;padding:13px}.coverbox strong{font-size:23px;color:var(--teal)}.coverbox p{margin:4px 0;font-size:12px}.conclusion{font-size:13px;white-space:pre-wrap;background:#f4f7f9;border-radius:6px;padding:12px}.negative{border-left:3px solid var(--gold);padding-left:11px;margin:9px 0;font-size:12px}.empty{color:var(--muted);font-size:13px;padding:18px 0}.errorbar{display:none;background:#fff0df;border:1px solid #e7cda5;color:#83540d;border-radius:6px;padding:10px 14px;font-size:13px;margin-bottom:14px}footer{color:var(--muted);font-size:11px;margin-top:22px}.mono{font-family:ui-monospace,Consolas,monospace}
@media(max-width:1000px){.grid{grid-template-columns:1fr}.timeline{max-height:750px}.stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){main{padding:16px}.top{padding:18px}.brand{font-size:21px}.hero{align-items:start;flex-direction:column}.panel{padding:15px}.stats{gap:10px}.stat{padding:13px}.stat strong{font-size:25px}.coverage{grid-template-columns:1fr}.panelhead{align-items:start;flex-direction:column}}
</style></head><body>
<header class="top"><div class="brand">CrowdLocateLab<small>数学科研智能体 · 真实运行证据观测台</small></div><div class="topright"><span id="connection">正在连接本地日志</span><button id="refresh">立即刷新</button></div></header>
<main><div id="error" class="errorbar"></div><div class="hero"><div><h1>研究过程、方法迭代与独立验证</h1><p id="runlabel">读取运行目录中的真实 JSON / JSONL 记录</p></div><div class="badges"><span id="mode" class="badge">等待状态</span><span id="status" class="badge neutral">—</span></div></div>
<div class="stats"><div class="stat"><div class="label">研究动作</div><strong id="steps">—</strong><div class="sub">每个动作来自已保存的模型决策</div><div class="progress"><i id="stepprogress"></i></div></div><div class="stat"><div class="label">实验与消融调用</div><strong id="experiments">—</strong><div id="experimentnote" class="sub">等待工具执行</div></div><div class="stat"><div class="label">模型调用 · 含重试与批评</div><strong id="calls">—</strong><div id="tokens" class="sub">Token 用量未报告</div></div><div class="stat"><div class="label">注册的新方法</div><strong id="methodsnum">—</strong><div id="methodnote" class="sub">基线单独标记</div></div></div>
<div class="grid"><section class="panel"><div class="panelhead"><h2>动作与观察时间线</h2><div class="filters" id="filters"><button data-filter="all" class="active">全部</button><button data-filter="model">模型</button><button data-filter="tool">工具</button><button data-filter="critic">批评</button></div></div><p class="muted">按事件序号显示最近 100 条；新的事件在上方。可展开实际参数与工具结果。</p><div id="timeline" class="timeline"><div class="empty">暂无事件。</div></div></section>
<div class="stack"><section class="panel"><h2>当前运行</h2><dl id="runtime" class="kv"></dl><details><summary>研究目标与证据标识</summary><p id="goal" class="muted"></p><pre id="identity"></pre></details></section><section class="panel"><h2>方法目录</h2><p class="muted">模型通过声明式算子组成方法；卡片中的假设来自注册记录。</p><div id="methodcards"></div></section><section class="panel"><h2>未解决问题与失败记录</h2><div id="limitations" class="empty">研究尚未冻结。失败工具调用和批评意见会保留在时间线中。</div></section></div></div>
<section class="panel section"><div class="panelhead"><h2>冻结后的独立测试</h2><span id="benchmarkstatus" class="badge neutral">尚未运行</span></div><p class="muted">此处只读取独立评估结果，不触发实验，也不会把测试指标反馈给研究智能体。</p><div id="benchmark"></div><div id="coverage"></div></section>
<footer>仅本机 127.0.0.1 · 每 5 秒读取已保存证据 · 无写入接口、无 shell 接口、无模型调用 · 合成数据实验不等同于原工业难题验收。</footer>
</main>
<script>
'use strict';
const $=id=>document.getElementById(id), opened=new Set();let allEvents=[],filter='all',busy=false;
const number=(v,d=2)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:d,minimumFractionDigits:d}):'—';
const pct=v=>typeof v==='number'?number(v*100,1)+'%':'—';
const date=v=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'—';
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
function budget(id,n,max){$(id).replaceChildren(document.createTextNode(String(n??0)),node('span',' / '+(max??'—')))}
function row(dl,key,value){dl.append(node('dt',key),node('dd',value))}
function renderState(s){
 $('error').style.display='none';$('connection').textContent='只读连接正常 · '+date(s.loaded_at);
 $('runlabel').textContent=s.run_name+' · 5 秒自动刷新 · '+(s.available?'读取已保存运行':'等待 state.json');
 $('mode').textContent=s.is_replay?'REPLAY · 历史决策回放':s.provider==='codex_cli'?'LIVE · 真实大模型运行':'尚无模型运行证据';$('mode').className='badge'+(s.is_replay?' replay':'');
 const statuses={running:'研究运行中',frozen:'方法已冻结',stopped:'已手动停止',budget_exhausted:'预算已耗尽',provider_failed:'模型调用失败',waiting:'等待日志',finished_without_freeze:'停止但尚未冻结'};
 $('status').textContent=statuses[s.status]||s.status;$('status').className='badge '+(s.status==='provider_failed'?'failed':'neutral');
 budget('steps',s.steps,s.budgets.max_steps);budget('experiments',s.experiment_calls,s.budgets.max_experiments);budget('calls',s.model_calls,s.budgets.max_model_calls);
 $('stepprogress').style.width=Math.min(100,100*s.steps/(s.budgets.max_steps||1))+'%';
 $('experimentnote').textContent='验证调用 '+s.tool_counts.validation_calls+' / 4 · 数学检查 '+s.tool_counts.math_checks+' 次';
 const u=s.usage||{};$('tokens').textContent='已报告输入 '+number(u.input_tokens,0)+' / 输出 '+number(u.output_tokens,0)+' tokens';
 const newMethods=s.methods.filter(m=>!m.is_baseline);$('methodsnum').textContent=newMethods.length;$('methodnote').textContent='另有 '+s.methods.filter(m=>m.is_baseline).length+' 个预声明基线';
 const runtime=$('runtime');runtime.replaceChildren();row(runtime,'当前等待',s.pending_tool?'工具 '+s.pending_tool.action:s.pending_request?'模型 '+s.pending_request.request_id:s.frozen?'独立评估可在冻结后进行':'无进行中的调用');
 row(runtime,'模型标识',s.model_identifiers.length?s.model_identifiers.join(' / '):'CLI 未报告；沿用用户配置，不推测名称');
 row(runtime,'推理设置',s.provider_settings.reasoning_effort||'用户默认');row(runtime,'已响应调用',String(s.successful_model_calls));row(runtime,'批评调用',String(s.critiques.length));
 row(runtime,'报告内推理tokens',number(u.reasoning_output_tokens,0));row(runtime,'记录调用耗时',number(s.recorded_model_latency_seconds,1)+' 秒');row(runtime,'额外 CLI 工具',String(s.unexpected_tool_events)+' 次检测记录');row(runtime,'状态更新时间',date(s.updated_at));
 $('goal').textContent=s.goal;$('identity').textContent='配置 SHA256: '+(s.identity_sha256||'—')+'\n种子 hash: '+(s.seed_hash||'—')+'\n最后事件序号: '+s.last_event_sequence;
 const cards=$('methodcards');cards.replaceChildren();if(!s.methods.length)cards.append(node('div','尚无注册方法。','empty'));
 for(const m of s.methods){const card=node('article',undefined,'method'+(m.is_baseline?' baseline':''));card.append(node('div',m.method_id+(m.is_baseline?' · 基线':''),'methodname'));card.append(node('p',m.hypothesis||'未记录假设'));
  const chips=node('div',undefined,'chips');for(const k of ['baseline','model','residual','scale','loss'])if(m.spec&&m.spec[k]!==undefined)chips.append(node('span',k+'='+m.spec[k],'chip'));card.append(chips);
  for(const split of ['discovery','validation']){const result=m.evaluations[split];if(result)card.append(node('div',split+'：平均误差 '+number(result.overall?.mean_m)+' m · P90 '+number(result.overall?.p90_m)+' m','mini'))}
  const d=node('details'),sum=node('summary','数学定义与完整配置');d.append(sum,node('p',m.mathematical_definition||'详见方法定义'),node('pre',JSON.stringify(m.spec,null,2)));card.append(d);cards.append(card)}
 const limits=$('limitations');limits.replaceChildren();limits.className='';const conclusion=s.frozen_method.scientific_conclusion||s.benchmark.frozen.scientific_conclusion;if(conclusion)limits.append(node('div',conclusion,'conclusion'));
 const lastCritic=s.critiques.at(-1);if(lastCritic){limits.append(node('h3','最新独立批评'));for(const issue of [...(lastCritic.issues||[]),...(lastCritic.evidence_caveats||[])])limits.append(node('div',issue,'negative'))}
 for(const [name,c] of Object.entries(s.benchmark.comparisons||{})){if(c.mean_reduction_m<0)limits.append(node('div','独立测试中，所选方法的平均误差比 '+name+' 高 '+number(-c.mean_reduction_m)+' m。','negative'));else if(c.ci95_m&&c.ci95_m[0]<=0&&c.ci95_m[1]>=0)limits.append(node('div','相对 '+name+' 的配对改善区间包含 0，不能据此声称稳定胜出。','negative'))}
 if(!limits.childNodes.length)limits.append(node('div','研究尚未形成冻结结论；失败个案与批评会原样保留。','empty'));
 renderBenchmark(s.benchmark);
}
function renderBenchmark(b){const states={complete:'独立评估已完成',running:'独立评估正在生成结果',not_started:'尚未运行'};$('benchmarkstatus').textContent=states[b.status]||b.status;const host=$('benchmark');host.replaceChildren();
 const metrics=Object.entries(b.metrics||{});if(!metrics.length){host.append(node('div','冻结后执行 benchmark.py，结果完成后此处自动显示。','empty'))}else{
  const wrap=node('div',undefined,'tablewrap'),table=node('table'),head=node('tr');for(const [i,title]of ['方法','主测试均值 / m','主测试 P90 / m','压力均值 / m','压力 P90 / m','主测试平均 work units'].entries())head.append(node('th',title,i?'number':''));const thead=node('thead');thead.append(head);table.append(thead);const body=node('tbody');
  for(const [name,v]of metrics){const tr=node('tr',undefined,name==='llm_selected'?'selected':'');tr.append(node('td',name+(name==='llm_selected'?' · 冻结方案':'')));for(const x of [v.main?.overall?.mean_m,v.main?.overall?.p90_m,v.stress?.overall?.mean_m,v.stress?.overall?.p90_m,v.main?.overall?.mean_work_units])tr.append(node('td',number(x),'number'));body.append(tr)}table.append(body);wrap.append(table);host.append(wrap,node('p','预测记录 '+number(b.prediction_count,0)+' 条 · 独立评估耗时 '+number(b.wall_seconds,1)+' 秒','muted'))}
 const coverage=$('coverage');coverage.replaceChildren();const conf=b.conformal?.results||{};if(Object.keys(conf).length){coverage.append(node('h3','独立校准后的预测覆盖率'));const boxes=node('div',undefined,'coverage');for(const [alpha,c]of Object.entries(conf)){const box=node('div',undefined,'coverbox');box.append(node('div','目标边际覆盖率 '+pct(1-Number(alpha)),'muted'));box.append(node('strong',pct(c.populations?.iid_test?.coverage)));box.append(node('p','独立同分布测试 · 平均半径 '+number(c.populations?.iid_test?.mean_radius_m)+' m'));box.append(node('p','分布偏移压力测试覆盖率 '+pct(c.populations?.shifted_stress?.coverage)));box.append(node('p','校准样本 '+c.calibration_n+' · 顺序统计量 k='+c.order_statistic_k,'muted'));boxes.append(box)}coverage.append(boxes,node('p','边际覆盖保证依赖交换性与冻结估计器；不保证每个场景、拒识子集或分布偏移后的覆盖。','muted'))}}
function renderEvents(){const host=$('timeline');host.replaceChildren();const events=allEvents.filter(e=>filter==='all'||filter==='model'&&e.type.startsWith('model_')||filter==='tool'&&e.type.startsWith('tool_')||filter==='critic'&&(e.type==='critique'||e.purpose==='critic')).slice().reverse();
 if(!events.length){host.append(node('div','此类别尚无事件。','empty'));return}
 for(const e of events){const card=node('article',undefined,'event '+e.type+(e.failed?' failed':''));card.append(node('div','#'+e.sequence+' · '+date(e.time_utc)+(e.replay?' · REPLAY':''),'event-meta'),node('div',e.title,'event-title'));if(e.summary)card.append(node('p',e.summary));if(e.hypothesis)card.append(node('p','科研假设：'+e.hypothesis,'hypothesis'));if(e.expected_result)card.append(node('p','预期观察：'+e.expected_result,'expected'));if(e.issues?.length)for(const issue of e.issues)card.append(node('p',issue,'negative'));
  if(e.details){const d=node('details');d.dataset.sequence=String(e.sequence);d.open=opened.has(e.sequence);d.append(node('summary','查看参数与证据摘要'),node('pre',e.details));d.addEventListener('toggle',()=>{if(d.open)opened.add(e.sequence);else opened.delete(e.sequence)});card.append(d)}host.append(card)}}
async function refresh(){if(busy)return;busy=true;try{const responses=await Promise.all([fetch('/api/state',{cache:'no-store'}),fetch('/api/events?limit=100',{cache:'no-store'})]);if(responses.some(r=>!r.ok))throw new Error('本地端点暂时不可用');const [state,events]=await Promise.all(responses.map(r=>r.json()));renderState(state);allEvents=events.events;renderEvents()}catch(error){$('error').textContent='读取失败：'+error.message+'。保留上次画面，5 秒后重试。';$('error').style.display='block';$('connection').textContent='等待重新连接'}finally{busy=false}}
$('filters').addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;filter=button.dataset.filter;for(const b of $('filters').querySelectorAll('button'))b.classList.toggle('active',b===button);renderEvents()});$('refresh').addEventListener('click',refresh);refresh();setInterval(refresh,5000);
</script></body></html>'''


def make_handler(data: DashboardData):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CrowdLocateReadOnly/1.0"

        def _send(self, status, content, content_type="application/json; charset=utf-8"):
            raw = content.encode("utf-8") if isinstance(content, str) else content
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            try:
                self.handle_get()
            except (OSError, ValueError, TypeError, AttributeError, OverflowError):
                self._send(503, '{"error":"Saved evidence is incomplete or incompatible; refresh after repair"}')

        def handle_get(self):
            expected_port = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{expected_port}", f"localhost:{expected_port}"):
                self._send(403, '{"error":"Localhost Host header required"}')
                return
            target = urlsplit(self.path)
            if target.path == "/" and not target.query:
                self._send(200, HTML, "text/html; charset=utf-8")
            elif target.path == "/api/state" and not target.query:
                self._send(200, json.dumps(data.state(), ensure_ascii=True, allow_nan=False))
            elif target.path == "/api/events":
                query = parse_qs(target.query, keep_blank_values=True)
                try:
                    if set(query) - {"after", "limit"} or any(len(v) != 1 for v in query.values()):
                        raise ValueError("Unknown query parameter")
                    after = int(query.get("after", ["0"])[0])
                    limit = int(query.get("limit", ["100"])[0])
                    if after < 0 or not 1 <= limit <= 100:
                        raise ValueError("after must be nonnegative and limit 1..100")
                except ValueError:
                    self._send(400, '{"error":"Use nonnegative after and limit=1..100 only"}')
                    return
                self._send(200, json.dumps(data.events(after, limit), ensure_ascii=True, allow_nan=False))
            else:
                self._send(404, '{"error":"No such read-only endpoint"}')

        def _deny_write(self):
            self._send(405, '{"error":"Read-only dashboard: only GET is supported"}')

        do_POST = do_PUT = do_PATCH = do_DELETE = _deny_write

        def log_message(self, format, *args):
            # Avoid terminal noise every five seconds; no client request text is executed.
            pass

    return Handler


def create_server(run: Path, results: Path, port=8765):
    return ThreadingHTTPServer(("127.0.0.1", int(port)), make_handler(DashboardData(run, results)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("runs/live_reference"))
    parser.add_argument("--results", type=Path, default=Path("results/benchmark"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    server = create_server(args.run, args.results, args.port)
    print(f"Read-only research dashboard: http://127.0.0.1:{server.server_address[1]}", flush=True)
    print("Press Ctrl+C to stop the dashboard. The research agent is unaffected.", flush=True)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
