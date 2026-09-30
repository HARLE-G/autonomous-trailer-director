"""Human-readable validation report (markdown)."""


def _tbl(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
    return "\n".join(out)


def write_validation_report(path, ctx, plans, initials, div):
    st = ctx.story
    L = ["# Validation report", "", f"Episode: **{st['title']}** ({st['episode_id']}), mode: `{ctx.mode}`, "
         f"LLM adapter: `{getattr(ctx.llm, 'name', 'none (heuristic)')}`", "",
         "## Summary", "", _tbl([[a, p["validation"]["status"], p["validation"]["initial_status"], p["validation"]["initial_fail_count"],
                                  len(p["validation"]["unresolved_failures"]), p["duration_seconds"], p["estimated_cost"]["total"]]
                                 for a, p in plans.items()],
                                ["trailer", "final status", "first-draft status", "first-draft FAILs", "unresolved FAILs", "seconds", "est. live cost USD"]), ""]
    L += ["The 'first draft' is the planner proposal as recorded/generated. The verifier rejected the FAILs below; the repair loop only accepted "
          "changes that the verifier re-approved. Nothing was forced through.", ""]
    for (a, p), init in zip(plans.items(), initials):
        L += [f"## {p['trailer_id']} - {p['validation']['status']}", "", f"Promise: *{p['audience_promise']}*", "",
              "### Rejected in first draft", ""]
        rows = [[f["code"], f"seg {f['segment']}" if f["segment"] is not None else (f"card {f['card']}" if f["card"] is not None else "plan"), f["message"]]
                for f in init["findings"] if f["severity"] == "FAIL"]
        L += [_tbl(rows, ["code", "where", "why"]) if rows else "_none_", "", "### Repairs applied (each re-verified)", ""]
        rows = [[h["iteration"], h["target"], ", ".join(h["codes"]), h["action"], h["before"], h["after"]] for h in p.get("repair_history", [])]
        L += [_tbl(rows, ["iter", "target", "fixed codes", "action", "before", "after"]) if rows else "_none_", "", "### Final segments", ""]
        rows = [[s["index"], s["beat"], s["video"], f"{s['source_in']} - {s['source_out']}", s["subtitle_track"], s["reason"][:70]] for s in p["segments"]]
        L += [_tbl(rows, ["#", "beat", "scene", "timecode", "subs", "reason"]), "", "### Warnings and approvals still required", ""]
        w = [f"- `{f['code']}` (segment {f['segment']}): {f['message']}" for f in p["validation"]["findings"] if f["severity"] == "WARN"]
        L += (w or ["- none"]) + [""]
        L += [f"- **{x['approver']}** approval: {x['item']} - {x['why']}" for x in p["human_approvals"]] + [""]
        if p.get("marketing_requests"):
            for r in p["marketing_requests"]:
                L += [f"**Marketing request {r['id']}**: '{r['text']}' -> {'accepted' if r['accepted'] else 'REJECTED (' + ', '.join(r['rejected_for']) + ')'}; "
                      f"compliant alternative: '{r['alternative']}'", ""]
        if p["validation"]["unresolved_failures"]:
            L += ["**UNRESOLVED FAILURES - trailer must not ship:**"] + [f"- {f['code']}: {f['message']}" for f in p["validation"]["unresolved_failures"]] + [""]
    L += ["## Cross-trailer checks", "", f"Footage overlap (Jaccard): {div['jaccard_footage_overlap']}; near-identical pairs: {div['near_identical'] or 'none'}", "",
          "## Untrusted-input handling", ""]
    L += [f"- Quarantined instruction in `{q['scene']}.{q['field']}`: \"{q['text']}\" -> {q['action']}" for q in st["quarantined_instructions"]] or ["- none"]
    L += [f"- Unsupported description claim in `{q['scene']}.{q['field']}`: \"{q['text']}\" ({q['unsupported_relations']}) -> excluded" for q in st["unsupported_description_claims"]]
    L += [f"- Rating conflict {c['scene']}: {c['dims']} -> {c['resolution']}" for c in st["description_conflicts"]]
    L += ["", "## Bias control on audience data", ""]
    for aid, s in ctx.signals.items():
        L += [f"- {aid}: used {[x['id'] for x in s['used']]}; dropped " + (str([(d['id'], d['reason']) for d in s['dropped']]) if s['dropped'] else "none") for _ in [0]]
    L += ["", "## Why not just pick the top-engagement scenes?", "",
          _tbl([[e["scene"], e["engagement"], "yes" if e["contains_blocked_moment"] else "no"] for e in st["historic_engagement_vs_safety"][:5]],
               ["scene", "historic engagement", "contains protected spoiler"]),
          "", "The two highest-engagement scenes are the spoilers. Engagement is used only as a small ranking weight.", ""]
    from pathlib import Path
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")
