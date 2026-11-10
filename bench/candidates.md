# Doppler benchmark candidates (outdoor YouTube livestream replays)

Built Nov 9 2026. All 22 are archived YouTube **livestream replays** of fixed cameras where aircraft are not the subject. Machine-readable list: `candidates.json`; generator: `make_candidates.py`; raw metadata: `meta/`.

**Truth time** = YouTube `liveBroadcastDetails.startTimestamp` (actual stream start, read from the watch page) + segment offset. `vod_missing_s` = (broadcast end - start) - VOD length. Near 0 means the VOD is continuous; large values mean a dropout, so the offsets after it are uncertain.
**Truth tiers:** `precise` (named fixed spot, ≤~250 m), `medium` (≤~500 m, the camera spot within a site isn't pinned yet), `coarse` (town/area only; score as a distance bucket, or pin it from frames first).
**Priors for the benchmark** (`available_time_prior`): `blind` = date only. `realistic` = the same stream-start metadata a user would see (± `prior_uncertainty_s`). `loose` = the VOD upload/processing timestamp (≈ stream end) ± `loose_uncertainty_s` (3–19 h).

| id | place | tier (±m) | segment UTC | dl | env | difficulty | audio |
|---|---|---|---|---|---|---|---|
| ocmd_plim_20260727 | Plim Plaza Hotel, Boardwalk at 2nd St, Ocean City MD | precise (100) | 2026-07-27T16:58Z +60m | mp3 | beach boardwalk, crowds, surf | medium | unknown (no music/no-audio note in descr |
| ocmd_dolles_20260703 | Dolle's (original), Boardwalk at Wicomico St, Ocean City MD | precise (150) | 2026-07-03T16:03Z +60m | mp3 | beach boardwalk, holiday-weekend crowds | medium-hard | unknown |
| ocmd_9thst_20260806 | 9th St & Boardwalk (Thrasher's), Ocean City MD | precise (150) | 2026-08-06T17:01Z +60m | mp3 | beach boardwalk | medium | unknown |
| mickriley_20260930 | Mick Riley Golf Course, Murray UT | medium (500) | 2026-09-30T22:42Z +60m | mp3 | golf course, suburban | medium | unknown |
| mickriley_20260921 | Mick Riley Golf Course, Murray UT | medium (500) | 2026-09-21T16:01Z +60m | mp3 | golf course, suburban | medium | unknown |
| chicago_lakepointtower_20260920 | Lake Point Tower, 505 N Lake Shore Dr, Chicago IL (high floor) | precise (60) | 2026-09-20T17:02Z +60m | mp3 | urban high-rise, lakefront | hard | LIKELY NO LIVE AUDIO: description says ' |
| sunnyisles_intracoastal_20260920 | Sunny Isles Beach high-rise overlooking the Intracoastal and William Lehman Causeway, FL | coarse (800) | 2026-09-20T16:00Z +60m | mp3 | urban high-rise, waterway, boats | hard | unknown (Reolink camera, may have mic) |
| clearwater_pier60_20260905 | Pier 60, Clearwater Beach FL | precise (250) | 2026-09-05T18:39Z +54m | mp3 | beach | medium | unknown (TV-station cam; may be silent) |
| destin_harborwalk_20261008 | HarborWalk Village / Emerald Grande, Destin FL | precise (200) | 2026-10-08T21:14Z +60m | mp3 | harbor, boats, tourist village | medium-hard | unknown (Beach TV streams sometimes carr |
| asheville_groveparkinn_20251031 | The Omni Grove Park Inn, Asheville NC | precise (200) | 2025-10-31T15:54Z +60m | mp3 | mountain resort, forest | medium | unknown |
| lorain_lighthouse_20260615 | Lorain Lighthouse (West Breakwater Light), Lorain OH | precise (250) | 2026-06-15T19:27Z +60m | mp3 | lakeshore breakwater, open water | easy-medium | CONFIRMED live audio (title) |
| jaxbeach_pier_20260626 | Jacksonville Beach Fishing Pier, Jacksonville Beach FL | precise (250) | 2026-06-26T19:41Z +109m | mp3 | beach pier, surf | medium | unknown |
| notting_hill_gate_20261016 | Outside Notting Hill Gate Station, London UK | precise (100) | 2026-10-16T21:42Z +60m | mp3 | dense urban street, buses, crowds | hard | CONFIRMED live audio (description) |
| stives_harbour_20260802 | St Ives Harbour, Cornwall UK (Classic Cottages cam) | medium (300) | 2026-08-02T18:15Z +60m | mp3 | harbour town, seaside | medium | unknown |
| berchtesgaden_markt_20260418 | Berchtesgaden town center (Marktplatz), Bavaria DE | precise (250) | 2026-04-18T11:54Z +60m | mp3 | alpine town square | hard | POSSIBLY MUSIC: 'may include licensed ba |
| krakow_rynek_20261108 | Main Market Square (Rynek Glowny), Krakow PL | precise (200) | 2026-11-08T10:00Z +30m | mp4 | urban old-town square, news event | hard | unknown (news live feed, likely live aud |
| moosburg_20240421 | North of Moosburg an der Isar, Bavaria DE | coarse (2000) | 2024-04-21T07:25Z +60m | mp3 | small town, suburban | medium | unknown |
| marquette_gales_20261105 | Lake Superior shoreline, Marquette MI (camera facing north) | coarse (3000) | 2026-11-05T18:19Z +60m | mp3 | lakeshore storm, 40+ kt gusts, big surf | very hard | unknown |
| marquette_night_20261031 | Lake Superior shoreline, Marquette MI (camera facing north) | coarse (3000) | 2026-11-01T00:59Z +60m | mp3 | quiet lakeshore at night | hard | unknown |
| elgin_sc_backyard_20261104 | Backyard feeder, Elgin SC | coarse (5000) | 2026-11-04T15:11Z +60m | mp3 | backyard, birdsong | medium | CONFIRMED natural sound ('No music. No t |
| gainesville_backyard_20261104 | Backyard feeder, Gainesville FL | coarse (10000) | 2026-11-04T16:39Z +60m | mp3 | backyard, suburban, pool noise | medium | CONFIRMED live audio ('The audio feed is |
| haun_ranch_20261106 | Haun Ranch near Meyersville TX (feeder camera) | coarse (8000) | 2026-11-06T16:32Z +60m | mp3 | rural ranch, quiet | easy-medium | unknown |

## Concerns
- **Audio unverified for most clips.** It's confirmed live only for Lorain, Notting Hill, Elgin and Gainesville. Chicago (Lake Point Tower) says it has a royalty-free music bed, and Berchtesgaden may carry Audio-Library music. Treat both as expected-fail or control cases. Val d'Isère and Coastal Café (Oxnard) were dropped because they state they have no live audio. Probe ~60 s of each clip first and mark `no_live_audio` rather than counting it as a localisation failure.
- **Truth time is not independent of the 'realistic' prior.** Both come from the stream-start metadata. Check independently with a burned-in clock (unchecked; needs frames) or a visible aircraft matched to ADS-B.
- **Downloader limits.** The VODs are 1–12 h long. If Y2Mate's 150 min/30 min caps apply to the *source* length rather than the selected range, only these fit: clearwater (54 min) and jaxbeach (110 min) as MP3. Lorain is 154 min, just over. Ranges are given in `segment` regardless.
- **Location precision.** 12 are precise, 4 medium and 6 coarse. Coarse sites need a frame-based pin, or should be scored with a km-scale tolerance.
- **Europe flight data.** adsb.lol coverage in the UK, Germany and Poland should be good. The Krakow stream is a news feed (The Sun), so confirm from frames that it's a real fixed cam.
- All segment days have adsb.lol prod archives. 2026 days come from the release list, where only 2026-05-06 is missing. 2025-10-31 and 2024-04-21 were checked individually. Note that marquette_night falls on UTC 2026-11-01.
