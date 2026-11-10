"""Headless check of the static viewer: every preset and every result renders with no console/page errors.
usage: ../.venv/bin/python tests/viewer_check.py --base <url> [--shots ../viewer/screenshots]"""
import asyncio, argparse, json, os, sys
from playwright.async_api import async_playwright
ap = argparse.ArgumentParser(); ap.add_argument("--base", required=True); ap.add_argument("--shots", default="")
ap.add_argument("--all-results", action="store_true"); A = ap.parse_args()

async def main():
    errs, bad = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
        pg = await b.new_page(viewport={"width": 1680, "height": 1100})
        pg.on("pageerror", lambda e: errs.append(("pageerror", str(e))))
        pg.on("console", lambda m: errs.append(("console", m.text)) if m.type == "error" else None)
        pg.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
        await pg.goto(A.base, wait_until="networkidle")
        await pg.wait_for_selector("#resTable tr[data-r]", timeout=60000)
        presets = await pg.evaluate("() => S.index.presets.map(p => p.id)")
        report = []
        for pid in presets:
            await pg.select_option("#projSel", pid)
            await pg.wait_for_function(f"() => S.pid === '{pid}' && S.proj && S.proj.meta.id === '{pid}' && S.res && document.querySelectorAll('#resTable tr[data-r]').length > 0", timeout=60000)
            rids = await pg.evaluate("() => S.proj.results.map(r => r.id)") if A.all_results else [await pg.evaluate("() => S.res.id")]
            for rid in rids:
                await pg.select_option("#resSel", rid)
                await pg.wait_for_function(f"() => S.res && S.res.id === '{rid}' && document.querySelectorAll('#compare canvas').length > 0", timeout=60000)
                await pg.wait_for_load_state("networkidle"); await pg.wait_for_timeout(800)
                info = await pg.evaluate("""() => {
                  const c = document.querySelector('#specImg'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                  let lit = 0; for (let i = 0; i < d.length; i += 4 * 97) if (d[i] + d[i+1] + d[i+2] > 30) lit++;
                  return {res: document.querySelector('#resSel').selectedOptions[0].text, rows: document.querySelectorAll('#resTable tr[data-r]').length,
                    events: document.querySelectorAll('#evTable tr[data-id]').length, cmp: document.querySelectorAll('#compare canvas').length,
                    heat: document.querySelector('#heatMsg').innerText, spec_lit_frac: +(lit / (d.length / (4 * 97))).toFixed(3),
                    gaps: document.querySelector('#gapTable').innerText.split('\\n').slice(1, 3).join(' | '),
                    banner: document.querySelector('#statusBanner').hidden ? '' : 'UNSOLVED banner shown', heatlayer: !!document.querySelector('.leaflet-heatmap-layer')}; }""")
                info["preset"] = pid; report.append(info); print(json.dumps(info), flush=True)
            if A.shots and pid in ("oysterbay", "ithaca_feederwatch", "altenthann"):
                await pg.screenshot(path=os.path.join(A.shots, f"viewer_{pid}.png"), full_page=False)
        if A.shots:
            await pg.select_option("#projSel", "oysterbay")
            await pg.wait_for_function("() => S.pid === 'oysterbay' && S.res", timeout=60000)
            await pg.select_option("#resSel", await pg.evaluate("() => S.proj.results.find(r => r.tag === 'multi_conus').id"))
            await pg.wait_for_function("() => S.res && S.res.tag === 'multi_conus'", timeout=60000)
            for k in (2, 3): await pg.click(f"#resTable tr[data-r='{k}'] .cmpchk")
            await pg.wait_for_load_state("networkidle"); await pg.wait_for_timeout(1500)
            await pg.locator("#compare").screenshot(path=os.path.join(A.shots, "viewer_oysterbay_compare.png"))
            await pg.click("#aboutBtn"); await pg.wait_for_timeout(500)
            await pg.locator("#about").screenshot(path=os.path.join(A.shots, "viewer_about.png"))
        await b.close()
    bad = [x for x in bad if "favicon" not in x[1]]
    print("ERRORS:", json.dumps(errs)); print("HTTP>=400:", json.dumps(bad))
    sys.exit(1 if errs or bad else 0)
asyncio.run(main())
