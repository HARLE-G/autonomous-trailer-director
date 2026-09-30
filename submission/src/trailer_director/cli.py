import argparse
import json
import sys
from pathlib import Path

from .pipeline import run, build_context
from .verifier import verify_plan


def main(argv=None):
    ap = argparse.ArgumentParser(prog="trailer_director", description="Autonomous Trailer Director")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="plan + verify + repair three trailers")
    r.add_argument("--data", default="data"); r.add_argument("--out", default="sample_run")
    r.add_argument("--mode", choices=["replay", "heuristic", "live"], default="replay")
    r.add_argument("--simulate-outage", action="store_true", help="preferred model unavailable -> fallback planner")
    r.add_argument("--budget-usd", type=float); r.add_argument("--max-model-calls", type=int); r.add_argument("--max-media-minutes", type=float)
    v = sub.add_parser("verify", help="verify a single trailer plan JSON against the package")
    v.add_argument("plan"); v.add_argument("--data", default="data")
    c = sub.add_parser("change", help="apply a contract/policy/subtitle/signal change to an existing run")
    c.add_argument("--data", default="data"); c.add_argument("--run", default="sample_run"); c.add_argument("--change", required=True); c.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        ov = {k: v for k, v in {"total_usd": a.budget_usd, "max_model_calls": a.max_model_calls, "max_media_minutes": a.max_media_minutes}.items() if v is not None}
        ctx, plans = run(a.data, a.out, a.mode, a.simulate_outage, ov)
        for k, p in plans.items():
            print(f"{p['trailer_id']:<20} {p['validation']['status']:<20} {p['duration_seconds']:>5}s  first-draft FAILs: {p['validation']['initial_fail_count']}  "
                  f"repairs: {len(p['repair_history'])}")
        print(f"outputs in {a.out}/")
        return 0 if all(p["validation"]["status"] != "REJECTED" for p in plans.values()) else 2
    if a.cmd == "verify":
        ctx = build_context(a.data, "heuristic")
        rep = verify_plan(json.loads(Path(a.plan).read_text()), ctx)
        print(json.dumps(rep, indent=2)); return 0 if rep["status"] != "REJECTED" else 2
    if a.cmd == "change":
        from .changes import run_change
        for rec in run_change(a.data, a.run, a.change, a.out):
            print(f"{rec['audience']:<15} {rec['status_before']} -> {rec['status_after']}  affected={rec['affected_segments']}  actions={len(rec['actions'])}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
