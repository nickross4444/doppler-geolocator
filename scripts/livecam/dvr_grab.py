"""Download a time window of a YouTube live stream from its DVR (the HLS media playlist keeps the last ~60 min,
each segment tagged with EXT-X-PROGRAM-DATE-TIME) and concatenate it.  Robust to box freezes: segments are files,
re-running only fetches what is missing.
usage: python dvr_grab.py VIDEO_ID OUTDIR T_START_EPOCH T_END_EPOCH [itags=230,234]"""
import sys, os, subprocess, json, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import urllib.request

def playlist(vid, itag):
    for _ in range(6):
        u = subprocess.run(["yt-dlp", "-g", "-f", str(itag), f"https://www.youtube.com/watch?v={vid}"],
                           capture_output=True, text=True).stdout.strip().splitlines()
        if u: break
        time.sleep(5)
    txt = urllib.request.urlopen(u[0], timeout=30).read().decode()
    segs = []; pdt = None; dur = None
    for line in txt.splitlines():
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"): pdt = datetime.fromisoformat(line.split(":", 1)[1]).timestamp()
        elif line.startswith("#EXTINF:"): dur = float(line[8:].split(",")[0])
        elif line.startswith("http"):
            sq = int(line.split("/sq/")[1].split("/")[0])
            if pdt is None and segs: pdt = segs[-1]["pdt"] + segs[-1]["dur"]
            segs.append(dict(sq=sq, pdt=pdt, dur=dur, url=line)); pdt = None
    return segs, txt

def fetch(seg, path):
    if os.path.exists(path) and os.path.getsize(path) > 0: return True
    for _ in range(5):
        try:
            data = urllib.request.urlopen(seg["url"], timeout=30).read()
            open(path + ".part", "wb").write(data); os.replace(path + ".part", path); return True
        except Exception as e: time.sleep(2)
    return False

def main(vid, out, ta, tb, itags=(230, 234)):
    os.makedirs(os.path.join(out, "segs"), exist_ok=True)
    index = {}
    for itag in itags:
        segs, txt = playlist(vid, itag)
        open(os.path.join(out, f"playlist_{itag}_{int(time.time())}.m3u8"), "w").write(txt)
        sel = [s for s in segs if s["pdt"] + s["dur"] > ta and s["pdt"] < tb]
        with ThreadPoolExecutor(8) as ex:
            ok = list(ex.map(lambda s: fetch(s, os.path.join(out, "segs", f"{itag}_{s['sq']}.ts")), sel))
        print(itag, "segments", len(sel), "ok", sum(ok), "first", sel[0]["sq"] if sel else None, flush=True)
        for s in sel: index.setdefault(str(itag), {})[str(s["sq"])] = dict(pdt=s["pdt"], dur=s["dur"])
    p = os.path.join(out, "segs", "index.json")
    old = json.load(open(p)) if os.path.exists(p) else {}
    for k, v in index.items(): old.setdefault(k, {}).update(v)
    json.dump(old, open(p, "w"))

if __name__ == "__main__":
    a = sys.argv
    main(a[1], a[2], float(a[3]), float(a[4]), tuple(int(x) for x in a[5].split(",")) if len(a) > 5 else (230, 234))
