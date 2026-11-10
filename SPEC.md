# Doppler-Curve Audio Geolocation: Implementation Spec (Phase 1)

Source: colsto, "I discovered a sound that leaks your exact location (and it's everywhere)", https://youtu.be/UytYrhYLOKI (29:11, published 2026-11-02).
Example media: https://youtu.be/4CxVxuMVkMc ("Unknown location - raw audio", 1 h).

How to read the citations:
- `[T mm:ss]` is a paragraph in `source/transcript.md`. Captions are auto-generated, so wording can be slightly off.
- `[KF file]` is a file in `source/key_frames/`.
- `[D]` is `source/video.description`.
- **INFERRED** marks anything not shown or said in the video. Everything else was transcribed from the video.

The author released no code [D line 14]. Everything below is rebuilt from the transcript, the on-screen tools and on-screen code.

---

## 0. Ground truth for the reference case (used to accept the build)

| Item | Value | Citation |
|---|---|---|
| Stated prior | Filmed **Sept 8** somewhere in CONUS | [T 00:00] |
| Original recording file | `oyster bay_20260913T191804Z_60to120min` | [KF 03m50s_audacity_spectrogram_oysterbay_filename.jpg] |
| Example YouTube audio | Minutes 60–120 of the original recording, so example t = original elapsed − 3600 s. Verified: the event-1 ridge descends from about 169 Hz to 86 Hz over example t ≈ 2100–2215 s. | own check on `examples/example1_audio_mono8k.wav` |
| Event 1 | Original elapsed 1:35:52, i.e. example 35:52 (2152 s). Flight **1100069709**. | [KF 18m25s_right_vs_wrong_on_real_spectrogram_dt_f0.jpg], [T 17:39] |
| Event 2 | Original elapsed 1:51:13 (example 51:13). About 920 s after event 1. Flight **1100048665**. | [KF 09m15s_doppler_curve_fit_tool_event2.jpg], [T 02:27] |
| Faint single flight | Original elapsed 1:21:16 (example 21:16), "8:37 p.m. UTC". Flight **1100029523**. | [KF 23m20s_single_flight_histogram_all_points.jpg], [T 22:58] |
| True location | ≈ **40.876466, −73.524951** (Oyster Bay, NY waterfront; security camera on a pole) | [KF 21m42s_top_combos_spectrogram_overlays.jpg], [KF 25m45s_street_view_security_camera_pole.jpg], [T 25:17] |
| Two-event winner | combo 1100069709 + 1100048665, score 10.64 + 9.84 = **20.48**. Next best is 15.25. | [T 21:31], [KF 21m32s_scatter_best_point_per_combo.jpg] |
| Single-flight winner | flight 1100029523 at 40.877922, −73.531941, score −1.74, dt +0.45 s | [KF 23m50s_single_flight_top_results_coords.jpg] |

There is a date discrepancy. The video says Sept 8, but the filename says 20260913. 19:18:04Z + 1:21:16 ≈ 20:39Z, which is consistent with "8:37 p.m. UTC" if the file starts at 19:18:04Z. **OPEN Q1.**

---

## 1. Pipeline overview (order matters)

