# PROGRESS (Phase 2 build log)

Times are America/Los_Angeles (PST).

## Done (committed)
- 653aee7 core: adsb.lol ingest (globe_history daily tar -> per-day flight store), DEM (Terrarium z7), physics,
  whitened spectrogram, faint-Doppler detection, multi-event search.
- 6fe3ab0 retarded-model auto-fit + Viterbi ridge trace + LSQ; numba exact emission-time model; vectorised
  locked fine scoring; single-flight mode.
- Livermore recorder position derived (scripts/livermore_truth.py): 37.663188, -121.781870, 153.3 m MSL,
  RMS residual of the published L values 1.6 m across 8 cases.

## Verified results so far
- Worked example: 96.065 -> 96.07 Hz (dopp.physics.worked_example).
- Example date: 2026-09-08 (Sept 13 has no matching pair). E1 = PC12 N22NY, E2 = B350 N93CQ, faint = CRJ9 N325PQ (jet).
- Gap: derived from fitted t0s (2155.6 -> 3075.9 s => 920.3 s); author's on-screen t0s 1:35:52 -> 1:51:13-14.8 => 921-923 s;
  ADS-B geometric delayed-CPA gap at the truth point 923.5 s. Tolerance = offset_tol_s(5) + 2*sqrt(sig1^2+sig2^2).
- Regional multi-event (NY bbox): truth #1, 36 m from 40.876466,-73.524951, score 5.56 vs 4.26.
- Single-flight (jets on): N325PQ #1, -1.05 vs -1.67, best point 225 m from camera, dt +34 s recovers clock error.
- CONUS multi-event (old code): truth #1 but only 5.56 vs 5.44. Scoring-variant study (window, harmonics, clip,
  contrast, spectrogram resolution, line residual hybrid) gives at most ~3-5 % margin: the competitors' predicted
  curves overlay the ridges almost perfectly (constant-velocity passes are described by just v and d), so the
  example is near-degenerate at CONUS scale with our data. Local 25 m refinement: truth 5.55 at 41 m from author pt.

## In progress / next
- Re-run CONUS with vectorised code (cache result for the preset).
- FastAPI server + JS frontend; presets; tests; README.

## Session 3 (Nov 9, from 08:30 PST)
- Server was already running (`nohup ../.venv/bin/python server.py --port 8765`, log /tmp/server.log); presets being rebuilt
  by scripts/build_presets.py (log /tmp/presets2.log).
- Livermore single-flight, CONUS, coarse 1 km grid: truth #1 for N394FX_R20 (344 m) and N413AK_R49 (245 m); #30 N788BM_R03,
  #9 N916KT_R22, not found N4040_R12 (coarse pass drops it). Debug: N4040 with coarse off, Bay-Area region -> truth #1
  (177 m, -2.447 vs -2.480); CONUS coarse off (233 s) -> truth not top (US-wide competitors -2.37): near-degenerate,
  constant-velocity line fits are explained by many (flight, point) combos once only v and d matter.
- Oyster regional rebuilt: multi truth #1 36 m (5.54 vs 4.45); single (jets on) N325PQ #1 225 m.
- Altenthann (freesound audio, NOT a video): helicopter D-HAIB R44 (12:41:58Z, 1.5 km) = auto event #2 (t0 219 s).
  Single-flight, Bavaria box, clock +-800 s: D-HAIB only #5 (1.7 km from truth); best is an unknown-reg flight 54 km away.
- YouTube: metadata + short download worked once, then bot-wall (yt-dlp and browser). Tried recording the Cornell
  FeederWatch live cam (Ithaca, 1.4 km from ITH airport, burned-in clock) + adsb.lol live API poller
  (examples/ithaca_feederwatch/{play.py,poll_adsb.py,rec2.sh}); blocked by "Sign in to confirm you're not a bot". Retry later.
- tests/test_core.py: 8 unit tests pass (96.07 Hz, readouts, gap table N pairs, no literal 920, offset filter vs author port,
  silence rule, crude score/rank sum).

### Milestone (09:10 PST): presets rebuilt, tests, README, UI e2e
- build_presets.py: tagged runs (`multi_region`, `multi_region_auto`, `single_region`, `multi_conus`, `single_conus`),
  `--phase regional|conus`, curves + heat maps for ranks 1-3 precomputed (server caches curves/heat JSON), default_tag per preset.
