"""Concatenate DVR segments (dvr_grab.py) into one mp4 (video 230 + audio 234) starting at the first audio segment.
Prints the absolute UTC start (EXT-X-PROGRAM-DATE-TIME of the first segment) -> clip_<start>.mp4.
usage: python dvr_concat.py OUTDIR [T_START] [T_END]"""
import sys, os, json, subprocess
from datetime import datetime, timezone
out = sys.argv[1]; ta = float(sys.argv[2]) if len(sys.argv) > 2 else 0; tb = float(sys.argv[3]) if len(sys.argv) > 3 else 1e12
idx = json.load(open(os.path.join(out, "segs", "index.json")))
A = sorted((int(k), v) for k, v in idx["234"].items() if v["pdt"] >= ta - 0.01 and v["pdt"] < tb)
sqs = [k for k, _ in A]
missing = [s for s in range(sqs[0], sqs[-1] + 1) if s not in set(sqs)]
assert not missing, f"missing audio segments {missing[:10]}"
t0 = A[0][1]["pdt"]
for itag in ("234", "230"):
    with open(os.path.join(out, f"concat_{itag}.ts"), "wb") as f:
        for s in sqs:
            p = os.path.join(out, "segs", f"{itag}_{s}.ts")
            if os.path.exists(p): f.write(open(p, "rb").read())
            else: print("missing", itag, s)
name = "clip_" + datetime.fromtimestamp(t0, timezone.utc).strftime("%Y%m%dT%H%M%S") + "Z.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(out, "concat_230.ts"), "-i", os.path.join(out, "concat_234.ts"),
                "-map", "0:v", "-map", "1:a", "-c", "copy", os.path.join(out, name)], check=True)
for itag in ("234", "230"): os.remove(os.path.join(out, f"concat_{itag}.ts"))
json.dump(dict(clip=name, start_utc=t0, n_segments=len(sqs), first_sq=sqs[0], last_sq=sqs[-1]),
          open(os.path.join(out, name.replace(".mp4", ".json")), "w"), indent=1)
print(name, t0, datetime.fromtimestamp(t0, timezone.utc).isoformat(), len(sqs) * 5, "s")
