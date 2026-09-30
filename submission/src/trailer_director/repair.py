"""Repair-or-reject loop.

Repairs are candidate transformations that must be RE-VERIFIED by the independent verifier before being
accepted. Nothing is forced through: if no candidate passes, the failure stays and the trailer is REJECTED.
Order of least change: fix flags on same footage -> swap footage -> drop.
"""
import copy
import itertools

from .planner import make_segment, beat_score, finalize_layout
from .verifier import verify_plan


def _fails(rep, seg=None, card=None):
    return [f for f in rep["findings"] if f["severity"] == "FAIL" and f["segment"] == seg and f["card"] == card]


def _variants(seg, ctx, aud):
    """Cheap variants of the same footage: strip bad signals, swap music for a licensed bed, alternate subtitle track."""
    tracks = [seg["subtitle_track"]] + [t for t in ("dialect_a", "dialect_b", "source") if t != seg["subtitle_track"]]
    music_opts = [None] + [r["subject"] for r in ctx.cmap["rules"] if r["type"] == "music_rights"]
    allowed = set(ctx.allowed_signal_ids(aud["id"]))
    combos = []
    for strip, mus, tr in itertools.product((False, True), music_opts, tracks):
        cost = int(strip) + int(mus is not None) + int(tr != seg["subtitle_track"])
        combos.append((cost, strip, mus or "", tr))
    for cost, strip, mus, tr in sorted(combos):
        v = copy.deepcopy(seg)
        actions = []
        if strip and any(x not in allowed for x in v["signal_refs"]):
            v["signal_refs"] = [x for x in v["signal_refs"] if x in allowed]; actions.append("strip_unapproved_signals")
        elif strip:
            continue
        if mus:
            v["audio"]["source_music"] = False; v["audio"]["music_override"] = mus; actions.append(f"replace_music_with_{mus}")
        if tr != seg["subtitle_track"] and v["audio"].get("dialogue"):
            m = ctx.pkg.moments.get(v.get("moment_id", ""))
            if not m:
                continue
            fresh = make_segment(m, ctx.pkg, tr, v["beat"], v["reason"], v["signal_refs"])
            v["subtitle_track"], v["subtitle"] = tr, fresh["subtitle"]; actions.append(f"use_subtitle_track_{tr}")
        elif tr != seg["subtitle_track"]:
            continue
        yield v, actions


def _try(plan, ctx, i, cand):
    t = copy.deepcopy(plan)
    t["segments"][i] = cand
    finalize_layout(t)
    rep = verify_plan(t, ctx)
    return not _fails(rep, seg=i), t


