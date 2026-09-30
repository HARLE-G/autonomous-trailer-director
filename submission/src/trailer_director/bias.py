"""Audience-signal bias filter: only behavioural, adequately-sampled, non-stereotyped signals may steer a plan."""
from .text_rules import word_hits

MIN_SAMPLE = 500
ALLOWED_BASIS = {"behavioral"}


def filter_signals(aud, pkg):
    used, dropped = [], []
    for s in aud["signals"]:
        reason = None
        if s["basis"] not in ALLOWED_BASIS:
            reason = f"basis '{s['basis']}' is a proxy (location/demographic inference), not behaviour"
        elif s["sample_size"] < MIN_SAMPLE:
            reason = f"sample_size {s['sample_size']} below minimum {MIN_SAMPLE}"
        elif word_hits(s["statement"], pkg.lexicons["stereotype"]):
            reason = "statement contains stereotype language"
        (dropped if reason else used).append({**s, "dropped_reason": reason} if reason else s)
    return {"audience": aud["id"], "used": used, "dropped": [{"id": d["id"], "statement": d["statement"], "reason": d["dropped_reason"]} for d in dropped],
            "human_review": [d["id"] for d in dropped if d["basis"] != "behavioral"]}
