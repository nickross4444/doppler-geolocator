#!/bin/bash
# usage: bench/fetch_day.sh YYYY-MM-DD [us|eu]  -> data/days/<day>
# Downloads the adsb.lol globe_history prod release and ingests it (same path as the app).
# The downloaded tars are deleted after ingest (only the ones this script fetched).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
D=$1; R=${2:-us}; Y=${D:0:4}; TAG=v${D//-/.}-planes-readsb-prod-0
DAYS=${DOPP_DAYS:-$ROOT/data/days}
OUT=$DAYS/$D
[ -f $OUT/flights.parquet ] && { echo "have $D"; exit 0; }
TMP=$ROOT/data/adsblol/bench_$D; mkdir -p $TMP
assets=$(curl -s https://api.github.com/repos/adsblol/globe_history_$Y/releases/tags/$TAG | python3 -c "import json,sys;print(' '.join(a['browser_download_url'] for a in json.load(sys.stdin).get('assets',[])))")
[ -z "$assets" ] && { echo "NO RELEASE $TAG"; exit 2; }
parts=()
for u in $assets; do f=$TMP/$(basename $u); curl -sSL --retry 3 -o $f $u; parts+=($f); done
[ $R = eu ] && export INGEST_BBOX=34,62,-12,30
cd "$ROOT"
PY=${PYTHON:-$ROOT/.venv/bin/python}
[ -x "$PY" ] || PY=python3
mkdir -p "$ROOT/data"
$PY scripts/ingest_adsblol.py "$OUT" data/ref/icao_aircraft_types.json "${parts[@]}" | tee "data/ingest_bench_$D.log"
rm -rf $TMP
tail -1 "data/ingest_bench_$D.log"; echo "ok $D"
