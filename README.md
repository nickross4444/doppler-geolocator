# Doppler flyover geolocator

A tool that works out where an outdoor recording was made from the Doppler curves of aircraft you can hear in it, including faint ones in clips where no plane is visible.

It recreates the methods from colsto's video *[I discovered a sound that leaks your exact location](https://youtu.be/UytYrhYLOKI)*. The formulas and pipeline follow that video; a frame-by-frame method spec is in [`SPEC.md`](SPEC.md).

A read-only viewer of the preloaded examples is at **https://doppler-geolocator.vercel.app**. Uploads, auto-detection and new searches need this app running locally.

## Method

- **Doppler fit** of four parameters: rest frequency \(f_0\), speed \(v\), closest-approach distance \(d\), and time of closest approach \(t_0\). The curve can include propagation delay ("Delayed") or omit it ("Position", the video's teaching curve).
- **ADS-B candidates** from [adsb.lol](https://adsb.lol) `globe_history` archives (ODbL). Known jets can be dropped; unknowns are kept.
- **200 m candidate grid** in the distance band, then **100 m fine scoring** (locked spectrogram score: event 1 sweeps \(\Delta t\) and \(f_0\); later events sweep \(f_0\) only, with times locked by geometry).
- **Two-event offset filter**: the gap between fitted \(t_0\)s is compared to the geometric delayed-CPA gap at each candidate (the ~920 s figure in the video is just the gap for that example; nothing is hard-coded).
- **Silence filter** (two-pass) and **single-flight mode** (one event plus a known recording start).
- **Optional harmonic-aware fit** (`dopp/harmonics.py`, off by default): find a harmonic comb, jointly fit \((F, v, d, t_0)\) across visible harmonics, and check hops / before-after pitch ratio.

Terrain comes from public AWS Terrarium tiles. Everything runs locally on free data.

## Install and tests

Python 3.11+ and `ffmpeg` on `PATH`. From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q tests        # unit + acceptance on cached preset results (~38 tests)
```

`DOPP_LIVE=1 python -m pytest -q tests` re-runs the regional acceptance searches (needs ingested flight data and the preset audio/spectrograms, which are not in this repo).

## Run the app

```bash
.venv/bin/python server.py --port 8765
# open http://localhost:8765
```

The UI ships with cached results for the presets (Oyster Bay, Livermore, Cornell FeederWatch, Venice Beach, Altenthann), so you can inspect fits, maps and comparisons without downloading flight data. To search a new recording, or to rebuild a search, ingest an ADS-B day and upload audio or video (any format ffmpeg reads; audio is extracted at 8 kHz mono).

### Using it

1. **Load a recording.** Pick a ★ preset, or "Upload video/audio…". The app computes a whitened spectrogram (1 s Hann window, 0.25 s hop, 20–1500 Hz). Wheel zooms time, shift+wheel zooms frequency, drag pans, a click seeks the player.
2. **Fit Doppler curves** — mix and match:
   - *Auto-detect* faint curves (per-frame whitening, harmonic stacking, retarded-model templates, Viterbi ridge trace, LSQ fit). Tick the candidates you want.
   - *Sliders* (\(f_0\), \(v\), \(d\), \(t_0\)) with the same \(f_\text{pre}\), \(f_\text{post}\) and max RoC readouts as the video.
   - *Trace mode*: click or drag along a ridge, then "Fit traced points (LSQ)".
3. **Check the gaps.** Each pair of selected events lists \(t_{0,k} - t_{0,j}\). Tolerance is `offset_tol_s` + \(k\sqrt{\sigma_j^2+\sigma_k^2}\).
4. **Recording start** (local time + time zone) is required for single-flight mode. Multi-event mode does not need it, but can optionally be limited to start ± an uncertainty.
5. **Search.** Multi-event (\(N \ge 2\), time unknown): velocity filter → 200 m grid → DEM slant-range CPA → offset filter → silence filter → crude rank → 100 m densify → locked scoring. Single-flight (one event, known time): score every segment at that time as \(-\mathrm{mean}|f_\text{model}-f_\text{traced}|\) after an alignment and \(f_0\) sweep.
6. **Results.** Top-match table and scatter, OSM/satellite map with a 25 m heat map, Google Maps / Street View links, and side-by-side curve comparisons.

Advanced options (all default off or unchanged from the video): wind correction (Open-Meteo/ERA5, manual, or per-candidate fit), score normalisation (`z` vs `db_med`) and aggregation (`mean` vs `sum`).

## Fetch ADS-B data

Daily `globe_history` releases are published at `github.com/adsblol/globe_history_YYYY/releases`, tag `vYYYY.MM.DD-planes-readsb-prod-0`. A CONUS day is about 4 GB to download and takes about a minute to ingest.

```bash
# helper: download the release, ingest, delete the tars
bench/fetch_day.sh 2026-09-08 us          # default bbox = CONUS
bench/fetch_day.sh 2026-03-03 eu          # Europe bbox (34–62 N, 12 W–30 E)

# or ingest files you already downloaded
INGEST_BBOX=lat0,lat1,lon0,lon1 .venv/bin/python scripts/ingest_adsblol.py \
    data/days/2026-09-08 data/ref/icao_aircraft_types.json part.aa part.ab
```

Stores land in `data/days/<day>/` (override with `DOPP_DAYS`). Ingested days show up in the UI's "Flight-data day" selector. Live API polls (`scripts/ingest_adsblol_live.py`, `scripts/livecam/`) can backfill a day that has not been published yet.

Recording a live cam yourself (how the FeederWatch and Venice Beach presets were made): poll ADS-B around the camera (`scripts/livecam/poll_adsb_env.py`), pull a window from the YouTube DVR (`scripts/livecam/dvr_grab.py` + `dvr_concat.py`), backfill full tracks (`scripts/adsblol_traces.py`), ingest, and list heard passes (`scripts/live_cam_passes.py`).

## Presets and what shipped

Cached **metadata and fit JSONs** for the presets are in `data/projects/` (enough for the acceptance tests and the result tables). Audio, spectrograms, DEM tiles and heat-map rasters are omitted because they are large; the [public viewer](https://doppler-geolocator.vercel.app) has the full preset presentation. Screenshots of each preset are in `screenshots/`.

The Venice Beach preset (public beach-front webcam at the Venice V Hotel) is the clean live-cam success: the multi-event search ranks the true camera location **#1 at 65 m**.

## Outdoor live-cam benchmark

`bench/` has the harness and the results of a 22-clip test on random outdoor YouTube live cams (ground-truth time and location used only for scoring). Full write-up: [`bench/results.md`](bench/results.md).

Honest summary:

- **0 of 10** runnable clips had the true location in the top 10, in any mode (blind = date only, whole-day store; prior = stream start time ± uncertainty; prior_single = single-flight with that time).
- **11 of 22** clips had no usable live audio (digital silence or a music bed).
- Of the clips that did have sound, typical top-1 errors were **1,200–1,500 km**. The closest miss was Mick Riley Golf Course, about **1.2 km** off when given the stream start time (outside the 500 m hit radius).
- Three blind runs timed out at 30 minutes; two were skipped for time.

The method still works on clean recordings such as Venice and the original video's Oyster Bay example. Random live cams usually fail because they have no aircraft tone, or wind and ambient noise hide it. Even when passes are found, thousands of flights across a continent can fit just as well.

```bash
# re-run (needs yt-dlp, ffmpeg, and ingested days for each clip's date)
.venv/bin/python bench/download.py
.venv/bin/python bench/drive2.py
.venv/bin/python bench/aggregate.py    # writes bench/results.json and results.md
```

## Limitations

- **Altenthann is unsolved.** The freesound recording of a helicopter over a Bavarian village does not recover the geotagged site. The likely aircraft is not in the ADS-B archive (and/or the village-level tag is too coarse for a unique pass). Harmonic-aware fitting does not fix it.
- US-wide single-flight searches are near-degenerate: many (flight, point) pairs can explain a constant-velocity Doppler line once only \(v\) and \(d\) matter.
- This repo does not include source videos, extracted audio, ADS-B archives, or DEM caches.

## Static viewer

```bash
.venv/bin/python scripts/export_static.py     # presets with meta "preset": true -> ../viewer/site
.venv/bin/python scripts/vercel_manifest.py   # sha1 manifest of changed files
```

The live site is the Vercel project `doppler-geolocator`. Viewer-only status labels (for example Altenthann "unsolved") live in `STATUS` in `scripts/export_static.py`.

## License

[MIT](LICENSE). Copyright (c) 2026 Nicholas Ross.

Flight tracks: adsb.lol `globe_history` ([ODbL](https://opendatacommons.org/licenses/odbl/)). Terrain: AWS Terrarium tiles. The Livermore examples use public Zenodo recordings ([10.5281/zenodo.23424278](https://doi.org/10.5281/zenodo.23424278)); the recorder position here was derived from the published tracks (see `scripts/livermore_truth.py` and `data/derived_recorder_position.json`).
