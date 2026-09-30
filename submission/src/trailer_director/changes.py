"""Change handling: trace a change to affected segments/cards, revise only those, prove the rest is untouched."""
import copy
import json
from pathlib import Path

from .package import clone_raw, load_raw
from .pipeline import build_context, finalize_plan, _dump, _ordered
from .planner import finalize_layout
from .repair import repair_plan
from .verifier import verify_plan


def _merge(dst, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def apply_change_raw(raw, ch):
    raw = clone_raw(raw)
    t = ch["type"]
    if t == "contract_update":
        c = next(c for c in raw["contracts"]["contracts"] if c["id"] == ch["contract_id"]); _merge(c, ch["patch"])
    elif t == "policy_update":
        p = next(p for p in raw["policies"]["policies"] if p["id"] == ch["policy_id"]); _merge(p, ch["patch"])
    elif t == "subtitle_update":
        c = next(c for c in raw["dialogue"]["cues"] if c["id"] == ch["cue_id"])
        if ch["track"] == "source": c["text"] = ch["text"]
        else: c.setdefault("subtitles", {})[ch["track"]] = ch["text"]
    elif t == "signal_withdrawn":
        for a in raw["audiences"]["audiences"]:
            a["signals"] = [s for s in a["signals"] if s["id"] != ch["signal_id"]]
    elif t == "scene_content_update":
        s = next(s for s in raw["episode"]["scenes"] if s["id"] == ch["scene_id"]); _merge(s["content"], ch["patch"])
    else:
        raise ValueError(f"unknown change type {t}")
    return raw


def change_tokens(ch):
    t = ch["type"]
    return {"contract_update": [f"contract:{ch.get('contract_id')}"], "policy_update": [f"policy:{ch.get('policy_id')}"],
            "subtitle_update": [f"cue:{ch.get('cue_id')}"], "signal_withdrawn": [f"signal:{ch.get('signal_id')}"],
            "scene_content_update": [f"scene:{ch.get('scene_id')}"]}[t]


def _seg_key(s):
    return json.dumps({k: v for k, v in s.items() if k not in ("index", "evidence", "risk_flags")}, sort_keys=True)


def run_change(data_dir, run_dir, change_file, out_dir, mode="replay"):
    ch = json.loads(Path(change_file).read_text(encoding="utf-8"))
    raw_old = load_raw(data_dir)
    raw_new = apply_change_raw(raw_old, ch)
    ctx = build_context(data_dir, mode, raw=raw_new)
    tokens = set(change_tokens(ch))
    ctx.log.event("change", "received", change=ch, tokens=sorted(tokens))
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    records = []
    for aid in ["family", "young_adult", "dialect_region"]:
        f = Path(run_dir) / f"{aid}_trailer.json"
        if not f.exists():
            continue
        plan = json.loads(f.read_text(encoding="utf-8"))
        aud = ctx.pkg.audiences[aid]
        before = copy.deepcopy(plan)
        affected = [s["index"] for s in plan["segments"] if tokens & set(s.get("evidence", []))]
        affected_cards = [i for i, c in enumerate(plan["text_cards"]) if tokens & set(c.get("evidence", []))]
        initial = verify_plan(plan, ctx)
        ctx.log.event("change", "impact_analysis", audience=aid, affected_segments=affected, affected_cards=affected_cards,
                      new_fail_codes=sorted({x["code"] for x in initial["findings"] if x["severity"] == "FAIL"}))
        plan["repair_history"] = []
        revised = repair_plan(plan, ctx, aud)
        hist = revised.pop("repair_history", [])
        for h in hist:
            ctx.log.event("repair", h["action"], audience=aid, target=h["target"], codes=h["codes"], before=h["before"], after=h["after"], caused_by=ch["id"])
        final = finalize_plan(revised, ctx, initial, 20)
        final["repair_history"] = before.get("repair_history", [])
        final["revision_history"] = before.get("revision_history", []) + [{"change_id": ch["id"], "type": ch["type"], "note": ch.get("note"),
                                                                            "affected_segments": affected, "actions": hist}]
        old_by = {_seg_key(s): s for s in before["segments"]}
        kept = sum(1 for s in final["segments"] if _seg_key(s) in old_by)
        untouched_ok = all(_seg_key(before["segments"][i]) in {_seg_key(s) for s in final["segments"]}
                           for i in range(len(before["segments"])) if i not in {h_i for h_i in _changed_idx(hist)})
        rec = {"audience": aid, "status_before": before["validation"]["status"], "status_after": final["validation"]["status"],
               "affected_segments": affected, "affected_cards": affected_cards, "new_failures_found": sorted({x["code"] for x in initial["findings"] if x["severity"] == "FAIL"}),
               "actions": hist, "segments_before": len(before["segments"]), "segments_after": len(final["segments"]), "segments_unchanged": kept,
               "unaffected_footage_preserved": untouched_ok}
        records.append(rec)
        _dump(out / f"{aid}_trailer.json", _ordered(final))
    md = [f"# Change report {ch['id']}", "", f"**Type:** `{ch['type']}`  **Change:** {ch.get('note', '')}", f"**Dependency tokens traced:** `{sorted(tokens)}`", ""]
    for r in records:
        md += [f"## {r['audience']}: {r['status_before']} -> {r['status_after']}", "",
               f"- Segments depending on the change: {r['affected_segments'] or 'none'} (text cards: {r['affected_cards'] or 'none'})",
               f"- Failures the verifier found after re-checking: {r['new_failures_found'] or 'none'}",
               f"- Segments unchanged: {r['segments_unchanged']}/{r['segments_before']}; unaffected footage preserved: {r['unaffected_footage_preserved']}"]
        md += [f"- **{a['action']}** on {a['target']} ({', '.join(a['codes'])}): {a['before']} -> {a['after']}" for a in r["actions"]] or ["- no edits required"]
        md += [""]
    (out / "change_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    _dump(out / "change_record.json", records)
    ctx.log.write(out / "decision_log.jsonl")
    return records


def _changed_idx(hist):
    import re
    idx = set()
    for h in hist:
        m = re.match(r"segment\[(\d+)\]", h["target"])
        if m: idx.add(int(m.group(1)))
    return idx
