"""Prepare a Vercel file-upload deployment of the static viewer (used with the Vercel connector's upload_file +
create_deployment tools, or any client of the Vercel REST API: POST /v2/files then POST /v13/deployments).

usage: ../.venv/bin/python scripts/vercel_manifest.py [--site ../viewer/site] [--work ../viewer/upload] [--mark-deployed]

Writes <work>/manifest.json (file, sha1, size, b64 path for every file), <work>/<sha>.b64 for each file,
<work>/changed.json (entries whose sha is not in <work>/deployed.json: only these need upload_file), and
<work>/files.json (the `files` array for create_deployment). --mark-deployed copies manifest.json to deployed.json
after a successful deployment."""
import os, json, hashlib, base64, argparse, shutil
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser(); ap.add_argument("--site", default=os.path.join(APP, "..", "viewer", "site"))
ap.add_argument("--work", default=os.path.join(APP, "..", "viewer", "upload")); ap.add_argument("--mark-deployed", action="store_true")
a = ap.parse_args(); site, work = os.path.abspath(a.site), os.path.abspath(a.work); os.makedirs(work, exist_ok=True)
if a.mark_deployed:
    shutil.copy(os.path.join(work, "manifest.json"), os.path.join(work, "deployed.json")); print("marked deployed"); raise SystemExit
man = []
for dp, _, fs in os.walk(site):
    for f in fs:
        p = os.path.join(dp, f); b = open(p, "rb").read(); sha = hashlib.sha1(b).hexdigest()
        bp = os.path.join(work, sha + ".b64")
        if not os.path.exists(bp): open(bp, "w").write(base64.b64encode(b).decode())
        man.append(dict(file=os.path.relpath(p, site), sha=sha, size=len(b), b64=bp))
man.sort(key=lambda x: x["file"])
dep = os.path.join(work, "deployed.json")
have = {x["sha"] for x in json.load(open(dep))} if os.path.exists(dep) else set()
changed = [x for x in man if x["sha"] not in have]
json.dump(man, open(os.path.join(work, "manifest.json"), "w"), indent=0)
json.dump(changed, open(os.path.join(work, "changed.json"), "w"), indent=0)
json.dump([dict(file=x["file"], sha=x["sha"], size=x["size"]) for x in man], open(os.path.join(work, "files.json"), "w"), separators=(",", ":"))
print(f"{len(man)} files, {sum(x['size'] for x in man) / 1e6:.1f} MB; {len(changed)} new/changed:")
for x in changed: print(f"  {x['sha']} {x['size']:>9} {x['file']}")
