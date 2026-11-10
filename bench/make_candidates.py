import json,re,datetime as dt,glob
UTC=dt.timezone.utc
# parse liveBroadcastDetails captured from watch pages
lbd={}
for f in glob.glob('meta/lbd*.txt'):
    for l in open(f):
        m=re.match(r'(\S+) (\{.*?\}) len (\d+) upload (\S+)',l.strip())
        if m:
            d=json.loads(m.group(2)); lbd[m.group(1)]=dict(start=d['startTimestamp'],end=d.get('endTimestamp'),len=int(m.group(3)),upload=m.group(4))
        else:
            m=re.match(r'(\S+) (\{.*?\}) len (\d+)',l.strip())
            if m and m.group(1) not in lbd:
                d=json.loads(m.group(2)); lbd[m.group(1)]=dict(start=d['startTimestamp'],end=d.get('endTimestamp'),len=int(m.group(3)),upload=None)
def meta(i):
    try: return json.load(open(f'meta/full/{i}.json'))
    except: return {}
TZ={'ET':-4,'EST':-5,'CT':-5,'CST':-6,'MT':-6,'UK':1,'CE':2,'CET':1}
C=[
# id, vid, location, lat, lon, loc_unc_m, truth_basis, tier, seg0, seg1, dl, env, diff, tzoff_h, audio, notes
("ocmd_plim_20260727","tTv1Tz7nrP4","Plim Plaza Hotel, Boardwalk at 2nd St, Ocean City MD",38.33297,-75.08499,100,"Named fixed cam on the Plim Plaza Hotel (OSM node for the hotel); boardwalk/beach view","precise",1800,5400,"mp3","beach boardwalk, crowds, surf",'medium',-4,"unknown (no music/no-audio note in description)","Summer Monday midday. Beach banner-tow planes (props, low, slow) and helicopter rides run along the OCMD beach in summer; OXB (Ocean City Muni) 5 km SW. Crowd noise can mask faint ridges."),
("ocmd_dolles_20260703","-fpNyovU42o","Dolle's (original), Boardwalk at Wicomico St, Ocean City MD",38.32750,-75.08680,150,"Description: 'Location: Dolle's Wicomico Street'; north-facing at the Ripley's shark; boardwalk geocode","precise",18000,21600,"mp3","beach boardwalk, holiday-weekend crowds",'medium-hard',-4,"unknown","July 3 (holiday eve) midday: heavy banner-plane traffic expected, but also heavy crowd noise. Within 600 m of the Plim Plaza cam; treat as same site, different day."),
("ocmd_9thst_20260806","P9w6gW2iFhQ","9th St & Boardwalk (Thrasher's), Ocean City MD",38.33990,-75.08080,150,"Description: '9th Street live camera' at the boardwalk; street-end geocode","precise",3600,7200,"mp3","beach boardwalk",'medium',-4,"unknown","VOD is 117 s shorter than the broadcast span (dropout somewhere) -> time drift up to ~2 min after the gap; check a burned-in clock if any."),
("mickriley_20260930","dttAxGKFr0c","Mick Riley Golf Course, Murray UT",40.66012,-111.87346,500,"Title: 'from Mick Riley Golf Course, Murray, UT'; OSM course centroid (camera spot on the course not yet pinned)","medium",1800,5400,"mp3","golf course, suburban",'medium',-6,"unknown","SLC Intl ~15 km NW, KSLC arrivals/departures plus GA and medevac helicopters (IMC hospital ~2 km). Course is ~800 m across: refine camera position from frames (clubhouse vs a tee)."),
("mickriley_20260921","j2z9TMF8AxE","Mick Riley Golf Course, Murray UT",40.66012,-111.87346,500,"Same cam as mickriley_20260930","medium",7200,10800,"mp3","golf course, suburban",'medium',-6,"unknown","Monday mid-morning. Same site, second day (tests repeatability)."),
("chicago_lakepointtower_20260920","psYo-GGsXsk","Lake Point Tower, 505 N Lake Shore Dr, Chicago IL (high floor)",41.89153,-87.61325,60,"Description: '24/7 live view from Lake Point Tower looking north-east over Navy Pier'; building geocode + Wikipedia coords","precise",25200,28800,"mp3","urban high-rise, lakefront",'hard',-5,"LIKELY NO LIVE AUDIO: description says 'royalty-free ambient under the live picture'","Expected-fail / control case: music bed; camera ~150-200 m above ground (DEM ground height will be wrong). Downtown helicopters, MDW/ORD traffic. Keep only if a live mic is mixed under the music."),
("sunnyisles_intracoastal_20260920","4avenZCp-GI","Sunny Isles Beach high-rise overlooking the Intracoastal and William Lehman Causeway, FL",25.95300,-80.12500,800,"Description: 'from Sunny Isles Beach overlooking Aventura and the William Lehman Causeway' (building not named)","coarse",10800,14400,"mp3","urban high-rise, waterway, boats",'hard',-4,"unknown (Reolink camera, may have mic)","Dense FLL/MIA/OPF traffic + helicopters; boat engines produce Doppler-like ridges (false positives). VOD 189 s shorter than broadcast span. Pin the building from frames to tighten truth."),
("clearwater_pier60_20260905","Jp8v5zxY6_E","Pier 60, Clearwater Beach FL",27.97756,-82.83104,250,"FOX 13 webcam 'provides views of the surrounding Pier 60'; Pier 60 geocode (camera likely on a nearby building)","precise",0,3265,"mp3","beach",'medium',-4,"unknown (TV-station cam; may be silent)","Whole VOD (54 min) fits the MP3 limit. Labor Day weekend Saturday afternoon. PIE/TPA traffic, beach banner planes, helicopter tours."),
("destin_harborwalk_20261008","4DtOXBZZXHY","HarborWalk Village / Emerald Grande, Destin FL",30.39470,-86.51223,200,"Description: 'located at HarborWalk Village in Destin, Florida. @TheEmeraldGrande'; Emerald Grande geocode","precise",0,3600,"mp3","harbor, boats, tourist village",'medium-hard',-5,"unknown (Beach TV streams sometimes carry music)","DTS (Destin Executive) ~3 km E, Eglin AFB/Hurlburt military traffic and helicopters. Boat engines = false-positive risk."),
("asheville_groveparkinn_20251031","f4NlUrFEy14","The Omni Grove Park Inn, Asheville NC",35.62017,-82.54195,200,"Description: 'live view from the Grove Park Inn in Asheville, NC'; Wikipedia/OSM coords","precise",10800,14400,"mp3","mountain resort, forest",'medium',-4,"unknown","Only 2025 candidate (adsb.lol 2025-10-31 exists). AVL ~15 km S; mountain terrain tests the DEM. Halloween Friday midday."),
("lorain_lighthouse_20260615","my6GvU6lG0U","Lorain Lighthouse (West Breakwater Light), Lorain OH",41.47758,-82.19053,250,"Title '24/7 Lorain Lighthouse Cam with Audio'; 'live views from the historic Lorain Lighthouse'; Wikipedia coords of the lighthouse","precise",0,3600,"mp3","lakeshore breakwater, open water",'easy-medium',-4,"CONFIRMED live audio (title)","Quiet water setting; LPR (Lorain County Regional) ~12 km S, Cleveland Hopkins ~30 km E, Lake Erie GA traffic. Camera may be on the lighthouse or ashore looking at it (±250 m)."),
("jaxbeach_pier_20260626","RbTcWotnuik","Jacksonville Beach Fishing Pier, Jacksonville Beach FL",30.29357,-81.38771,250,"Title 'Jacksonville Beach Fishing Pier / Overview Live Cam'; OSM pier geocode","precise",0,6575,"mp3","beach pier, surf",'medium',-4,"unknown","Whole VOD (110 min) fits MP3 limit. NAS Jax/Mayport Navy helicopters + JAX/CRG traffic; surf noise broadband."),
("notting_hill_gate_20261016","Mo2NKB6CsPc","Outside Notting Hill Gate Station, London UK",51.50893,-0.19620,100,"Description: 'Live webcam and audio outside Notting Hill Gate Station'; OSM/Wikipedia station coords","precise",0,3600,"mp3","dense urban street, buses, crowds",'hard',1,"CONFIRMED live audio (description)","Europe/urban stress test. Heathrow arrivals (jets) pass over west London; Met Police/air-ambulance helicopters. VOD 135 s shorter than broadcast span. Friday evening 22:42 BST."),
("stives_harbour_20260802","pJqJK39dD7w","St Ives Harbour, Cornwall UK (Classic Cottages cam)",50.21250,-5.47850,300,"Title 'St Ives Harbour Live Cam'; harbour geocode (camera building not pinned)","medium",7200,10800,"mp3","harbour town, seaside",'medium',1,"unknown","UK coastal: Coastguard/RAF helicopters, Land's End (LEQ) and Newquay (NQY) GA. Ozolio labels it a 'highlight' but the VOD length equals the broadcast span (continuous). Sunday afternoon."),
("berchtesgaden_markt_20260418","Hda-Fun16Lg","Berchtesgaden town center (Marktplatz), Bavaria DE",47.63276,13.00147,250,"Title/description: public fixed webcam 'Berchtesgaden Town Center'; Marktplatz geocode","precise",0,3600,"mp3","alpine town square",'hard',2,"POSSIBLY MUSIC: 'may include licensed background audio from the YouTube Audio Library'","Alpine valley, SZG (Salzburg) ~20 km N; helicopter rescue traffic common. Check whether there is live audio at all before scoring."),
("krakow_rynek_20261108","BO2swbOl6vE","Main Market Square (Rynek Glowny), Krakow PL",50.06150,19.93711,200,"The Sun live view 'of Main Market Square in Krakow' (dated news event); square geocode","precise",3600,7200,"mp4","urban old-town square, news event",'hard',1,"unknown (news live feed, likely live audio)","Day after a reported shooting: police/media helicopters plausible. KRK ~11 km W. MP4 chosen so frames can confirm it is a live fixed cam and read any clock."),
("moosburg_20240421","14XwZobL100","North of Moosburg an der Isar, Bavaria DE",48.46670,11.93330,2000,"Title 'Live Webcam Germany, Bavaria 85368 Moosburg an der Isar', 'view over the North of Moosburg' (private cam; town-level only)","coarse",0,3600,"mp3","small town, suburban",'medium',2,"unknown","Munich Airport (MUC) ~12 km SW: many jets + GA. 2024 date (adsb.lol 2024-04-21 exists). Truth only town-level unless frames pin the house."),
("marquette_gales_20261105","M1bPaKMfLZM","Lake Superior shoreline, Marquette MI (camera facing north)",46.55000,-87.39500,3000,"Description: 'Camera is facing north over Lake Superior from Marquette' (exact building not given)","coarse",0,3600,"mp3","lakeshore storm, 40+ kt gusts, big surf",'very hard',-5,"unknown","Deliberately hard: wind + 14 ft surf. MQT (Sawyer) ~25 km S, little traffic. Coarse truth."),
("marquette_night_20261031","UqDQ-iIqjdw","Lake Superior shoreline, Marquette MI (camera facing north)",46.55000,-87.39500,3000,"Same channel/cam as marquette_gales","coarse",3600,7200,"mp3","quiet lakeshore at night",'hard',-4,"unknown","Night (Halloween ~21:00 EDT), very low traffic -> likely few or no events; segment falls on UTC 2026-11-01 (adsb.lol day 11-01)."),
("elgin_sc_backyard_20261104","IH3Pm_inS8k","Backyard feeder, Elgin SC",34.17098,-80.79425,5000,"Description: 'feeders in Elgin, South Carolina' (town only)","coarse",7200,10800,"mp3","backyard, birdsong",'medium',-5,"CONFIRMED natural sound ('No music. No talking.')","Realistic 'backyard' case. CAE ~30 km SW, McEntire JNGB (F-16s) ~15 km S, Shaw AFB ~45 km E. VOD 106 s short vs span."),
("gainesville_backyard_20261104","63E_s5QAOGE","Backyard feeder, Gainesville FL",29.65197,-82.32498,10000,"Title 'Bird Feeder Cam | Gainesville, FL' (city only)","coarse",10800,14400,"mp3","backyard, suburban, pool noise",'medium',-5,"CONFIRMED live audio ('The audio feed is live')","GNV ~8 km NE of city centre. City-level truth only."),
("haun_ranch_20261106","537CyuO5dkk","Haun Ranch near Meyersville TX (feeder camera)",28.92832,-97.31249,8000,"Description: 'Haun Ranch near Meyersville, TX Feeder Camera' (rural, town-level)","coarse",0,3600,"mp3","rural ranch, quiet",'easy-medium',-6,"unknown","Quiet rural site: best SNR case if audio is live. Victoria (VCT) ~30 km SE; Ag aircraft, oil-field helicopters."),
]
ADSB_2026=set(l.strip()[1:11] for l in open('meta/rel2026.txt') if 'prod-0' in l)
out=[]
for (cid,vid,loc,lat,lon,unc,tb,tier,s0,s1,dl,env,diff,tz,audio,notes) in C:
    L=lbd.get(vid); M=meta(vid)
    st=dt.datetime.fromisoformat(L['start']) if L else dt.datetime.fromtimestamp(M['release_timestamp'],UTC)
    en=dt.datetime.fromisoformat(L['end']) if L and L['end'] else None
    ln=L['len'] if L else M.get('duration')
    drift=round((en-st).total_seconds()-ln) if en else None
    seg_utc0=st+dt.timedelta(seconds=s0); seg_utc1=st+dt.timedelta(seconds=s1)
    day=seg_utc0.strftime('%Y.%m.%d')
    if seg_utc0.year==2026: adsb='exists' if day in ADSB_2026 else 'MISSING'
    elif day=='2025.10.31': adsb='exists (globe_history_2025 v2025.10.31-planes-readsb-prod-0, checked)'
    elif day=='2024.04.21': adsb='exists (globe_history_2024 v2024.04.21-planes-readsb-prod-0, checked)'
    else: adsb='unchecked'
    if seg_utc0.date()!=seg_utc1.date(): adsb+='; segment crosses UTC midnight -> need both days'
    up=L.get('upload') if L else None
    if not up and M.get('timestamp'): up=dt.datetime.fromtimestamp(M['timestamp'],UTC).isoformat()
    upd=dt.datetime.fromisoformat(up) if up else None
    mid=seg_utc0+(seg_utc1-seg_utc0)/2
    loose_unc=int(abs((upd-mid).total_seconds())//3600+1)*3600 if upd else None
    unc_s=max(60,abs(drift or 0)+30)
    dur_min=(s1-s0)/60
    if dl=='mp4' and dur_min>30: s1=s0+1800
    out.append(dict(
        id=cid,url=f"https://www.youtube.com/watch?v={vid}",youtube_id=vid,title=M.get('title'),channel=M.get('channel'),
        location_name=loc,lat=lat,lon=lon,location_uncertainty_m=unc,truth_tier=tier,truth_basis=tb,
        vod_broadcast_start_utc=st.isoformat(),vod_broadcast_end_utc=en.isoformat() if en else None,vod_duration_s=ln,
        vod_missing_s=drift,
        utc_start=seg_utc0.isoformat(),utc_end=(st+dt.timedelta(seconds=s1)).isoformat(),
        local_start=(seg_utc0+dt.timedelta(hours=tz)).strftime('%Y-%m-%d %H:%M')+f" (UTC{tz:+d})",
        time_basis=f"YouTube liveBroadcastDetails.startTimestamp (actual stream start) + segment offset; VOD length vs broadcast span differs by {drift} s" + (" (no gap, offset mapping reliable to ~30 s)" if drift is not None and abs(drift)<=60 else " (dropout or trim: offsets after the gap may be early by up to that much; confirm with a burned-in clock if visible)"),
        time_uncertainty_s=unc_s,
        segment=dict(start_s=s0,end_s=s1),download_type=dl,
        available_time_prior=dict(
            realistic="YouTube 'Streamed live' metadata (actual start time, public via the page / yt-dlp release_timestamp) + offset into the VOD",
            realistic_uncertainty_s=unc_s,
            loose=f"VOD upload/processing timestamp {up} (roughly the stream end) with the segment somewhere before it" if up else "upload date only",
            loose_utc=up, loose_uncertainty_s=loose_unc,
            blind=f"date only ({seg_utc0.date().isoformat()} UTC)", visible_clock="unchecked (inspect frames)"),
        prior_uncertainty_s=unc_s,
        adsblol_archive=adsb,
        environment=env,expected_difficulty=diff,audio=audio,notes=notes))
json.dump(out,open('candidates.json','w'),indent=1)
print(len(out))
for o in out: print(o['id'],o['utc_start'],o['vod_missing_s'],o['adsblol_archive'][:40],o['available_time_prior']['loose_uncertainty_s'])