- Oyster Bay "manual" events = LSQ slider fit on the ridge + the author's measured ranges (E1 130-154 m/s, 4300-7800 m;
  E2 88-110 m/s, 1390-4690 m). Gap from fitted t0s = 921.46 s (tol 7.41 s).
  - multi_region: truth #1, 36 m, 5.540 vs 4.637 (19.5 %). multi_region_auto (auto ranges): #1, 36 m, 5.540 vs 4.447.
  - multi_conus: truth #1, 36 m, 5.540 vs 5.363 (N272GX+N169JM, 2664 km away): margin 3.3 % (author: 20.48 vs 15.25).
  - single_region (jets on, faint N325PQ): #1 at 225 m, -1.053 vs -1.302, dt +34.2 s (clock error recovered).
- Livermore single_region (Bay Area box, no coarse pass, clock +-5 s): truth ranks 1,1,1,3,2
  (N394FX 344 m, N4040 177 m, N413AK 245 m, N788BM #3 459 m, N916KT #2 136 m).
- Altenthann single_region (Bavaria, clock +-800 s): D-HAIB #5, 1.7 km. Cause found: acoustic fit v = 77 m/s
  (consistent over harmonics, rms 0.5 Hz) vs ADS-B ground speed 63 m/s => ~14 m/s wind (Doppler sees air-relative
  speed; the video's method uses ground speed). Documented as known issue.
- Server fix: /results listing skipped cache files (was 500). UI: satellite layer, Maps/Street View links, cached heat
  auto-load, preset region default, compare order. tests/ui_e2e.py drives the UI headless (playwright + system chrome).
- Upload E2E via curl: 19 min mp4 clip -> project 0b725f4ec7, auto-detect finds both events (t0 105.6 / 1025.8, conf 24 / 10.9),
  slider event readouts 190.72/100.34/1.389, trace LSQ event, multi search queued.

## Session 4 (Nov 9, from 09:20 PST)
- ce5d48d silence filter switches (silence_count_jets, N>2 consecutive/span windows, N>2 event-1 re-pick) + UI + test (reviewed, 22 tests pass).
- c2c4710 wind correction, default OFF (`wind=off|era5|manual|fit`, `wind_model=full|air_los`), Open-Meteo historical
  pressure-level winds (dopp/wind.py, cache data/wind/), per-candidate wind fit on the top rows, UI controls, unit test.
  Physics note: with a stationary listener and a uniform wind the correct moving-medium Doppler ratio is
  c_eff/(c_eff+v_r) with c_eff = c - W.m; the wind cancels to FIRST order (only ~0.5 m/s apparent-speed change for 14 m/s).
  'air_los' (air-relative source velocity only, as briefed) is offered as a switch but is not physically complete.
- Altenthann wind check (scripts/wind_experiment.py, Bavaria box, clock +-800 s; truth = within 1 km of the geotag):
  winds at 12:42Z were calm: 1.6 m/s at 850 m, 2.3 m/s at 1500 m (Open-Meteo). D-HAIB ADS-B: gs 63 m/s, track 146,
  position-derived speed 62.4 m/s. A fixed-v=63 retarded fit of the ridge gives rms 2.0-3.6 Hz vs 0.5-0.7 Hz free (v=77).
  So the 77 vs 63 m/s gap is NOT wind (and 77 m/s = 150 kt exceeds an R44's Vne of 130 kt): the event is probably not
  D-HAIB, or D-HAIB's acoustic signature is something else. Ranks of D-HAIB (truth_rank within 1 km: none in every run):
    off #5 (-1.378) | era5+full #5 (-1.375) | era5+air_los #5 (-1.218) | fit+full #5 (-1.236, wind at grid edge -20,+20) | fit+air_los: see below
- 5151eea score_norm (z | db_med) and score_agg (mean | sum) parameters; scripts/norm_experiment.py.
- fit+air_los: D-HAIB #8 (-0.902, fitted wind (-12,0) m/s); every flight gains from a free wind vector, so ranks don't improve.
  Conclusion: wind does not rescue Altenthann; the 77 m/s acoustic speed is not explained by the calm day's winds.

### Milestone (09:58 PST): third preset = outdoor VIDEO with a faint aircraft (task 3)
- Commons (geotagged + timestamped videos, User:Lorax/G. Edward Johnson, DC Jan 2024; adsb.lol 2024-01-13/14 ingested
  for a DC box): 8 clips checked against DCA traffic (approaches every ~2 min at 0.4-1.4 km). No usable aircraft curve:
  the one clean Doppler-like ridge (Tidal Basin 2024-01-14 10:27:12) rises 105->155 Hz in 0.6 s then decays (an engine
  RPM change, not Doppler; best ADS-B matches need CPA ~100 m vs 470 m real), FDR clip only flat post-CPA tones,
  other clips no auto events. Also looked at: Commons regex search of timestamped videos (2024-26), radio aporee /
  Internet Archive (audio only), Zenodo Urban Soundscapes of the World (360 video, but 2017-19, date only),
  MAVD Montevideo (2019), AeroSonicDB (audio). Projects deleted; wavs kept in examples/commons_cands/.
- WORKS: Cornell Lab FeederWatch live cam (Sapsucker Woods, Ithaca NY), HLS capture started 17:06:12.5Z (earlier worker's
  recorder), burned-in camera clock -> t=0 = 17:05:54Z (checked at t=10 s and 1800 s; stream latency 18.5 s).
  adsb.lol live API polled in parallel (60 nm) -> day store 2026-11-09. Auto-detect's two strongest events (conf 17.5,
  15.2) and #4 (rms 0.94 Hz, t0 1892.8 s, f0 121 Hz) are the US Army King Air 95-00088 (BE20, 7.2 km slant, 7.5 km alt,
  98 m/s, ADS-B heard-CPA 1927 s), harmonics at 2x visible. Single-flight, jets off (the video's method): 95-00088 #1
  (only velocity-matched prop), best point 1.60 km from the camera, dt -47 s. With wind=era5 (full model; 32 m/s
  wind at 7.5 km): -0.565 vs -0.660, 1.29 km, dt -17 s. Jets on: still #1 (-0.66 vs -1.89 for an E75L 67 km away).
  Preset tags single_region / single_region_wind (default). Clip saved as examples/ithaca_feederwatch/clip_20261109T170554Z.mp4.
- Task 4 so far (Oyster Bay CONUS, scripts/norm_experiment.py, data/norm_experiment.json): margin (s1-s2)/s1
  z_mean 3.21 % | db_med 2.91 % (scores 18.1+12.7 dB, same scale as the author's 10.6+9.8) | z_sum 3.21 | db_sum 2.91 |
  f0 sweep +-3 % 3.21 | tol_sigma_k 0 (pure +-5 s) 3.21. Normalisation does not explain the gap: the #2 combo's curves fit
  as well as the truth's (event 1 truth 3.25 vs 3.36 for #2; event 2 2.29 vs 2.00, the author's #2 had 5.73 vs 9.84).
- Task 4 results, continued (CONUS full day unless noted; margin m1 = (s1-s2)/s1, m2 = (s1-s2)/s2; the author's
  20.48 vs 15.25 is m1 25.5 % / m2 34.3 %, so the "34 %" is m2; ours full-day m2 = 3.3 %):
  | variant | truth rank | s1 | s2 | m1 | m2 |
  |---|---|---|---|---|---|
  | z_mean (default) | 1 | 5.540 | 5.363 | 3.21 % | 3.3 % |
  | db_med mean / sum | 1 | 30.73 | 29.84 | 2.91 % | 3.0 % |
  | z sum | 1 | | | 3.21 % | 3.3 % |
  | f0 sweep +-3 % / tol_sigma_k 0 / both | 1 | 5.540 | 5.363 | 3.21 % | 3.3 % |
  | f0 sweep +-2 % | 2 | 5.363 | 5.337 | 0.48 % | |
  | harmonics 3 (z) | 4 | 3.044 | 3.013 | 1.0 % | |
  | harmonics 3 (db_med) | 3 | 16.43 | 16.41 | 0.1 % | |
  | jets count in silence | not in top | 5.184 | 5.163 | 0.4 % | |
  | **known start +-900 s** | 1 | 5.540 | 3.570 | 35.6 % | **55 %** |
  | **known start +-300 s** | 1 | 5.540 | 3.276 | 40.9 % | **69 %** |
  Conclusion: normalisation, aggregation, harmonics and the f0 sweep do not explain the gap; on a whole day of adsb.lol
  data (more GA/UAT/MLAT coverage than FR24, and 80k dense candidates vs the author's 29k) other GA pairs fit the two
  curves almost as well (#2 N272GX+N169JM, Laramie WY). Constraining the reference CPA to the recording clock +-15 min
  (an existing option, `use_start_time` + `clock_uncertainty_s`) reproduces a margin at least as large as the author's
  without changing scoring. New preset tag `multi_conus_time900` (scripts/build_presets.py --only oyster_time): truth #1,
  36 m, #2 N17468+N313NR 400 km away at 3.57. Defaults unchanged (author's method).
- Task 5: screenshots refreshed for oysterbay, livermore_n4040_r12, altenthann, ithaca_feederwatch (ui_e2e: no JS
  errors; heat-map peaks 18 m / 137 m / 53.7 km / 447 m from truth). 26 tests pass (core + acceptance).

## Session 5 (Nov 9, from 10:10 PST): preset 5 = second outdoor live-cam VIDEO
- Candidates probed (yt-dlp works again for live HLS): audio + frame + adsb.lol traffic per cam. Rejected: Channel Islands Harbor
  (Oxnard; audio is canned "ambient", per the owner), Redondo/Friday Harbor/Jackson main/Hawaii Ozolio cams (silent), Waikiki and
  Sandy Hook (music), Hale Pau Hana Maui and Jackson NE (live audio but sparse ADS-B). Recorded 3 in parallel:
  Venice Beach (Teleport/Venice V Hotel, EO_1LWqsCNE), Port Lorain OH (1MVB3fgg7kg, 41.4736,-82.1781) and Pacifica Mori Point
  (iQ7XJDXYSMo, mast with WU station KCAPACIF205 37.631,-122.494). Lorain had one GA pass (P28A at the very start) and Pacifica only
  a 3.6 km helicopter, so Venice became the preset.
- The box paused ~10:25-10:36 (all processes froze, live HLS ffmpeg and pollers stalled). Fix: grab the window from the YouTube DVR
  (segments + PROGRAM-DATE-TIME, scripts/livecam/dvr_grab.py) and backfill tracks from https://adsb.lol/data/traces/XX/trace_full_HEX.json
  (scripts/adsblol_traces.py; 622 aircraft, 250k points) -> day store 2026-11-09_socal (630 flights).
- Venice clip: 18:13:58.98Z (first segment) + 3405 s; burned-in clock 1.7 s behind -> t=0 = 18:13:57.28Z. Truth = 5 Westminster Ave (OSM).
  Heard GA passes < 1.1 km: SR20 N882AB 325 s, C172 N1882S 675, PA46 N350TV 874, SLG4 N238F 1301, SLG4 N775AM 1507, P28R N56114 2312,
  C172 N171CP 2570, P46T N293SF 2821 (passes_nonjet_3km.txt). Auto-detect (80 events) has speed/distance-matched events for
  SR20 (#5 t0 325.4, v 55), PA46 (#3 874.5, v 52, d 499), P28R (#43 2314.1, v 56), C172 (#12 2575.0, v 42).
- Results (region 33.6-34.4 N, 118.95-118.0 W, jets off):
  | tag | event(s) | truth | #1 | dist |
  |---|---|---|---|---|
  | multi_region (default) | SR20 + PA46, gap 549.1 s | #1 | N882AB+N350TV 1.998 vs 1.430 (40 %) | 65 m |
  | multi_region_b | P28R + C172, gap 260.9 s | pair #3, 2.3 km off | N6856N+N513MS 1.429 | 2256 m |
  | single_region (clock +-5 s) | PA46 | #1 | -0.471 vs -0.510 | 216 m |
  | single_region_c172 (+-5 s) | C172 | #1 | -0.918 vs -0.954 | 456 m |
  | single_region_sr20 (+-5 s) | SR20 | #4 | N777GB -1.827 | 394 m |
  | single_region_p28r (+-5 s) | P28R | #4 | N63482 -1.320 | 202 m |
  | single_region_clock60 (default +-60 s) | PA46 | #3 | N958LA (dt 19 s) | 216 m |
- Fix: match-curve JSON had NaN where the curve ran past a flight's track coverage (500) -> samples dropped.
- 33 tests pass; screenshots refreshed for all 5 presets (venice heat-map peak 91 m from truth).

## Session 5 (Nov 9, from 11:20 PST): outdoor YouTube benchmark (task: performance + failure rate)
- Scripts: app/bench/ (copies of /workspace/doppler/bench/*.py; the bench dir holds audio, runs/, results).
  download.py (yt-dlp --download-sections bestaudio; the 2 Y2Mate MP3s cut locally), fetch_day.sh (adsb.lol release ->
  ingest, CONUS or Europe bbox 34-62N 12W-30E; tars deleted after ingest), run_bench.py (prep / truth / run), drive.py
  (2 clips at a time, 30 min hard timeout per run), aggregate.py (results.json + results.md).
- Auto policy (no human): auto-detect (max_events 40, half window 40 s), primary passes ranked by confidence, the two most
  confident >= 60 s apart -> multi-event; else single-flight (needs the time prior). blind = date only, whole store;
  prior = stream start +- prior_uncertainty_s (multi: time_window on event 1; single: time tol + dt sweep).
- First finding: 6 of the first 8 downloaded replays (all 3 Ocean City Live cams, Clearwater, Jacksonville, Destin,
  Moosburg) carry a digitally SILENT audio track (all-zero samples; opus ~3 kb/s). yt-dlp bot-walls ~1 in 3 requests
  when run 5-wide ("Sign in to confirm you're not a bot"); recorded as download failures, retried once serially.

## Session 6 (Nov 9, from 11:30 PST): Altenthann "fits harmonics?" (Nicholas, via viewer)
- Diagnosis (work/harm/altenthann_ridge_fit.png, comb_altenthann.png, altenthann_harmonic_v.png): the auto line (event 2, f0 102.8 Hz)
  is the 6th harmonic of a 17.13 Hz comb (42 visible harmonics up to ~1 kHz). NO hop: hop_check k_pre = k_post = 6.
  Per-harmonic retarded fits of the clean lines (rms < 1.5 Hz) all give v 76.3-78.2 m/s, d 1.17-1.26 km, t0 219.0-219.1 s;
  joint comb fit v 78.2 +- 0.1 m/s, d 1279 m. Model-free: line pre/post ratio 1.518 within only +-25-45 s (comb spacing 21.9 Hz
  before vs 14.1 Hz after = 1.55), which needs >= 70.5 m/s at c = 343 (>= 68 at 0 C); 63 m/s gives at most 1.45 at infinity.
  Rest comb 17.13 Hz is not an R44 (main-rotor BPF 13.6 Hz at 408 rpm, tail-rotor BPF 80.9 Hz, engine 45.3 Hz); 4-blade at 257 rpm
  matches the NH90 (256 rpm -> 17.07 Hz; cruise ~150 kt = 77 m/s), military, no ADS-B position. A scan of the whole recording
  (+-800 s) for an R44 comb along D-HAIB's own Doppler curve finds nothing distinct. Verdict: (d) a different, untracked aircraft.
- 076cfd6 dopp/harmonics.py (optional, default OFF): find_comb (F, k_line; neighbour + min-comb guards), comb_fit (joint
  (F,v,d,t0) over all visible harmonics, each traced in a band < half the line spacing), hop_check, asymptote_ratio,
  harmonic_refit; refine_event/detect_events harmonic_fit flag; server detect "harmonic_fit" + POST events/{eid}
  {"harmonic_refit": true} (needs a server restart to be live; not restarted). tests/test_harmonics.py (5).
- scripts/harmonic_experiment.py -> /workspace/doppler/data/harmonic_experiment.json: comb found only for Altenthann and Oyster E1
  (PC12, 111.9 Hz x4: v 143.1 vs single-line 137.4, ADS-B 136.8 = worse). Re-runs: Altenthann D-HAIB #5 both tags (unchanged),
  Oyster multi_region #1 5.540 vs 4.608 (was 4.637), multi_region_auto #1 5.540 vs 4.455 (was 4.447). Others: no comb, identical.
  -> stays opt-in. Viewer: Altenthann status note updated (still unsolved), re-exported --only altenthann; viewer_check clean.

## Session 7 (Nov 9, from 12:10 PST): benchmark resume
- OOM: 2 concurrent blind (whole-CONUS) runs reach ~8 GB each -> kernel OOM killed mickriley_20260930 blind (rc -9, recorded as
  "error" with empty stderr). drive.py now 1 worker (BENCH_WORKERS), re-runs empty-error (OOM) runs; silent clips prep without a day store.
- ocmd_9thst_20260806 also digitally silent (-91 dB). download.py (serial) still running from 11:58.

## Session 8 (Nov 9, from 13:00 PST): benchmark finish (deadline ~15:30)
- drive.py ran clips serially in candidate order with blind first (~30 min each) -> ~10 unprepped clips would take ~5+ h.
  Stopped drive.py (13:01) and chained bench/drive2.py after the in-flight mickriley_20260930 blind (wait_then_drive2.sh, kill at
  13:09:30 = 30 min cap -> status timeout). drive2: phase A prep/label/truth/prior/prior_single for all clips, phase B blind one at
  a time (30 min timeout), no blind started after 15:20 -> status not_run (time budget); rc -9 -> status oom. Log bench/drive2.out.
