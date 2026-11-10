"""Download each benchmark candidate's segment (bestaudio) into bench/audio/<id>.m4a.

Uses yt-dlp on PATH (or $YT_DLP). Optional pre-downloaded full-length files can be
pointed at with $Y2MATE_DIR/<id>.mp3 and will be cut locally with ffmpeg instead.
"""
import json, os, shutil, subprocess, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
B = os.path.join(ROOT, "bench")
A = os.path.join(B, "audio"); os.makedirs(A, exist_ok=True)
Y2DIR = os.environ.get("Y2MATE_DIR", "")
YTDLP = os.environ.get("YT_DLP") or shutil.which("yt-dlp") or "yt-dlp"
env = dict(os.environ)
deno = os.path.expanduser("~/.deno/bin")
if os.path.isdir(deno):
    env["PATH"] = deno + ":" + env.get("PATH", "")
os.makedirs(os.path.join(B, "dl"), exist_ok=True)
only = set(sys.argv[1:])
for c in json.load(open(os.path.join(B, "candidates.json"))):
    if only and c["id"] not in only: continue
    cid = c["id"]; s0, s1 = c["segment"]["start_s"], c["segment"]["end_s"]; out = f"{A}/{cid}.m4a"
    lf = f"{B}/dl/{cid}.json"
    if os.path.exists(lf) and json.load(open(lf)).get("status") == "ok": continue
    log = {}
    t = time.time()
    y2 = os.path.join(Y2DIR, f"{cid}.mp3") if Y2DIR else ""
    if y2 and os.path.exists(y2):
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(s0), "-to", str(s1), "-i", y2, "-c:a", "aac", "-b:a", "128k", out]
        src = "local_mp3"
        r0err = None
    else:
        # native yt-dlp download of the whole bestaudio track, then cut locally: --download-sections goes through
        # ffmpeg, which YouTube throttles to ~1.4x realtime (1 h segment = 40+ min)
        full = f"{B}/full/{cid}"; os.makedirs(f"{B}/full", exist_ok=True)
        r0 = subprocess.run([YTDLP, "-f", "bestaudio", "--force-overwrites", "-o", full + ".%(ext)s", c["url"]],
                            capture_output=True, text=True, env=env)
        got = [f for f in os.listdir(f"{B}/full") if f.startswith(cid + ".") and not f.endswith(".part")]
        if r0.returncode != 0 or not got:
            cmd = ["false"]; r0err = (r0.stderr or r0.stdout)
        else:
            cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(s0), "-to", str(s1), "-i", f"{B}/full/{got[0]}", "-vn", "-c:a", "aac", "-b:a", "128k", out]
            r0err = None
        src = "yt-dlp"
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    ok = r.returncode == 0 and os.path.exists(out)
    if src == "yt-dlp":
        for f in os.listdir(f"{B}/full"):
            if f.startswith(cid + "."): os.remove(f"{B}/full/{f}")
        if cmd == ["false"]: r = type("R", (), dict(stderr=r0err, stdout=""))()
    dur = None
    if ok:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                                   capture_output=True, text=True).stdout.strip() or 0)
    prev = json.load(open(lf)) if os.path.exists(lf) else {}
    log[cid] = dict(status="ok" if ok else "download_failed", source=src, attempts=prev.get("attempts", 1 if prev else 0) + 1, seconds=round(time.time() - t, 1), duration_s=dur,
                    err=None if ok else (r.stderr or r.stdout)[-800:])
    print(cid, log[cid]["status"], dur, flush=True)
    json.dump(log[cid], open(lf, "w"), indent=1)
