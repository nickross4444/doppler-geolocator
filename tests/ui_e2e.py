"""Headless end-to-end drive of the UI (playwright + system Chrome) and screenshots.
usage: ../.venv/bin/python tests/ui_e2e.py [--base http://localhost:8765] [--project oysterbay] [--out screenshots]"""
import asyncio, argparse, os, sys, time
from playwright.async_api import async_playwright
ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://localhost:8765")
ap.add_argument("--project", default="oysterbay"); ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "screenshots"))
ap.add_argument("--prefix", default=None)
A = ap.parse_args(); os.makedirs(A.out, exist_ok=True); PFX = A.prefix or A.project

async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
        pg = await b.new_page(viewport={"width": 1680, "height": 1050}, device_scale_factor=1)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await pg.goto(A.base, wait_until="networkidle")
        await pg.wait_for_selector("#resTable tr[data-r]", timeout=60000)
        t0 = time.time()
        await pg.select_option("#projSel", A.project)
        await pg.wait_for_timeout(300)
        # wait for results table + compare canvases
        await pg.wait_for_selector("#resTable tr[data-r]", timeout=60000)
        await pg.wait_for_selector("#compare canvas", timeout=60000)
        await pg.wait_for_function("() => document.querySelector('#heatMsg').innerText.length > 0", timeout=60000)
        t_load = time.time() - t0
        await pg.wait_for_timeout(2500)
        info = await pg.evaluate("""() => ({summary: document.querySelector('#summary').innerText,
            heat: document.querySelector('#heatMsg').innerText, nres: document.querySelectorAll('#resTable tr[data-r]').length,
            nev: document.querySelectorAll('#evTable tr[data-id]').length, gaps: document.querySelector('#gapTable').innerText,
            res: document.querySelector('#resSel').selectedOptions[0]?.text})""")
        # 1) spectrogram with fitted curves (left column top)
        ev = await pg.query_selector("#evTable tr[data-id] .evgo")
        await pg.locator("#left .panel").nth(1).screenshot(path=f"{A.out}/{PFX}_1_spectrogram_fits.png")
        # zoom on the first selected event
        sel = await pg.query_selector_all("#evTable tr[data-id]")
        for tr in sel:
            if await (await tr.query_selector(".evsel")).is_checked():
                await (await tr.query_selector(".evgo")).click(); break
        await pg.wait_for_timeout(1500)
        await pg.locator("#left .panel").nth(1).screenshot(path=f"{A.out}/{PFX}_2_event_zoom_with_match_curve.png")
        await pg.locator("#left .panel").nth(2).screenshot(path=f"{A.out}/{PFX}_3_sliders_events.png") if False else None
        await pg.locator("#left .panel").nth(3).screenshot(path=f"{A.out}/{PFX}_3_events_gaps.png")
        # 2) map with heat map
        await pg.locator("#right .panel").nth(1).screenshot(path=f"{A.out}/{PFX}_4_map_heat.png")
        await pg.locator("#right .panel").nth(2).screenshot(path=f"{A.out}/{PFX}_5_top_matches.png")
        # 3) comparison: tick #2 and #3
        for k in (1, 2):
            boxes = await pg.query_selector_all("#resTable .cmpchk")
            if k < len(boxes) and not await boxes[k].is_checked(): await boxes[k].click(); await pg.wait_for_timeout(1500)
        await pg.wait_for_timeout(2500)
        await pg.locator("#right .panel").nth(3).screenshot(path=f"{A.out}/{PFX}_6_compare.png")
        await pg.screenshot(path=f"{A.out}/{PFX}_0_full.png", full_page=True)
        await b.close()
    print(f"loaded in {t_load:.1f}s"); print(info)
    if errors: print("JS errors:", errors); sys.exit(1)
asyncio.run(main())
