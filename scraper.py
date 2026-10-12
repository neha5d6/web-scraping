
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "https://cignaforhcp.cigna.com"
DASHBOARD_URL = f"{BASE}/app/dashboard"
SEARCH_URL = f"{BASE}/app/patient/search"

HERE = Path(__file__).parent
OUT_DIR = HERE / "output"
PROFILE_DIR = HERE / "edge_profile"   # keeps your session/cookies locally
SAVE_HTML = False                     # True = also save raw HTML (useful to tune selectors; contains PHI)


# ------------------------------------------------------------------ helpers
def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def wait_for_login(page):
    """Open dashboard and wait until the user has logged in and the dashboard is shown."""
    page.goto(DASHBOARD_URL)
    print("Please log in in the Edge window (including any MFA). Waiting...")
    while True:
        try:
            page.wait_for_url(re.compile(r"/app/dashboard"), timeout=5000)
            page.wait_for_selector("text=Dashboard", timeout=5000)
            if page.query_selector("text=Welcome"):
                print("Logged in - dashboard detected.")
                return
        except PWTimeout:
            pass


def go_to_patient_search(page):
    """Click Patients menu -> Patient Search; fall back to the direct URL."""
    try:
        page.get_by_text("Patients", exact=True).first.click(timeout=8000)
        link = page.get_by_role("link", name=re.compile(r"patient\s*search", re.I)).first
        link.click(timeout=8000)
        page.wait_for_url(re.compile(r"/app/patient/search"), timeout=15000)
    except Exception:
        print("Menu click failed, opening Patient Search directly.")
        page.goto(SEARCH_URL)
    page.wait_for_selector("text=Patient Search", timeout=20000)
    print("Patient Search page is open.")


# ------------------------------------------------------------------ extraction
def extract_tables(page):
    tables = []
    for t in page.query_selector_all("table"):
        headers = [clean(h.inner_text()) for h in t.query_selector_all("thead th")]
        if not headers:
            headers = [clean(h.inner_text()) for h in t.query_selector_all("tr:first-child th")]
        rows = []
        trs = t.query_selector_all("tbody tr") or t.query_selector_all("tr")
        for tr in trs:
            cells = [clean(c.inner_text()) for c in tr.query_selector_all("td")]
            if not cells:
                continue
            rows.append(dict(zip(headers, cells)) if headers and len(headers) == len(cells) else cells)
        if rows:
            tables.append({"headers": headers, "rows": rows})
    return tables


def extract_key_values(page):
    pairs = {}
    for dl in page.query_selector_all("dl"):
        for dt, dd in zip(dl.query_selector_all("dt"), dl.query_selector_all("dd")):
            k = clean(dt.inner_text())
            if k:
                pairs[k] = clean(dd.inner_text())
    for line in page.inner_text("body").splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z0-9 /&()'.-]{1,60}?)\s*:\s*(.+)$", line)
        if m:
            pairs.setdefault(clean(m.group(1)), clean(m.group(2)))
    return pairs


def extract_headings(page):
    return [clean(h.inner_text()) for h in page.query_selector_all("h1,h2,h3,h4") if clean(h.inner_text())]


def scrape(page):
    return {
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "url": page.url,
        "title": page.title(),
        "headings": extract_headings(page),
        "key_values": extract_key_values(page),
        "tables": extract_tables(page),
        "raw_text": page.inner_text("body"),   # fallback so nothing is lost
    }


# ------------------------------------------------------------------ main
def main():
    OUT_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="msedge", headless=False, no_viewport=True
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        wait_for_login(page)
        go_to_patient_search(page)

        saved = 0
        while True:
            cmd = input("\nEnter patient details in Edge and click Search. "
                        "When results show, press Enter to scrape (q to quit, s to go back to Patient Search): ").strip().lower()
            if cmd == "q":
                break
            if cmd == "s":
                go_to_patient_search(page)
                continue
            # results may open in a new tab; use the most recent Cigna tab
            cigna_pages = [pg for pg in ctx.pages if "cignaforhcp.cigna.com" in pg.url]
            target = cigna_pages[-1] if cigna_pages else page
            data = scrape(target)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = OUT_DIR / f"patient_{stamp}.json"
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            if SAVE_HTML:
                (OUT_DIR / f"patient_{stamp}.html").write_text(target.content(), encoding="utf-8")
            saved += 1
            print(f"Saved {path.name}  (tables: {len(data['tables'])}, fields: {len(data['key_values'])})")

        ctx.close()
        print(f"Done. {saved} file(s) in {OUT_DIR}")


if __name__ == "__main__":
    sys.exit(main())