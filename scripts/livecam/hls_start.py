"""Absolute UTC time of the first sample of capture.ts: ffmpeg's HLS demuxer starts live playback 3 segments
from the end of the media playlist (live_start_index=-3), so t0 = EXT-X-PROGRAM-DATE-TIME of that segment."""
import sys, re
from datetime import datetime
def start_from_playlist(path, live_start_index=-3):
    segs = []; pdt = None
    for line in open(path):
        line = line.strip()
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pdt = datetime.fromisoformat(line.split(":", 1)[1]).timestamp()
        elif line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
            if pdt is None: pdt = segs[-1][0] + segs[-1][1]
            segs.append((pdt, dur)); pdt = None
    return segs[live_start_index][0], segs
if __name__ == "__main__":
    t0, segs = start_from_playlist(sys.argv[1])
    print(f"{t0:.3f}", datetime.utcfromtimestamp(t0).isoformat() + "Z", "segments", len(segs), "last end",
          datetime.utcfromtimestamp(segs[-1][0] + segs[-1][1]).isoformat() + "Z")
