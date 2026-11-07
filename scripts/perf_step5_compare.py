"""Compare the output hashes of two ``perf_step5_check.py`` results (timings are ignored).

    python scripts/perf_step5_compare.py <before.json> <after.json>
"""

import json
import sys


def hashes(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from hashes(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from hashes(v, f"{path}[{i}]")
    elif "sha256" in path or path.endswith(("_identical", "_equals_kit_file")):
        yield path, obj


a, b = (dict(hashes(json.load(open(p, encoding="utf-8")))) for p in sys.argv[1:3])
diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
flags = {k: v for k, v in b.items() if k.endswith(("_identical", "_equals_kit_file"))}
print(f"{len(a)} hashes/flags compared; differences: {diff or 'none'}; flags in {sys.argv[2]}: {flags}")
sys.exit(1 if diff or not all(flags.values()) else 0)
