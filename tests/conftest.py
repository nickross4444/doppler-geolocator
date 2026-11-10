import os, sys, json, glob
import pytest
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
LIVE = os.environ.get("DOPP_LIVE") == "1"   # 1 = re-run the searches instead of reading the cached preset results

def preset_results(pid, mode=None, tag=None):
    """Cached preset results for a project (written by scripts/build_presets.py)."""
    out = []
    for f in glob.glob(os.path.join(APP, "data", "projects", pid, "results", "*.json")):
        if "_heat" in f: continue
        j = json.load(open(f))
        if mode and j["mode"] != mode: continue
        if tag and j.get("tag") != tag: continue
        out.append(j)
    out.sort(key=lambda j: -j["created"])
    return out
