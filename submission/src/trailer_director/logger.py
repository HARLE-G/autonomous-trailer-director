"""Structured decision log (JSONL). Deterministic (sequence numbers, no wall-clock) so runs are diffable."""
import json


class DecisionLog:
    def __init__(self):
        self.events = []

    def event(self, stage, decision, **detail):
        e = {"seq": len(self.events) + 1, "stage": stage, "decision": decision, **detail}
        self.events.append(e)
        return e

    def write(self, path):
        with open(path, "w", encoding="utf-8") as f:
            for e in self.events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