def repair_plan(plan, ctx, aud, used_elsewhere=(), max_iter=6):
    history = []
    for it in range(1, max_iter + 1):
        rep = verify_plan(plan, ctx)
        fails = [f for f in rep["findings"] if f["severity"] == "FAIL"]
        if not fails:
            break
        progressed = False

        # ---- cards
        for ci in sorted({f["card"] for f in fails if f["card"] is not None}, reverse=True):
            card = plan["text_cards"][ci]
            codes = sorted({f["code"] for f in _fails(rep, card=ci)})
            done = False
            for tpl in [c for c in aud["cards"] if c["type"] == card.get("type") and c["text"] != card["text"]] or []:
                t = copy.deepcopy(plan); t["text_cards"][ci] = {**tpl, "after_beat": card["after_beat"]}; finalize_layout(t)
                if not _fails(verify_plan(t, ctx), card=ci):
                    history.append({"iteration": it, "target": f"text_card[{ci}]", "codes": codes, "action": "replace_with_verified_template_card",
                                    "before": card["text"], "after": t["text_cards"][ci]["text"]})
                    plan, done, progressed = t, True, True; break
            if not done:
                t = copy.deepcopy(plan); del t["text_cards"][ci]; finalize_layout(t)
                history.append({"iteration": it, "target": f"text_card[{ci}]", "codes": codes, "action": "drop_card", "before": card["text"], "after": None})
                plan, progressed = t, True

        # ---- segments (highest index first so drops do not shift pending indices)
        rep = verify_plan(plan, ctx)
        for i in sorted({f["segment"] for f in rep["findings"] if f["severity"] == "FAIL" and f["segment"] is not None}, reverse=True):
            seg = plan["segments"][i]
            codes = sorted({f["code"] for f in _fails(rep, seg=i)})
            if not codes:
                continue
            done = False
            for v, actions in _variants(seg, ctx, aud):
                if not actions:
                    continue
                ok, t = _try(plan, ctx, i, v)
                if ok:
                    history.append({"iteration": it, "target": f"segment[{i}] {seg['video']} {seg['source_in']}", "codes": codes,
                                    "action": "+".join(actions), "before": seg["source_in"] + "-" + seg["source_out"], "after": "same footage"})
                    plan, done, progressed = t, True, True; break
            if done:
                continue
            beat = next((b for b in aud["beats"] if b["name"] == seg["beat"]), {"name": seg["beat"], "themes": []})
            in_plan = {s.get("moment_id") for s in plan["segments"]}
            pool = []
            for m in ctx.pkg.moments.values():
                if m.id in in_plan or m.id in ctx.story["blocked_moments"] or m.id in ctx.story["zone_moments"]:
                    continue
                sc = beat_score(m, beat, aud, ctx, used_elsewhere)
                if sc is not None:
                    pool.append((-sc, m.id, m))
            for _, _, m in sorted(pool, key=lambda x: (x[0], x[1])):
                base = make_segment(m, ctx.pkg, aud["subtitle_track"], seg["beat"], f"Replacement for rejected footage: {m.summary}",
                                    [s["id"] for s in ctx.signals[aud["id"]]["used"] if set(s["preference_tags"]) & set(m.themes)])
                for v, actions in [(base, [])] + list(_variants(base, ctx, aud)):
                    ok, t = _try(plan, ctx, i, v)
                    if ok:
                        history.append({"iteration": it, "target": f"segment[{i}] {seg['video']} {seg['source_in']}", "codes": codes,
                                        "action": "replace_footage" + ("+" + "+".join(actions) if actions else ""),
                                        "before": f"{seg['video']} {seg['source_in']}-{seg['source_out']}",
                                        "after": f"{m.scene}/{m.id} {v['source_in']}-{v['source_out']}"})
                        plan, done, progressed = t, True, True; break
                if done:
                    break
            if done:
                continue
            if len(plan["segments"]) > 5:
                t = copy.deepcopy(plan); del t["segments"][i]
                # keep card anchors valid
                live = {s["beat"] for s in t["segments"]} | {"end", "start"}
                for c in t["text_cards"]:
                    if c["after_beat"] not in live:
                        c["after_beat"] = t["segments"][max(0, min(i, len(t["segments"])) - 1)]["beat"]
                finalize_layout(t)
                history.append({"iteration": it, "target": f"segment[{i}] {seg['video']} {seg['source_in']}", "codes": codes, "action": "drop_segment",
                                "before": f"{seg['video']} {seg['source_in']}-{seg['source_out']}", "after": None})
                plan, progressed = t, True

        # ---- plan-level: duration too short -> add best verified footage; too long -> drop weakest
        rep = verify_plan(plan, ctx)
        if any(f["code"] == "duration_out_of_range" for f in rep["findings"]):
            plan, moved = _fix_duration(plan, ctx, aud, rep, used_elsewhere, history, it)
            progressed = progressed or moved
        if not progressed:
            break
    plan["repair_history"] = history
    return plan


def _fix_duration(plan, ctx, aud, rep, used_elsewhere, history, it):
    msg = next(f for f in rep["findings"] if f["code"] == "duration_out_of_range")["message"]
    tot = rep["timeline_seconds"]
    from .constraints import duration_for
    d = duration_for(ctx.cmap, aud["id"], plan["territories"])
    if tot < d["min"]:
        in_plan = {s.get("moment_id") for s in plan["segments"]}
        pool = []
        for m in ctx.pkg.moments.values():
            if m.id in in_plan or m.id in ctx.story["blocked_moments"] or m.id in ctx.story["zone_moments"]:
                continue
            best = max([beat_score(m, b, aud, ctx, used_elsewhere) for b in aud["beats"]] or [None], key=lambda x: -1e9 if x is None else x)
            if best is not None:
                pool.append((-best, m.id, m))
        for _, _, m in sorted(pool, key=lambda x: (x[0], x[1])):
            base = make_segment(m, ctx.pkg, aud["subtitle_track"], "extra", f"Added to reach minimum duration: {m.summary}",
                                [s["id"] for s in ctx.signals[aud["id"]]["used"] if set(s["preference_tags"]) & set(m.themes)])
            for v, actions in [(base, [])] + list(_variants(base, ctx, aud)):
                t = copy.deepcopy(plan); pos = max(1, len(t["segments"]) - 1); t["segments"].insert(pos, v); finalize_layout(t)
                r = verify_plan(t, ctx)
                if not _fails(r, seg=pos) and not any(f["severity"] == "FAIL" and f["segment"] not in (None, pos) and f["code"] not in
                                                        [x["code"] for x in rep["findings"]] for f in r["findings"]):
                    history.append({"iteration": it, "target": "plan", "codes": ["duration_out_of_range"], "action": "add_segment",
                                    "before": f"{tot}s", "after": f"{r['timeline_seconds']}s (+{m.scene}/{m.id})"})
                    return t, True
    elif tot > d["max"] and len(plan["segments"]) > 5:
        idx = min(range(1, len(plan["segments"]) - 1), key=lambda k: ctx.pkg.history.get(plan["segments"][k]["video"], 0))
        t = copy.deepcopy(plan); gone = t["segments"].pop(idx); finalize_layout(t)
        history.append({"iteration": it, "target": "plan", "codes": ["duration_out_of_range"], "action": "drop_weakest_segment",
                        "before": f"{tot}s", "after": gone["video"] + "/" + gone.get("moment_id", "")})
        return t, True
    return plan, False
