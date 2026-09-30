"""Strict HH:MM:SS.mmm timecode helpers. Invalid timecodes raise, never get 'fixed'."""
import re

_TC = re.compile(r"^(\d{2}):([0-5]\d):([0-5]\d)\.(\d{3})$")


class TimecodeError(ValueError):
    pass


def tc_to_sec(tc):
    m = _TC.match(tc) if isinstance(tc, str) else None
    if not m:
        raise TimecodeError(f"invalid timecode: {tc!r}")
    h, mi, s, ms = map(int, m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def sec_to_tc(x):
    ms = int(round(x * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))
