"""Constraint map: contracts + policies + spoiler facts compiled into machine-testable rules.

The verifier reads THESE rules (never prose), so a contract/policy change is a data change.
"""
from .package import DIMS


def _scope_ok(scope, audience_id, territories):
    if scope == "all":
        return True
    if "audience" in scope and audience_id in scope["audience"]:
        return True
    if "territory" in scope and set(scope["territory"]) & set(territories):
        return True
    return False


def build_constraint_map(pkg, story):
    rules = []
    scenes_with = lambda chars: sorted({m.scene for m in pkg.moments.values() if set(chars) & set(m.characters)})
    for c in pkg.contracts:
        if c["type"] == "actor":
            chars = [k for k, v in pkg.cast.items() if v == c["subject"]]
            rules.append({"id": f"R-ACT-{c['subject']}", "type": "actor_rights", "subject": c["subject"], "contract": c["id"],
                          "territories": c["territories"], "expires": c["expires"], "promo_allowed": c.get("promo_allowed", True),
                          "conditions": c.get("conditions", []), "voice_clone_allowed": c.get("voice_clone_allowed", False),
                          "applies_to": {"characters": chars, "scenes": scenes_with(chars)}, "note": c.get("note")})
        elif c["type"] == "music":
            rules.append({"id": f"R-MUS-{c['subject']}", "type": "music_rights", "subject": c["subject"], "contract": c["id"],
                          "title": c.get("title"), "territories": c["territories"], "expires": c["expires"],
                          "promo_allowed": c.get("promo_allowed", True), "max_total_seconds": c.get("max_total_seconds"),
                          "approval": c.get("approval"),
                          "applies_to": {"scenes": sorted(s.id for s in pkg.scenes.values() if s.music == c["subject"])}})
    for p in pkg.policies:
        base = {"policy": p["id"], "scope": p["scope"], "source": p.get("source")}
        for dim, mx in p.get("limits", {}).items():
            rules.append({"id": f"R-POL-{p['id']}-{dim}", "type": "content_limit", "dim": dim, "max": mx, **base})
        for lex in p.get("text_ban", []):
            rules.append({"id": f"R-POL-{p['id']}-textban-{lex}", "type": "text_ban", "lexicon": lex,
                          "terms": pkg.lexicons[lex], **base})
        if "duration" in p:
            rules.append({"id": f"R-POL-{p['id']}-duration", "type": "duration_limit", **p["duration"], **base})
        if "accessibility" in p:
            rules.append({"id": f"R-ACCESS-{p['id']}", "type": "accessibility", **p["accessibility"], **base})
    for sp in story["spoilers"]:
        rules.append({"id": f"R-SPOIL-{sp['id']}", "type": "spoiler", "fact": sp["fact"], "patterns": sp["patterns"],
                      "blocked_moments": sorted({r["moment"] for r in sp["reveal_moments"]})})
    rules.append({"id": "R-SPOIL-PROTECTED-SCENES", "type": "spoiler_scene", "scenes": story["protected_scenes"]})
    rules.append({"id": "R-SPOIL-ZONE", "type": "spoiler_zone", "start": story["zone_start"], "severity": "WARN"})
    return {"campaign": {"release": pkg.release_date, "end": pkg.campaign_end, "as_of": pkg.as_of}, "rules": rules}


def limits_for(cmap, aud, terr):
    out = {d: {"max": 3, "rules": []} for d in DIMS}
    for r in cmap["rules"]:
        if r["type"] == "content_limit" and _scope_ok(r["scope"], aud, terr):
            if r["max"] < out[r["dim"]]["max"]:
                out[r["dim"]] = {"max": r["max"], "rules": [r["id"]]}
            elif r["max"] == out[r["dim"]]["max"]:
                out[r["dim"]]["rules"].append(r["id"])
    return out


def text_bans_for(cmap, aud, terr):
    return [r for r in cmap["rules"] if r["type"] == "text_ban" and _scope_ok(r["scope"], aud, terr)]


def duration_for(cmap, aud, terr):
    lo, hi, ids = 0, 10 ** 6, []
    for r in cmap["rules"]:
        if r["type"] == "duration_limit" and _scope_ok(r["scope"], aud, terr):
            lo, hi = max(lo, r["min"]), min(hi, r["max"]); ids.append(r["id"])
    return {"min": lo, "max": hi, "rules": ids}


def access_for(cmap):
    return next(r for r in cmap["rules"] if r["type"] == "accessibility")


def policy_ids_for(cmap, aud, terr):
    return sorted({r["policy"] for r in cmap["rules"] if "policy" in r and _scope_ok(r["scope"], aud, terr)})
