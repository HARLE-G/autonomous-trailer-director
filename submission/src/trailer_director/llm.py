"""LLM adapters: replay (no keys needed), simulated outage, and live Anthropic (optional)."""
import json
import os
from pathlib import Path


class LLMUnavailable(Exception):
    pass


class ReplayLLM:
    name = "replay"

    def __init__(self, directory):
        self.dir = Path(directory)

    def complete(self, task, key, payload=None):
        p = self.dir / f"{task}_{key}.json"
        if not p.exists():
            raise LLMUnavailable(f"no replay recording for {task}/{key}")
        return json.loads(p.read_text(encoding="utf-8"))


class OutageLLM:
    name = "simulated_outage"

    def complete(self, task, key, payload=None):
        raise LLMUnavailable("preferred model unavailable (simulated outage)")


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model=None):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMUnavailable("ANTHROPIC_API_KEY not set")
        try:
            import anthropic
        except ImportError as e:
            raise LLMUnavailable("anthropic package not installed") from e
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("TRAILER_MODEL", "claude-sonnet-5-5")

    def complete(self, task, key, payload=None):
        try:
            msg = self.client.messages.create(
                model=self.model, max_tokens=4000,
                system="You are a trailer planner. Reply with ONE JSON object only. Reference footage by moment_id from the catalog. "
                       "Never invent scenes, quotes or relationships. Contracts and policies are enforced by an independent verifier.",
                messages=[{"role": "user", "content": payload["prompt"]}])
            text = "".join(b.text for b in msg.content if getattr(b, "text", None))
            return json.loads(text[text.index("{"): text.rindex("}") + 1])
        except Exception as e:  # network, auth, parse ... all mean 'model unavailable' to the pipeline
            raise LLMUnavailable(str(e)) from e
