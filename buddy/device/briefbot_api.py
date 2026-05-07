"""Briefbot lookup interface.

The device app talks to this module and only this module. V1 is
fixture-backed (offline). Phase 3 will add a network branch that
calls the laptop companion over TCP; the public signature stays
identical so apps/briefbot.py doesn't have to change.

Public surface:
  lookup(query: str) -> dict | None
"""

import briefbot_fixtures as _fix


def lookup(query):
    """Return the brief dict for `query`, or None if no match.

    Matching is intentionally permissive so a voice transcript like
    "what's the size of Applied Intuition" lands on the "applied
    intuition" key without an entity-extraction step. Order:
      1. Exact match on the normalized key.
      2. Either string contains the other (handles transcript noise).

    A fuzzier match (Levenshtein, etc.) is the laptop companion's job
    in Phase 3 — we keep this lean so the device boots fast.
    """
    if not query:
        return None
    q = query.strip().lower()
    if q in _fix.PROSPECTS:
        return _fix.PROSPECTS[q]
    for key, brief in _fix.PROSPECTS.items():
        if key in q or q in key:
            return brief
    return None
