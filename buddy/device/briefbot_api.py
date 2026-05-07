"""Local prospect data — file-backed lookup.

The full 1362-company brief data lives on flash as two peer files:

  /flash/briefbot_index.txt   one company name per line, sort order matches data file
  /flash/briefbot_data.jsonl  one JSON brief per line, same order as the index

Index loads at first call (~15 KB into RAM). The data file is too big
to fit in RAM (~415 KB), so we re-open and stream-skip to the target
line on each `get(idx)`. ESP32 reads ~1 MB/s from internal flash so
worst-case (line 1361) takes <500 ms — imperceptible in the picker
flow.

Public surface:
  list_names() -> list[str]
  get(idx)     -> dict (the brief at line index `idx` in the data file)
  lookup(query)-> dict | None  (substring match — kept for compatibility)
"""

_INDEX_PATH = "/flash/briefbot_index.txt"
_DATA_PATH = "/flash/briefbot_data.jsonl"

_NAMES = None


def _load_index():
    global _NAMES
    if _NAMES is not None:
        return _NAMES
    try:
        with open(_INDEX_PATH) as f:
            _NAMES = [ln.rstrip("\r\n") for ln in f if ln.strip()]
    except OSError as e:
        print("briefbot_api: index load failed:", e)
        _NAMES = []
    return _NAMES


def list_names():
    """Return all company names in display order (alphabetical)."""
    return _load_index()


def get(idx):
    """Return the brief dict at line `idx` in the data file, or None."""
    if idx is None or idx < 0:
        return None
    try:
        import json
        with open(_DATA_PATH) as f:
            for i, line in enumerate(f):
                if i == idx:
                    line = line.strip()
                    if not line:
                        return None
                    return json.loads(line)
    except Exception as e:
        print("briefbot_api: get(", idx, ") failed:", e)
    return None


def lookup(query):
    """Find a brief by name (case-insensitive exact / substring match).

    Kept for compatibility with the original picker UI and any voice
    path that wants to reuse this module. The new picker uses
    list_names() + get(idx) directly for type-to-filter.
    """
    if not query:
        return None
    q = query.strip().lower()
    names = _load_index()
    for i, n in enumerate(names):
        if n.lower() == q:
            return get(i)
    for i, n in enumerate(names):
        nl = n.lower()
        if q in nl or nl in q:
            return get(i)
    return None