1. **Ingest audio.** Upload video or audio, extract mono PCM, and build a spectrogram (STFT).
2. **Detect and measure Doppler events.** This can be automatic or manual. For each event, record the CPA time in audio time, the ranges for speed v and slant distance d, and f0. See §3.
3. **ADS-B corpus.** Collect every flight for the day (or the time window), resample to 1 s, and drop jets by ICAO type code. See §2.
4. **Velocity filter.** Keep flight segments whose ground speed is inside each event's v-range. See §4.1.
5. **Candidate points.** Lay a 200 m grid. For a measured event, use only points whose slant distance at CPA is inside the event's d-range (normally the grid reaches 10 km). Compute each point's delayed CPA time for each flight using a DEM. See §4.2.
6. **Two-event offset filter.** A point must hear event-1 and event-2 flights separated by the observed gap (920 s), with a ± tolerance. Implemented as a batch nearest-neighbour search. See §4.3.
7. **Silence filter.** Drop any point that heard another (non-jet) flight between the two events, with the threshold distance = max(CPA dist 1, CPA dist 2). Done as Pass 1 (cheap, from the point's own lists) and then Pass 2 (cKDTree over all trackpoints). See §4.4.
8. **Crude rank** (match_score from speed and distance errors, plus silence_penalty, giving combined_rank) and a diagnostic scatter. See §4.5.
9. **Densify** the survivors from 200 m to 100 m. See §5.4.
10. **Physics curve model.** For each candidate point and flight, compute the emission-time (retarded) radial velocity from interpolated ADS-B, then the Doppler frequency over time. See §5.1.
11. **Locked two-event spectrogram scoring.** Sweep dt and f0 for event 1. Event 2 gets an f0 sweep only, with its time locked by geometry. The score is the sum of spectrogram brightness under the curve. See §5.2.
12. **Group and rank.** Keep the best point per flight-pair combo. Show a scatter of event1 vs event2 scores, the top-N spectrogram overlays, a QGIS-style heat map, and a map / Street View link. See §6.
13. **Single-flight known-time mode.** Search US-wide for one flight with a time constraint. Score against a hand-traced line, plot a histogram, and keep the best per flight. See §7.

---

## 2. ADS-B data

- The author used Flightradar24. There is a paid API, and the Python package **pyfr24** (github stiles/pyfr24) "to scrape the data". He ran it "for a few hours" for all CONUS flights on Sept 8. It downloads whole flight paths, including legs to Hawaii and abroad. [T 02:58], [KF 03m05s_pyfr24_flightradar_api_package.jpg]
- The `flight_ids_airborne` CSV has these columns: `flightid, registration, typecode, callsign, first_seen_utc, last_seen_utc, max_alt, sample_lat, sample_lon`. [KF 03m58s_flight_ids_airborne_csv_columns.jpg]
- The per-flight track layer `flight_<id>_track_1sec` is **resampled to 1 s**. Fields: `fid, timestamp(epoch s), utc_time, lat, lon, alt_ft, gs_kt, vs_fpm, track_deg, valid`. [T 04:25], [KF 04m29s_qgis_flight_track_points_200m_grid.jpg]
  - The description says "interpolated ADS-B data". [D line 6]
  - The interpolation method is **INFERRED**: linear for lat/lon/alt and linear for speed.
- The scripts are named "download flightpaths v1–v6". Files are 50 GB parquet. [T 02:02]
- **Prop/jet filter.** The lookup table is ICAO DOC 8643 Aircraft Type Designators. Its "Engine Type" column covers Jet, Piston, Turboprop/Turboshaft, Electric and so on. [KF 04m02s_icao_doc8643_aircraft_type_designators_lookup.jpg]
  - Because the table is missing data, the filter drops **known jet types** and keeps everything else, unknowns included. [T 03:58]
  - In code this is the `nonjet_ids` set, plus a `PROP_ONLY` setting. [KF 02m04s_code_cheap_silence_check.png]
- **Our substitute data source (DECISION D1).**
  - FR24 is paid.
  - The free source is **adsb.lol `globe_history_2026`**, under ODbL. Release `v2026.09.08-planes-readsb-prod-0` exists (2 parts, about 3.9 GB). Its readsb `trace_full_<hex>.json` traces carry lat, lon, alt_baro/geom, gs, track and so on.
  - Flight IDs will be ICAO hex + segment rather than FR24 flight ids. Results can still be cross-checked by registration.
  - OpenSky Trino needs a research account.
- **DEM.**
  - The author used a CONUS-wide DEM, `dem_mosaic_1km` (Band 1), at roughly 1 km resolution. [T 05:54], [KF 06m21s_dem_mosaic_1km_slant_distance_delayed_cpa.jpg]
  - **INFERRED** substitute: SRTM / Copernicus GLO-90, resampled, or the USGS 3DEP 1 km mosaic.
  - Use ADS-B altitude as MSL. Altitude type (baro vs geom) is **OPEN Q9**.

---

## 3. Spectrogram and measurement (manual and automatic)

### 3.1 Spectrogram display
- The author zooms in on frequency and **increases resolution** to see the ridges. [T 01:32], [KF 01m50s_spectrogram_zoomed_event_selection_1247-1314s.jpg]
- The colour bar runs from 0 to −80 dB. [KF 10m30s_doppler_curve_fit_tool_snapshot.jpg]
- Exact STFT parameters (window, nfft, hop) are **not shown**. **OPEN Q2.**
  - Proposed default: Hann window, nfft 16384 at 48 kHz (≈2.9 Hz bins), hop 0.1–0.25 s.
  - The UI should expose these settings.
- Audacity is used for the overview, with boost and high-pass. [KF 03m50s_audacity_spectrogram_oysterbay_filename.jpg]

### 3.2 "Doppler Curve Fit" measurement tool (manual)
Web tool with these sliders [KF 09m15s_doppler_curve_fit_tool_event2.jpg], [KF 10m10s_doppler_curve_fit_tool_t0_slider.jpg], [KF 10m30s_doppler_curve_fit_tool_snapshot.jpg], [T 09:20–10:19]:
- **f0** (fundamental frequency, Hz)
- **v** (plane speed, m/s)
- **d** (CPA slant distance, m)
- **t0** (CPA time in recording time, e.g. 1:51:14.80)

The overlay is a straight-line constant-velocity curve. Readouts, verified against the on-screen numbers:
- `f_pre = f0·c/(c − v)` (approach asymptote)
- `f_post = f0·c/(c + v)` (recede asymptote)
- `max RoC = f0·v²/(c·d)` (Hz/s at CPA)
- with c = 343 m/s.
- Check: f0 = 131.5, v = 106.5, d = 3130 gives f_pre 190.72, f_post 100.34, RoC 1.389. ✔

The curve model is **INFERRED** to be the classic straight-line one:

```
f(t) = f0·c / (c + v_r),   v_r(t) = v²(t−t0) / sqrt(d² + v²(t−t0)²)
```

The display window is the event ±~50 s (01:50:30–01:52:15).

A snapshot log records "MIN/MAX auto-calculated, BEST = midpoint". The user sets a generous min/max fit by hand, and BEST = (MIN + MAX)/2 for v and d. [KF 10m30s_doppler_curve_fit_tool_snapshot.jpg], [T 09:49]

Measured ranges for the reference case [KF 09m35s_two_event_measured_ranges_slide.jpg], [KF 12m42s_recap_slide_920s_ranges.jpg]:

| Event | Speed | CPA distance |
|---|---|---|
| Event 1 | 130–154 m/s | 4300–7800 m |
| Event 2 | 88–110 m/s | 1390–4690 m |
| Gap | 920 s | |

CPA timing has a few seconds of uncertainty ("best match 151.15 … 151.13") [T 10:19].

### 3.3 Automatic detection (our addition; the video does it manually)
- **INFERRED/new.** Detect ridges in 50–1500 Hz:
  - Find spectral peaks per frame.
  - Link them into tracks.
  - Fit the §3.2 model by nonlinear least squares, giving v, d, t0, f0 with confidence intervals, which become the min/max ranges.
  - Group harmonics (k·f0).
  - Report candidate events for the user to confirm.
- The manual tool must stay available and match the video exactly.

---

## 4. Coarse search (grid, CPA, filters)

### 4.1 Velocity filter
- Keep only (non-jet) flight segments whose `seg_speed_mps` is in the event's v-range. The map shows event 1 in green and event 2 in pink. [T 10:19–10:45]
- `seg_speed_mps` comes from the track near CPA. The exact segment definition is **INFERRED**: ground speed at CPA, gs_kt × 0.514444.

### 4.2 Grid, CPA and delayed CPA
- The grid uses a **200 m** spacing. Only points within **10 km** of a flight are used for the plain offset filter. [T 04:25], [T 04:58], [KF 05m22s_grid_10km_radius_around_flight.jpg]
- With measured events, points are placed only in the slant-distance band [d_min, d_max] around each velocity-matched segment. [T 10:45]
- For each point and flight:
  - The CPA is the minimum over the track's 1 s points of the 3D **slant distance** sqrt(horiz² + (alt_msl − ground_elev)²). Ground elevation comes from the DEM. [T 05:54]
  - `delayed_cpa_time = cpa_time + slant_dist/c`. Example: CPA 22:46:34 is heard at 22:46:46, about 3+ km away. [T 05:26], [T 06:21], [KF 06m21s_dem_mosaic_1km_slant_distance_delayed_cpa.jpg]
- Per-point attributes form the layer `gridpoints_heard_multiple` with fields `fid, n_flights, flight_ids, retarded_cpa_times`. [T 06:50], [KF 06m58s_gridpoints_heard_multiple_attributes.jpg]
- The code also carries per-point lists `all_flightids_k, all_cpa_times_k, all_cpa_distances_k` and the per-event chosen `flightid_k, cpa_time_utc_k, cpa_distance_m_k, seg_speed_mps_k`. [KF 02m04s_code_cheap_silence_check.png]
- Scripts are named "1.1 generate candidates v1–v7". The comments mention `TREE_QUERY_MAX_LAT_DEG` and a fix for "full-CONUS distortion" from projecting through a single UTM zone. The decision was to work in lat/lon degrees with a conservative radius and an exact haversine check. [KF 02m04s_code_full_silence_check_spatial_lookup_crude_score.png]

### 4.3 Two-event offset filter: `best_combo_batch_nearest_neighbor(joined, observed_gap)`
Transcribed from [KF 02m02s_code_best_combo_batch_nearest_neighbor.png]. For each candidate row r, with lists times1 (event-1 matching flights' delayed CPA) and times2:

```python
sort_idx = np.argsort(times2); times2_sorted = times2[sort_idx]
targets = times1 - observed_gap
insert_pos = np.searchsorted(times2_sorted, targets)
pos_lo = np.clip(insert_pos - 1, 0, m - 1); pos_hi = np.clip(insert_pos, 0, m - 1)
diff_lo = (times1 - times2_sorted[pos_lo]) - observed_gap
diff_hi = (times1 - times2_sorted[pos_hi]) - observed_gap
use_lo = np.abs(diff_lo) <= np.abs(diff_hi)
best_diff_per_i = np.where(use_lo, diff_lo, diff_hi)
# keep argmin |best_diff| over i → winning (flightid_1, flightid_2, times, dists, speeds)
```

- Output columns: `flightid_1/2, cpa_time_utc_1/2, cpa_distance_m_1/2, seg_speed_mps_1/2, predicted_gap_s, time_diff_s, abs_time_diff_s`.
- Points are kept if `abs_time_diff_s ≤ tolerance`.
- The tolerance is "a bit of wiggle room", described as about ±5 s from where the centre is measured. [T 07:17], [KF 07m44s_offset_window_tolerance_green_box.jpg]
- The exact tolerance value is **OPEN Q3**. Proposed default: ±10 s.
- **Sign:** with `targets = times1 − gap`, the matching flight satisfies times1 − times2 ≈ gap. So in the code, "1" is the **later** event, or else the gap is passed as negative. **OPEN Q4.** Implement it with explicit (earlier, later) naming.
- Counts in the video:
  - About 35k survivors in the test area (plain offset). [T 07:47]
  - About 73k / 70k with speed + distance + offset, CONUS. [T 11:13], [T 12:42], [KF 11m13s_speed_distance_filtered_points_73k_airports.jpg]

### 4.4 Silence filter (two passes)
The rule comes from a slide [KF 12m10s_silence_filter_logic_slide.jpg], [T 12:08]:
- Disqualify any point that heard ≥1 flight between event 1 and event 2.
- Audible threshold distance = **max(event1 CPA, event2 CPA)**, with **no multiplier**. 1.5× or 2× was considered and rejected.
- So `SILENCE_EXCLUDE_RATIO = 1.0`. The constant name comes from the code; the value is inferred from the slide.

**Pass 1: `cheap_silence_check_from_own_lists(row, nonjet_ids)`** [KF 02m04s_code_cheap_silence_check.png]

```python
w_lo = min(cpa_time_utc_1, cpa_time_utc_2); w_hi = max(...)
own_max_dist = max(cpa_distance_m_1, cpa_distance_m_2)
exclude_threshold = own_max_dist * SILENCE_EXCLUDE_RATIO
closest_other = None
for fids, times, dists in ((all_flightids_1, all_cpa_times_1, all_cpa_distances_1),
                           (all_flightids_2, all_cpa_times_2, all_cpa_distances_2)):
    for fid, t, d in zip(fids, times, dists):
        if fid == flightid_1 or fid == flightid_2: continue   # signal, not noise
        if str(fid) not in nonjet_ids: continue               # jets don't count
        if w_lo <= t <= w_hi and (closest_other is None or d < closest_other):
            closest_other = d
excluded = closest_other is not None and closest_other < exclude_threshold
return excluded, closest_other
```

**Pass 2: `build_track_spatial_index(tracks)`**
- Builds `cKDTree(np.column_stack([lat, lon]))` over **every raw non-jet trackpoint**, in degree space.
- Also keeps aligned `flightids` (Utf8) and `timestamps` (float64).
- Rows with null lat/lon/timestamp are dropped.
- There is no per-flight interpolation and no DEM; it is a coarse geometric check. [KF 02m04s_code_full_silence_check_spatial_lookup_crude_score.png]

**`full_silence_check_spatial_lookup(cand_lat, cand_lon, w_lo, w_hi, flightid_1, flightid_2, own_max_dist, tree, tree_flightids, tree_timestamps, radius_m=AUDIBILITY_SEARCH_RADIUS_M)`**

```python
exclude_threshold = own_max_dist * SILENCE_EXCLUDE_RATIO
search_radius_m = min(radius_m, exclude_threshold)
meters_per_deg_lon_min = 111320.0 * math.cos(math.radians(TREE_QUERY_MAX_LAT_DEG))
degree_radius = search_radius_m / meters_per_deg_lon_min        # conservative
nearby_idx = tree.query_ball_point([cand_lat, cand_lon], r=degree_radius)
if not nearby_idx: return False, None
nearby_idx = np.array(nearby_idx); fids = tree_flightids[nearby_idx]; ts = tree_timestamps[nearby_idx]
keep = (ts >= w_lo) & (ts <= w_hi) & (fids != str(flightid_1)) & (fids != str(flightid_2))
if not keep.any(): return False, None
tree_points = tree.data[nearby_idx[keep]]
dists_m = haversine_m(cand_lat, cand_lon, tree_points[:,0], tree_points[:,1])
closest_other = float(dists_m.min()); return closest_other < exclude_threshold, closest_other
```

- Pass 2 uses **horizontal** haversine distance against the slant-based threshold, which is conservative.
- **Values for constants:**
  - `TREE_QUERY_MAX_LAT_DEG`: **INFERRED** ≈ 50 (the northern edge of CONUS).
  - `AUDIBILITY_SEARCH_RADIUS_M`: **INFERRED** 10 000 (the grid radius).
  - Both are **OPEN Q5**.
- **`apply_real_silence_check`** runs Pass 2 on the Pass-1 survivors and **overwrites** `silence_penalty = 0.0 if closest_other is None else −closest_other`. [KF 02m05s_code_score_points_crude_apply_real_silence.png]
- About 70k points drop to about 15k. [T 12:42]

### 4.5 Crude scoring and ranking
- **`crude_speed_distance_score`** [KF 02m04s_code_full_silence_check_spatial_lookup_crude_score.png]:
  `−( |seg_speed_mps − v_audio_best|/v_audio_best + |cpa_distance_m − d_best|/d_best )` (≤ 0).
- **`score_points_crude(points_df, nonjet_ids, label)`**:
  - `match_score = s1 + s2` with `EVENT{1,2}_V_AUDIO_BEST` and `EVENT{1,2}_D_BEST` (the BEST midpoints from §3.2).
  - Non-finite rows are dropped.
  - Then Pass 1, which sets `silence_penalty`.
  - It logs "[label] Pass 1 (cheap): N valid … → n excluded".
  - Separate runs are made per "band", i.e. per label. [KF 02m05s_code_score_points_crude_apply_real_silence.png]
- **`finalize_ranks`** runs once on the combined set across all bands [KF 02m05s_code_finalize_ranks_build_scatter.png]:

```python
silence_rank = rank(silence_penalty, method="ordinal")
_match_rank = rank(match_score, "ordinal", descending=True)
_silence_rank_for_combo = rank(silence_penalty, "ordinal", descending=False)
combined_rank = rank(_match_rank + _silence_rank_for_combo, "ordinal")
```

  - The docstring says "rank-sum of match_score descending + silence_penalty ascending".
  - Reproduce it exactly as written. Note that ascending silence_penalty ranks the *noisiest* points first, which may be an author bug. **OPEN Q6.**
- **`build_scatter_html`** (Plotly 2.32.0):
  - x = −silence_penalty ("quieter = better"), y = match_score.
  - Hover shows combined_rank and abs_time_diff_s.
  - Points within `TARGET_HIGHLIGHT_RADIUS_M` of TARGET_LAT/LON are coloured red, `rgb(255, 200·frac, 200·frac)` with frac = dist/radius; points outside are blue. This is a debug view for known-answer validation.

---

## 5. Fine search: physics curve model and locked scoring

### 5.1 Doppler curve model (emission-time radial velocity)
Sources: [T 14:46–17:39], [KF 15m15s_radial_velocity_flightpath_tool.jpg], [KF 16m05s_step_by_step_position_mode.jpg], [KF 16m40s_step_by_step_doppler_equation.jpg], [KF 20m36s_step_by_step_delayed_mode_step11_formulas.jpg], [KF 17m40s_right_vs_wrong_curve_delayed.jpg]

For an observer at P (lat, lon, DEM elevation), take the aircraft track X(t_e), interpolated from 1 s ADS-B, in local ENU metres. For each reception time t_r:

1. Solve **t_r = t_e + |X(t_e) − P| / c** for t_e. The equation is monotonic, so use fixed-point or Newton iteration.
2. **v_r = V(t_e) · (X(t_e) − P)/|X(t_e) − P|**. This is positive when moving away. V is the full 3D velocity from the track (gs, track, vs).
3. **f(t_r) = f0 · c / (c + v_r)**, with c = 343.

Worked example on screen (step 11/11):
- t_r = 25.00 s gives t_e = 16.910 s.
- X = (2367, 1264, 1020) m, V = (140.0, 16.8, 0.0) m/s.
- r = 2775 m, v_r = 121.16 m/s.
- f = 130·343/(343 + 121.16) = **96.07 Hz**.

**Use this as a unit test.** The sliders were Aircraft speed 140, CPA distance 980, Altitude 1020, f0 130.

The "wrong" Position mode uses v_r at t_r. The video shows it is a different curve, not just shifted in time. It is useful as a teaching toggle. [T 16:40], [T 17:11]

Other examples:
- At 7:12:35, v_r = 62.7 gives 109.9 Hz with f0 = 130. [T 16:09]
- The interactive drag-flightpath tool uses speed 90 m/s, f0 500 Hz, a ±15 s window, altitude at path start/end 600 ft, and a "propagation delay" toggle. [KF 15m35s_drag_flightpath_doppler_tool_propagation_delay.jpg]

### 5.2 Locked two-event spectrogram scoring
Sources: [T 18:11–20:01], [KF 18m25s_right_vs_wrong_on_real_spectrogram_dt_f0.jpg], [KF 18m45s_scoring_sweep_dt.jpg], [KF 19m45s_locked_two_event_scoring_viewer.jpg]

- **Score** = the sum of spectrogram brightness values that the modelled curve covers. [T 18:11]
  - The exact definition is **INFERRED**: the mean over curve samples of normalized dB, taken from the bin nearest f(t). Scores are around 8–15 per event and can be negative, which suggests per-band z-scored or median-subtracted dB.
  - **OPEN Q7.** The candidate-curve grid frame [KF 18m10s_many_candidate_curves_grid.jpg] is labelled "FlightID lat, lon".
- **Placement.** The model's delayed CPA (from geometry) is anchored at the **measured** CPA time in audio time.
- **Event 1** sweeps **dt** ("a few seconds") and **f0** (around the measured f0). The best combination wins. [T 19:03]
  - Examples on screen: Δt −0.20 s with f0 113.0, and Δt −21.90 s with f0 112.5.
  - Sweep ranges and steps are **OPEN Q8**. Proposed default: dt ±5 s in 0.05 s steps, f0 ±3% in 0.1 Hz steps.
- **Event 2** sweeps **f0 only**. Its time is the event-1 placement plus the **geometry-predicted** offset ("one shared dt shifts BOTH events' modelled curves"). A bad event-2 offset therefore lowers the score on purpose. [T 19:30], [KF 19m45s_locked_two_event_scoring_viewer.jpg]
  - Example: event1 8.89, event2 8.43 at dt = 0.74 s.
- Total = event1_score + event2_score.
- Script names: `1.2 filter+score v4–v9`, `1.2a v1–v10`, `1.2b v1–v6/v7`. The output is `curve_match_scores_locked_spectrogram`. [KF 21m05s_scatter_event1_vs_event2_all_points.jpg]

### 5.3 Spectrogram used for scoring
- A per-event band, for example around 80–220 Hz for event 1 and 2.
- The faint flight sits at about 300–510 Hz. [KF 23m00s_single_flight_spectrogram_faint_curve.jpg]
- Parameters are **OPEN Q2**.

### 5.4 Densification
- 200 m is too coarse. The correct point can be up to 141 m (the half-diagonal) away from the nearest grid point and score badly. [T 20:01]
- Expand survivors to **100 m**, i.e. 2×2 sub-points per 200 m point, centred. That gives about 30–40k points to score. [T 20:35], [KF 20m41s_densified_100m_grid.jpg]
- The exact sub-grid layout is **INFERRED**.

---

## 6. Grouping, results and visualisation

- **Scatter "Event1 Score vs Event2 Score"**: 29 245 points from `1_2b_v7.py`. Top-right is best. [T 21:03], [KF 21m05s_scatter_event1_vs_event2_all_points.jpg]
  - Options: **colour by flight-pair combo**, and **show only the best point per combo (by match_score)**. [KF 21m32s_scatter_best_point_per_combo.jpg]
- **Grouping rule:** per (flightid_1, flightid_2) combo, keep the max total score. Rank combos by that score. [T 21:03–21:31]
- **Top-N comparison**: for each top combo, show the event-1 and event-2 spectrograms with the modelled curve overlaid, plus the scores. [KF 21m42s_top_combos_spectrogram_overlays.jpg], [KF 22m22s_second_best_combo_overlays.jpg], [T 21:31–22:02]

  | Rank | Flights | Location | Score |
  |---|---|---|---|
  | 1 | 1100069709 + 1100048665 | (40.876466, −73.524951) | 10.64 + 9.84 = 20.48 |
  | 2 | 1100088546 + 1100072793 | (45.699498, −73.643756) | 9.52 + 5.73 = 15.25 |
  | 3 | 1100029918 + 1100060374 | (36.454080, −94.456016) | 15.04 |

- **Heat maps**: re-run at finer resolution using only the winning flights. Show event-1-only, event-2-only and combined surfaces, as in QGIS. [T 22:02–22:32], [KF 22m50s_qgis_heatmap_oyster_bay.jpg]
  - The heat-map grid resolution is **INFERRED**: 10–25 m.
- **Verify**: open the coordinate in Google Maps / Street View. [T 25:17]

---

## 7. Single-flight known-time mode

Sources: [T 22:58–23:52], [KF 23m00s_single_flight_spectrogram_faint_curve.jpg], [KF 23m20s_single_flight_histogram_all_points.jpg], [KF 23m40s_single_flight_top_curves_traced_line.jpg], [KF 23m50s_single_flight_top_results_coords.jpg]

- Inputs: a one-minute clip, a known time ("8:37 p.m. UTC"), and the v-range from measurement. Search US-wide, keeping only segments at that time.
- Because the curve is very faint, the user **traces it by hand** as a polyline (`LINE_POINTS`, "traced composite line"). Each candidate is scored by how close its curve is to that line, not by brightness.
- The metric is **INFERRED**: −mean |f_model(t) − f_line(t)| in Hz over the traced span, after a dt and f0 sweep. Scores range from −137.78 to −1.74.
- **Histogram** "All Scored Points — 1:21:16": n = 380 127, min −137.78, max −1.74, mean −51.44. Right is better. There is a **best point per flight** toggle.
- Top results:

  | Rank | Flight | Location | Score | dt |
  |---|---|---|---|---|
  | 1 | 1100029523 | 40.877922, −73.531941 | −1.74 | +0.45 s |
  | 2 | 1100057337 | 41.044589, −77.739976 | −3.68 | |
  | 3 | 1100026508 | 43.692244, −79.325785 | −4.66 | +0.50 s |

---

## 8. Countermeasures section (for the tool's "robustness" demo; optional)

- After high-pass → MP3 → boost, the curve returns. [T 26:13], [KF 26m15s_highpass_countermeasure.jpg], [KF 26m40s_mp3_boost_curve_returns.jpg]
- Drowning the curve out with speech or a speaker fails. [T 26:45]
- The curve survives phone-call compression and noise reduction. [T 27:14]
- Cutting the audio defeats the offset method. [T 27:41]

---

## 9. App requirements (from owner brief) mapped to the above

1. Upload video or audio, or pick a preloaded example. The app extracts audio and shows a zoomable spectrogram.
2. Fit Doppler curves:
   - **Manual**: the §3.2 sliders and readouts, an event marker, and a polyline trace for §7.
   - **Automatic**: §3.3.
3. Search configuration:
   - Date or time window.
   - Region (CONUS default, or a bbox or city).
   - The event list with ranges, the gap, and prop-only.
4. Pipeline: §4 → §5, with the same functions, ordering, two-pass silence, crude ranks, densify, and locked scoring.
5. Results:
   - Scatter (colour by combo, best per combo).
   - Top-N table and spectrogram overlays.
   - Map with points and heat maps, using a Leaflet/MapLibre basemap plus a satellite layer and a Street View link.
   - Side-by-side comparison of the top matches.
   - Histogram for single-flight mode.
6. Teaching panels: the Position vs Delayed step-by-step (§5.1) and the drag-flightpath demo.

---

## 10. Open questions and decisions

| # | Question |
|---|---|
| Q1 | Recording date: Sept 8 (stated) or Sept 13 (filename `20260913T191804Z`)? We need the right ADS-B day. Plan: test both. adsb.lol has both days. |
| Q2 | STFT parameters (window, nfft, hop, dB normalization) are not shown. |
| Q3 | Offset-filter tolerance (± s). |
| Q4 | Sign convention of `observed_gap` / which event is "1" in `best_combo_batch_nearest_neighbor`. |
| Q5 | `TREE_QUERY_MAX_LAT_DEG` and `AUDIBILITY_SEARCH_RADIUS_M` values. `SILENCE_EXCLUDE_RATIO` = 1.0 per the slide. |
| Q6 | `_silence_rank_for_combo` sorts ascending, which looks inverted. Replicate it verbatim, with a flag. |
| Q7 | Exact brightness-score definition and normalization (scores can be negative). |
| Q8 | dt and f0 sweep ranges and step sizes. |
| Q9 | ADS-B altitude: barometric or geometric? (Matters for slant distance and the DEM.) |
| Q10 | ADS-B interpolation method and 1 s resampling details; how `seg_speed_mps` is derived. |
| Q11 | Heat-map resolution and colour scale. |

| # | Decision |
|---|---|
| D1 | Data source. FR24 / pyfr24 is the same as the author's but paid. adsb.lol is free (ODbL) and is about 4 GB per day for the world. adsb.lol is recommended. Flight IDs will differ from the video's FR24 ids. |
| D2 | DEM: 3DEP / Copernicus resampled to about 1 km to match `dem_mosaic_1km`, or full resolution. |
| D3 | Scale. A full-CONUS run is heavy: about 50 GB parquet and hours of compute. Precompute the reference day offline and ship it as cached results, with live search limited to a region or time window. |
| D4 | Extra examples (see the final report): they are mostly near-airport livestreams with jets. The prop-only filter must be switchable. |
