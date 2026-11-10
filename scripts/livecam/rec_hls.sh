#!/bin/bash
# Record a YouTube live cam's HLS (360p video + high audio) for DUR seconds; save the media playlists at start
# (EXT-X-PROGRAM-DATE-TIME gives the absolute time of each segment) and the wall clock.
# usage: rec_hls.sh VIDEO_ID OUTDIR DUR
ID=$1; OUT=$2; DUR=${3:-3300}
cd "$OUT"
for i in 1 2 3 4 5 6; do
  V=$(yt-dlp -g -f 230 "https://www.youtube.com/watch?v=$ID" 2>/dev/null | head -1)
  A=$(yt-dlp -g -f 234 "https://www.youtube.com/watch?v=$ID" 2>/dev/null | head -1)
  [ -n "$V" ] && [ -n "$A" ] && break; sleep 5
done
printf "%s\n%s\n" "$V" "$A" > hls_url.txt
curl -s "$A" > playlist_audio_at_start.m3u8
date -u +%s.%N > capture_start_utc.txt
ffmpeg -v warning -y -i "$V" -i "$A" -map 0:v -map 1:a -t $DUR -c copy capture.ts > ffmpeg.log 2>&1
date -u +%s.%N > capture_end_utc.txt
curl -s "$A" > playlist_audio_at_end.m3u8
