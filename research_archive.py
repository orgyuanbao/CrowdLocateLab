"""Build a read-only, audited research archive from one completed frozen run.

This is an evidence index, not another model run or an automated proof checker.
Only saved decisions, tool observations and critiques are displayed. Existing
historical source and evidence files are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from urllib.parse import quote

from audit import audit_run


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 2000
PROTECTED = {"runs", "results", "research", "agent_runtime", "tools", "revision",
             "localization", "report", "latex", "tests", "docs", "verification", ".git"}
KINDS = {"read_sources": "source_review", "register_method": "registered_method",
         "run_experiment": "numerical_observation", "ablate": "numerical_observation",
         "diagnose": "diagnosis", "verify_math": "mathematical_witness",
         "freeze": "freeze_decision", "finish": "completion"}
LABELS = {"source_review": "来源审阅", "registered_method": "方法登记",
          "numerical_observation": "数值观察", "diagnosis": "失败诊断",
          "mathematical_witness": "数学检查与例证", "freeze_decision": "冻结决定",
          "completion": "研究结束", "critique": "独立提示审阅",
          "attempt_not_executed": "未执行的研究请求", "tool_failure": "工具失败记录"}
LIMITATIONS = [
    "完整性通过表示保存记录在本次检查时一致，不证明其中的科学主张为真。",
    "假设和预期均保留为 unassessed；本工具不自动判定支持、否定或数学证明。",
    "数学检查记录中的推导、条件和数值例证各有适用范围，不等于形式化证明。",
    "审阅角色采用独立提示；不能据此声称独立模型或独立研究复现。",
    "本档案只索引开发过程，不包含冻结后评测，也不产生新模型调用或新实验。",
    "哈希不是数字签名，不能防止所有文件被协同重写；并发前后校验不是操作系统级锁。",
]


class ArchiveError(ValueError):
    """Source evidence is unsuitable for a verified archive."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _strict(data):
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ArchiveError("Duplicate JSON key: " + key)
            value[key] = child
        return value

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ArchiveError("Nonfinite JSON number")
        return result

    def constant(value):
        raise ArchiveError("Nonfinite JSON constant: " + value)

    try:
        return json.loads(data.decode("utf-8-sig") if isinstance(data, bytes) else data,
                          object_pairs_hook=unique, parse_float=number, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ArchiveError("Invalid archive input JSON: " + str(error)) from error


def _no_links(path, boundary):
    path, boundary = Path(path).absolute(), Path(boundary).absolute()
    if not path.is_relative_to(boundary):
        raise ArchiveError("Path escapes the selected boundary")
    current = boundary
    for part in ((), *[(part,) for part in path.relative_to(boundary).parts]):
        if part:
            current = current / part[0]
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ArchiveError("Linked paths are forbidden: " + str(current))
    if not path.resolve().is_relative_to(boundary.resolve()):
        raise ArchiveError("Resolved path escapes the selected boundary")


def _read_regular(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
        raise ArchiveError("Archive input must be a bounded regular file: " + path.name)
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ArchiveError("Archive input exceeds the file size limit")
    return data


def _capture(root, run):
    """Keep extraction bytes, then bind all audit-consumed run/source files."""
    if not run.is_dir():
        raise ArchiveError("Run directory does not exist")
    _no_links(root, Path(root.anchor))
    _no_links(run, Path(run.anchor))
    if any((run / name).exists() for name in (".research.lock", "uncertain_tool_failure.json",
                                             "tools/uncertain_tool_failure.json")):
        raise ArchiveError("Active or uncertain runs cannot be archived")
    run_files, source_files, total, nodes = {}, {}, 0, 0
    for directory, dirs, files in os.walk(run, followlinks=False):
        for name in sorted(dirs + files):
            nodes += 1
            if nodes > MAX_FILES:
                raise ArchiveError("Run exceeds bounded archive file/directory limits")
            _no_links(Path(directory) / name, run)
        for name in sorted(files):
            path = Path(directory) / name
            data = _read_regular(path)
            total += len(data)
            if len(run_files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ArchiveError("Run exceeds bounded archive input limits")
            run_files[path.relative_to(run).as_posix()] = data
    if ".research.lock" in run_files or "uncertain_tool_failure.json" in run_files:
        raise ArchiveError("Active or uncertain runs cannot be archived")
    try:
        state = _strict(run_files["state.json"])
        protocol = _strict(run_files["protocol.json"])
    except KeyError as error:
        raise ArchiveError("Run requires state.json and protocol.json") from error
    if not isinstance(state, dict) or not isinstance(protocol, dict):
        raise ArchiveError("State and protocol must be JSON objects")
    if state.get("frozen") is not True or state.get("status") != "frozen" or state.get("pending_tool"):
        raise ArchiveError("Only a completed frozen run without pending tools may be archived")
    hashes = protocol.get("source_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ArchiveError("Source hash manifest is missing")
    for relative, expected in sorted(hashes.items()):
        if not isinstance(relative, str) or not isinstance(expected, str) or Path(relative).is_absolute():
            raise ArchiveError("Invalid source hash manifest")
        path = root / relative
        _no_links(path, root)
        data = _read_regular(path)
        if _hash(data) != expected:
            raise ArchiveError("Source hash differs from frozen protocol: " + relative)
        total += len(data)
        if len(run_files) + len(source_files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ArchiveError("Sources exceed bounded archive input limits")
        source_files[path.relative_to(root).as_posix()] = data
    return run_files, source_files, state


def _inventory(snapshot):
    run_files, source_files, _ = snapshot
    return {"run_files": {name: _hash(data) for name, data in sorted(run_files.items())},
            "source_files": {name: _hash(data) for name, data in sorted(source_files.items())}}


def _events(raw):
    if not raw or not raw.endswith(b"\n"):
        raise ArchiveError("A complete newline-terminated event log is required")
    events, previous = [], "0" * 64
    for sequence, line in enumerate(raw.splitlines(), 1):
        event = _strict(line)
        if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
            raise ArchiveError("Every event and payload must be objects")
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        if (type(event.get("sequence")) is not int or event["sequence"] != sequence
                or event.get("previous_sha256") != previous
                or event.get("event_sha256") != _hash(_canonical(body).encode("utf-8"))):
            raise ArchiveError("Event ordering or content hash failed")
        previous = event["event_sha256"]
        events.append(event)
    return events


def _ref(event):
    return {"sequence": event["sequence"], "event_sha256": event["event_sha256"],
            "file": "events.jsonl", "line": event["sequence"]}


def _portable(value):
    """Historical absolute evidence paths are rebased, never opened here."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key == "evidence_path" and isinstance(child, str):
                path = child.replace("\\", "/")
                path = path.rsplit("/tools/", 1)[-1] if "/tools/" in path else path.removeprefix("tools/")
                if Path(path).is_absolute() or ".." in Path(path).parts or ":" in path:
                    raise ArchiveError("Invalid portable evidence reference")
                result[key] = "tools/" + path
            else:
                result[key] = _portable(child)
        return result
    if isinstance(value, list):
        return [_portable(child) for child in value]
    return value


def _observation(action, result):
    if not isinstance(result, dict):
        raise ArchiveError("Tool observations must be objects")
    if action == "read_sources":
        return {"source_names": sorted(result.get("sources", {})),
                "scope": result.get("scope", result.get("revision_scope")),
                "note": "Source texts and model prompts are omitted; inspect the referenced original event."}
    return _portable(result)


def _cards(events):
    cards, matched = [], set()
    results = [event for event in events if event["type"] == "tool_result"]
    for event in events:
        payload = event["payload"]
        if event["type"] == "decision" and payload.get("action") in KINDS:
            action, step = payload["action"], payload.get("step")
            if type(step) is not int or step < 1:
                raise ArchiveError("Action decisions require a positive integer step")
            pairs = [result for result in results if result["payload"].get("step") == step
                     and result["payload"].get("action") == action]
            if len(pairs) != 1 or pairs[0]["sequence"] <= event["sequence"] or pairs[0]["sequence"] in matched:
                raise ArchiveError("Each decision requires exactly one later matching tool result")
            observed = pairs[0]
            matched.add(observed["sequence"])
            executed = observed["payload"].get("executed")
            if type(executed) is not bool:
                raise ArchiveError("Tool execution status must be explicitly recorded")
            arguments = _strict(payload.get("arguments_json", "{}"))
            if not isinstance(arguments, dict):
                raise ArchiveError("Decision arguments must be an object")
            has_arguments = "arguments" in observed["payload"]
            if ((executed and not has_arguments)
                    or (has_arguments and arguments != observed["payload"]["arguments"])):
                raise ArchiveError("Decision arguments do not match the executed tool result")
            result = observed["payload"].get("result")
            if not isinstance(result, dict):
                raise ArchiveError("Tool observations must be objects")
            kind = "attempt_not_executed" if not executed else "tool_failure" if result.get("ok") is False else KINDS[action]
            cards.append({"id": "step-" + str(step), "kind": kind, "step": step, "action": action,
                          "tool_executed": executed,
                          "arguments_binding": "matched" if has_arguments else "not_recorded_in_tool_result",
                          "decision_ref": _ref(event), "observation_ref": _ref(observed),
                          "hypothesis": {"text": payload.get("hypothesis", ""), "assessment": "unassessed"},
                          "expected_result": payload.get("expected_result", ""),
                          "decision_summary": payload.get("analysis_summary", ""),
                          "arguments": _portable(arguments),
                          "observation": _portable(result) if kind in ("attempt_not_executed", "tool_failure") else _observation(action, result),
                          "scientific_status": "recorded_observation_not_automatic_claim_verification"})
        elif event["type"] == "critique":
            cards.append({"id": "critique-" + str(event["sequence"]), "kind": "critique", "event_ref": _ref(event),
                          "role": "independent_prompt_review_not_independent_model_evidence",
                          "different_model_claimed": payload.get("different_model_claimed", False),
                          "observation": _portable(payload), "scientific_status": "attributed_reviewer_opinion"})
    if len(matched) != len(results):
        raise ArchiveError("Unpaired tool results cannot be silently omitted")
    if len({card["id"] for card in cards}) != len(cards) or not any(card["kind"] == "freeze_decision" for card in cards):
        raise ArchiveError("Cards require unique steps and a recorded freeze decision")
    return cards


def _build(root, run):
    root, run = Path(root).absolute(), Path(run).absolute()
    snapshot = _capture(root, run)
    inventory = _inventory(snapshot)
    run_files, _, state = snapshot
    try:
        events = _events(run_files["events.jsonl"])
    except KeyError as error:
        raise ArchiveError("Run requires events.jsonl") from error
    if (state.get("last_event_sequence") != len(events)
            or state.get("last_event_sha256") != events[-1]["event_sha256"]):
        raise ArchiveError("State is not bound to the complete event chain")
    cards = _cards(events)
    checked = audit_run(root, run, require_frozen=True)
    if checked.get("exit_code") != 0 or checked.get("status") != "passed_frozen" or checked.get("integrity_ok") is not True:
        raise ArchiveError("Frozen evidence audit failed; no verified archive will be exported")
    if _inventory(_capture(root, run)) != inventory:
        raise ArchiveError("Source changed during verification; archive refused")
    name = run.relative_to(root).as_posix() if run.is_relative_to(root) else run.name
    archive = {"schema_version": 1, "archive_kind": "verified_development_evidence_index",
               "run_name": name, "new_model_calls": 0, "new_experiments": 0,
               "verification": {"status": checked["status"], "integrity_ok": True,
                                "snapshot_sha256": _hash(_canonical(inventory).encode("utf-8")),
                                "counts": checked.get("counts", {}), "findings": checked.get("findings", []),
                                "warning_count": checked.get("warning_count", 0), "inventory": inventory,
                                "event_count": len(events), "event_tail_sha256": events[-1]["event_sha256"]},
               "card_counts": dict(sorted(Counter(card["kind"] for card in cards).items())),
               "cards": cards, "limitations": LIMITATIONS,
               "verification_instructions": ["Run: python audit.py --run <original-run-directory> --require-frozen",
                    "Rebuild this archive from the original run; compare verification.snapshot_sha256.",
                    "Event references identify events.jsonl line numbers and event SHA-256 values.",
                    "SHA256SUMS.txt checks exported file bytes; it is not a digital signature."]}
    return archive, inventory


def build_archive(root, run):
    """Return a verified JSON-ready index without creating any files."""
    return _build(root, run)[0]


def _render(archive, event_url):
    esc = lambda value: html.escape(str(value), quote=True)
    parts = ["<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">",
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             "<title>CrowdLocateLab · 研究证据档案</title><style>body{font:16px/1.7 system-ui,sans-serif;margin:0;background:#f1f5f9;color:#17283d}main{max-width:1050px;margin:auto;padding:36px 24px}h1{line-height:1.25}article,header,section{background:white;border:1px solid #d5e0ea;border-radius:12px;padding:22px;margin:18px 0}h2{font-size:21px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:16px;border-radius:6px;font-size:13px}a{color:#075985}.tag{color:#087669;font-weight:bold}.note{color:#526474}li{margin:8px 0}code{overflow-wrap:anywhere}summary{cursor:pointer;font-weight:bold}@media print{body{background:white}article{break-inside:avoid}details{display:block}}</style><main>",
             "<header><p class=\"tag\">CrowdLocateLab / 开发过程证据</p><h1>从假设到观察，保留每一步依据</h1>",
             "<p>" + esc(archive["run_name"]) + " · " + str(len(archive["cards"])) + " 张证据卡片</p>",
             "<p><strong>历史完整性审计通过；科学主张仍需人工判断。</strong>本页无模型调用、无新实验，不包含冻结后评测。</p>",
             "<p class=\"note\">审计警告数：" + str(archive["verification"]["warning_count"]) + "；警告详情保留在结构化 JSON。快照 SHA-256：<code>" + archive["verification"]["snapshot_sha256"] + "</code></p></header>",
             '<nav><a href="#limitations">适用边界</a> · <a href="#verification">如何复查</a> · <a href="archive.json">结构化 JSON</a></nav>',
             '<section><h2>研究步骤索引</h2><p>' + " · ".join('<a href="#' + esc(card["id"]) + '">' + esc(card["id"] + " / " + LABELS.get(card["kind"], card["kind"])) + '</a>' for card in archive["cards"]) + '</p></section>']
    for card in archive["cards"]:
        title = LABELS.get(card["kind"], card["kind"])
        parts.append('<article id="' + esc(card["id"]) + '"><p class="tag">' + esc(card["id"]) + "</p><h2>" + esc(title) + "</h2>")
        if "hypothesis" in card:
            parts.extend(["<h3>提出的假设（未自动判定）</h3><p>" + esc(card["hypothesis"]["text"]) + "</p>",
                          "<h3>实验前预期</h3><p>" + esc(card["expected_result"]) + "</p>",
                          "<p class=\"note\">决策摘要：" + esc(card["decision_summary"]) + "</p>"])
        if card["kind"] == "mathematical_witness":
            parts.append("<p><strong>以下为保存的数学检查、推导或例证，档案生成不构成形式化证明。</strong></p>")
        if card["kind"] == "critique":
            parts.append("<p>独立提示的审阅意见；不据此声称独立模型或独立研究复现。</p>")
        if card["kind"] == "freeze_decision":
            parts.append("<p>冻结结论按原记录归属展示，不能替代冻结后的评测结论。</p>")
        if card["kind"] in ("attempt_not_executed", "tool_failure"):
            parts.append("<p><strong>此次请求未执行或工具失败；保留失败记录，不计作成功实验。</strong></p>")
        parts.append("<details><summary>展开保存的工具观察 / 审阅意见</summary><pre>" + esc(json.dumps(card["observation"], ensure_ascii=False, indent=2, allow_nan=False)) + "</pre></details><p>")
        for key in ("decision_ref", "observation_ref", "event_ref"):
            if key in card:
                ref = card[key]
                label = "原始 events.jsonl 事件 / 行 #" + str(ref["sequence"])
                link = '<a href="' + esc(event_url + "#L" + str(ref["line"])) + '">' + label + '</a>' if event_url else '<span>' + label + '（跨盘路径，请在源目录查看）</span>'
                parts.append(link + " <code>" + ref["event_sha256"] + "</code><br>")
        parts.append("</p></article>")
    parts.append('<section id="limitations"><h2>适用边界</h2><ul>' + "".join("<li>" + esc(item) + "</li>" for item in archive["limitations"]) + "</ul></section>")
    parts.append('<section id="verification"><h2>如何复查</h2><ol>' + "".join("<li>" + esc(item) + "</li>" for item in archive["verification_instructions"]) + "</ol><p>本地文本查看器不一定支持 #L 行号；请按事件编号、行号和哈希定位原始文件。源目录移动后需重新导出以更新链接。</p></section></main></html>")
    return "\n".join(parts)


def _output_path(root, run, output):
    output = Path(output).absolute()
    _no_links(output, Path(output.anchor))
    resolved = output.resolve()
    if resolved == root.resolve() or resolved.is_relative_to(run.resolve()):
        raise ArchiveError("Archive output cannot overwrite the project or source run")
    if resolved.is_relative_to(root.resolve()) and resolved.relative_to(root.resolve()).parts[0].lower() in PROTECTED:
        raise ArchiveError("Archive output is inside a protected project directory")
    if output.exists():
        raise ArchiveError("Archive output already exists; choose a new directory")
    return output


def export_archive(root, run, output):
    """Validate first, stage all files, then exclusively create a new output."""
    root, run = Path(root).absolute(), Path(run).absolute()
    output = _output_path(root, run, output)
    archive, inventory = _build(root, run)
    try:
        event_url = quote(os.path.relpath(run / "events.jsonl", output).replace("\\", "/"), safe="/.")
    except ValueError:  # Windows source and destination can be on different drives.
        event_url = None
    contents = {"archive.json": (json.dumps(archive, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"),
                "index.html": _render(archive, event_url).encode("utf-8")}
    contents["SHA256SUMS.txt"] = "".join(_hash(data) + "  " + name + "\n" for name, data in sorted(contents.items())).encode("ascii")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".crowdloc-archive-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, data in contents.items():
            (staging / name).write_bytes(data)
        if _inventory(_capture(root, run)) != inventory:
            raise ArchiveError("Source changed before publication; archive refused")
        _output_path(root, run, output)
        # mkdir is the atomic non-overwrite claim. Each complete staged file is
        # then atomically moved; the checksum manifest is the last commit marker.
        output.mkdir()
        for name in ("archive.json", "index.html", "SHA256SUMS.txt"):
            os.replace(staging / name, output / name)
    return {"output": str(output), "snapshot_sha256": archive["verification"]["snapshot_sha256"],
            "cards": len(archive["cards"]), "files": list(contents), "new_model_calls": 0, "new_experiments": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = args.root.absolute()
    run = args.run if args.run.is_absolute() else root / args.run
    try:
        result = export_archive(root, run, args.output) if args.output else build_archive(root, run)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        parser.exit(2, "research_archive: " + str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
