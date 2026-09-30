"""Configurable budget tracker (model calls, media minutes, USD). Charges are simulated in replay mode."""


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, costs, overrides=None):
        self.costs = costs
        self.limits = {**costs["budget"], **(overrides or {})}
        self.spent, self.calls, self.media = 0.0, 0, 0.0
        self.ledger = []

    def unit_cost(self, kind):
        c = self.costs
        return {"planner": c["model_calls_usd"]["planner"], "second_opinion": c["model_calls_usd"]["verifier_second_opinion"],
                "story_extraction": c["model_calls_usd"]["story_extraction"], "media_minute": c["media_analysis_usd_per_minute"],
                "tool_call": c["tool_call_usd"]}[kind]

    def can(self, kind, units=1):
        cost = self.unit_cost(kind) * units
        if self.spent + cost > self.limits["total_usd"] + 1e-9:
            return False
        if kind in ("planner", "second_opinion", "story_extraction") and self.calls + units > self.limits["max_model_calls"]:
            return False
        if kind == "media_minute" and self.media + units > self.limits["max_media_minutes"] + 1e-9:
            return False
        return True

    def charge(self, kind, units=1, note=""):
        if not self.can(kind, units):
            raise BudgetExceeded(f"budget would be exceeded by {kind} x{units}")
        cost = round(self.unit_cost(kind) * units, 4)
        self.spent = round(self.spent + cost, 4)
        if kind in ("planner", "second_opinion", "story_extraction"):
            self.calls += units
        if kind == "media_minute":
            self.media += units
        self.ledger.append({"kind": kind, "units": units, "usd": cost, "note": note})
        return cost

    def summary(self):
        return {"spent_usd": self.spent, "model_calls": self.calls, "media_minutes": round(self.media, 2), "limits": self.limits}
