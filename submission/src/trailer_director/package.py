"""Episode package: loads supplied material into typed, indexed objects.

The raw JSON is kept (`pkg.raw`) so a change (contract/policy/subtitle) can be applied
to a deep copy and the package rebuilt deterministically.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from .timecode import tc_to_sec, overlap

DIMS = ("violence", "fear", "suggestive", "profanity")
TRACKS = ("source", "dialect_a", "dialect_b")
FILES = {
    "episode": "episode.json", "dialogue": "dialogue.json", "policies": "policies.json",
    "contracts": "contracts.json", "audiences": "audiences.json",
    "history": "historic_performance.json", "costs": "cost_sheet.json",
    "requests": "marketing_requests.json",
}


@dataclass
class Moment:
    id: str
    scene: str
    t_in: float
    t_out: float
    summary: str
    themes: list
    emotion: str
    characters: list
    content: dict | None = None
    reveals: list = field(default_factory=list)

    @property
    def dur(self):
        return self.t_out - self.t_in


@dataclass
class Scene:
    id: str
    t_in: float
    t_out: float
    title: str
    human_description: str
    ai_description: str
    music: str | None
    content: dict
    ai_content: dict
    beat: str
    emotion: str
    moment_ids: list

    @property
    def effective_content(self):
        # conservative: the stricter of human and AI annotation
        return {d: max(self.content.get(d, 0), self.ai_content.get(d, 0)) for d in DIMS}


@dataclass
class Cue:
    id: str
    scene: str
    t_in: float
    t_out: float
    speaker: str
    addressee: str
    text: str
    subs: dict


class Package:
    def __init__(self, raw: dict):
        self.raw = raw
        ep = raw["episode"]
        self.episode = ep
        self.duration = tc_to_sec(ep["duration"])
        self.as_of = ep["as_of"]
        self.campaign_end = ep["campaign_end"]
        self.release_date = ep["release_date"]
        self.cast = ep["cast"]
        self.scenes, self.moments, self.cues = {}, {}, {}
        for s in ep["scenes"]:
            ids = []
            for m in s["moments"]:
                self.moments[m["id"]] = Moment(
                    m["id"], s["id"], tc_to_sec(m["start"]), tc_to_sec(m["end"]), m["summary"],
                    m["themes"], m["emotion"], m["characters"], m.get("content"), m.get("reveals", []))
                ids.append(m["id"])
            self.scenes[s["id"]] = Scene(
                s["id"], tc_to_sec(s["start"]), tc_to_sec(s["end"]), s["title"], s["human_description"],
                s["ai_description"], s.get("music"), s["content"], s["ai_content"], s["beat"],
                s["emotion"], ids)
        for c in raw["dialogue"]["cues"]:
            self.cues[c["id"]] = Cue(c["id"], c["scene"], tc_to_sec(c["start"]), tc_to_sec(c["end"]),
                                     c["speaker"], c["addressee"], c["text"], c.get("subtitles", {}))
        self.policies = raw["policies"]["policies"]
        self.lexicons = raw["policies"]["lexicons"]
        self.contracts = raw["contracts"]["contracts"]
        self.audiences = {a["id"]: a for a in raw["audiences"]["audiences"]}
        self.signals = {s["id"]: s for a in raw["audiences"]["audiences"] for s in a["signals"]}
        self.history = raw["history"]["scene_engagement"]
        self.costs = raw["costs"]
        self.requests = raw.get("requests") or []

    # ---- helpers -------------------------------------------------------
    def moment_content(self, m: Moment):
        return m.content if m.content else self.scenes[m.scene].effective_content

    def cue_text(self, cue: Cue, track: str):
        return cue.text if track == "source" else cue.subs.get(track)

    def cues_overlapping(self, scene_id, a, b):
        return sorted((c for c in self.cues.values()
                       if c.scene == scene_id and overlap(a, b, c.t_in, c.t_out) > 0.05),
                      key=lambda c: c.t_in)

    def moments_overlapping(self, scene_id, a, b):
        return [m for m in self.moments.values() if m.scene == scene_id and overlap(a, b, m.t_in, m.t_out) > 0]

    def actor_of(self, character):
        return self.cast.get(character)


def load_raw(data_dir):
    d = Path(data_dir)
    raw = {}
    for key, fn in FILES.items():
        p = d / fn
        raw[key] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else ([] if key == "requests" else {})
    return raw


def load_package(data_dir) -> Package:
    return Package(load_raw(data_dir))


def clone_raw(raw):
    return copy.deepcopy(raw)
