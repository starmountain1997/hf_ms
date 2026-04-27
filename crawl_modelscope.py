"""Crawl ModelScope top 1000 downloaded models with LMDB progress and parallel crawling."""

import asyncio
import csv
import json
import re
import signal
from pathlib import Path

import lmdb
from g_playwright.firefox import create_playwright_context, find_firefox_profile, human_delay

DB_PATH = Path(__file__).parent / "modelscope_crawl.lmdb"
CSV_PATH = Path(__file__).parent / "modelscope_top1000.csv"
BASE_URL = "https://modelscope.cn/models"
MAX_MODELS = 10
LIST_CONCURRENCY = 3     # parallel list pages
DETAIL_CONCURRENCY = 3   # parallel detail pages
MAX_PAGES = 200          # safety limit


# ── LMDB helpers ──────────────────────────────────────────────────────

def _init_db() -> lmdb.Environment:
    return lmdb.open(str(DB_PATH), map_size=100 * 1024 * 1024)


def _save_models(env: lmdb.Environment, models: list[dict]) -> None:
    with env.begin(write=True) as txn:
        for m in models:
            key = m["path"].encode()
            existing = txn.get(key)
            data = {**(json.loads(existing) if existing else {}), **m}
            txn.put(key, json.dumps(data, ensure_ascii=False).encode())


def _save_pages_done(env: lmdb.Environment, pages: list[int]) -> None:
    with env.begin(write=True) as txn:
        txn.put(b"__pages_done__", json.dumps(pages).encode())


def _load_pages_done(env: lmdb.Environment) -> list[int]:
    with env.begin() as txn:
        raw = txn.get(b"__pages_done__")
        return json.loads(raw) if raw else []


def _all_models(env: lmdb.Environment) -> list[dict]:
    models = []
    with env.begin() as txn:
        for key, val in txn.cursor():
            if key.startswith(b"__"):
                continue
            models.append(json.loads(val))
    return models


def _sorted_models(env: lmdb.Environment) -> list[dict]:
    """Return all models sorted by downloads descending."""
    models = _all_models(env)

    def _dl(m):
        try:
            return int(m.get("downloads", "0").replace(",", ""))
        except (ValueError, AttributeError):
            return 0

    models.sort(key=_dl, reverse=True)
    return models


# ── Scrapers ──────────────────────────────────────────────────────────

async def _scrape_list_page(context, page_num: int) -> list[dict]:
    """Extract model paths from one list page."""
    url = f"{BASE_URL}?page={page_num}&sort=downloads"
    page = await context.new_page()
    try:
        await page.set_viewport_size({"width": 1920, "height": 1080})
        await page.goto(url, wait_until="networkidle")

        for _ in range(6):
            await page.mouse.wheel(0, 3000)
            await asyncio.sleep(human_delay(0.8, 1.5))
        await page.wait_for_timeout(2000)

        models = await page.evaluate("""() => {
            const models = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[href*="/models/"]');
            for (const link of links) {
                const href = link.getAttribute('href');
                if (!href || !href.startsWith('/models/') || href.includes('/tags/')) continue;
                const path = href.replace('/models/', '');
                if (seen.has(path)) continue;
                seen.add(path);
                models.push({
                    path,
                    name: path.split('/').pop(),
                    url: 'https://modelscope.cn/models/' + path,
                });
            }
            return models;
        }""")
        return models
    finally:
        await page.close()


async def _scrape_detail(context, url: str) -> dict:
    """Extract detail info from one model page by parsing embedded JSON."""
    page = await context.new_page()
    try:
        await page.set_viewport_size({"width": 1920, "height": 1080})
        await page.goto(url, wait_until="load")
        await page.wait_for_timeout(5000)

        html = await page.content()

        # ModelScope embeds model data as JSON with escaped quotes
        downloads_m = re.search(r'\\"Downloads\\":(\d+)', html)
        stars_m = re.search(r'\\"Stars\\":(\d+)', html)
        created_m = re.search(r'\\"CreatedTime\\":(\d+)', html)

        downloads = downloads_m.group(1) if downloads_m else 'N/A'
        likes = stars_m.group(1) if stars_m else 'N/A'
        publishTime = created_m.group(1) if created_m else 'N/A'

        # Extract author from URL path
        path = url.replace("https://modelscope.cn/models/", "")
        author = path.split("/")[0] if "/" in path else "N/A"

        # modelType from Tasks array - extract ChineseName from the first task
        modelType = "N/A"
        tasks_idx = html.find('\\"Tasks\\":[')
        if tasks_idx >= 0:
            depth = 0
            end = tasks_idx
            for i, c in enumerate(html[tasks_idx:]):
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        end = tasks_idx + i + 1
                        break
            tasks_json = html[tasks_idx:end]
            chinese_names = re.findall(r'\\"ChineseName\\":\\"([^\\]+)\\"', tasks_json)
            modelType = chinese_names[0] if chinese_names else "N/A"

        return {
            "modelType": modelType,
            "author": author,
            "likes": likes,
            "downloads": downloads,
            "publishTime": publishTime,
        }
    finally:
        await page.close()


# ── Crawl phases ──────────────────────────────────────────────────────

async def _crawl_list_pages(env: lmdb.Environment, context) -> int:
    """Crawl list pages in parallel until we have MAX_MODELS or exhaust pages."""
    pages_done = _load_pages_done(env)
    total = len(_all_models(env))

    if pages_done:
        print(f"[resume] pages done: {pages_done}")
        print(f"[resume] known models: {total}")

    if total >= MAX_MODELS:
        print(f"Already have {total} models >= {MAX_MODELS}")
        return total

    page = max(pages_done) + 1 if pages_done else 1

    while total < MAX_MODELS and page <= MAX_PAGES:
        batch = list(range(page, min(page + LIST_CONCURRENCY, MAX_PAGES + 1)))
        print(f"  list pages {batch[0]}-{batch[-1]} …")

        results = await asyncio.gather(
            *[_scrape_list_page(context, p) for p in batch],
            return_exceptions=True,
        )

        new_pages = []
        for p, res in zip(batch, results):
            if isinstance(res, Exception):
                print(f"    page {p} FAILED: {res}")
                continue
            _save_models(env, res)
            new_pages.append(p)
            print(f"    page {p}: {len(res)} models")

        pages_done.extend(new_pages)
        _save_pages_done(env, pages_done)

        total = len(_all_models(env))
        print(f"  total known: {total}")

        # If no results from any page in this batch, assume we hit the end
        if all(isinstance(r, Exception) or len(r) == 0 for r in results):
            print("  no more models found, stopping list crawl")
            break

        page += LIST_CONCURRENCY

    return total


async def _crawl_details(env: lmdb.Environment, context) -> None:
    """Crawl detail pages for models that don't have detail data yet."""
    models = _sorted_models(env)[:MAX_MODELS]
    to_crawl = [m for m in models if not m.get("downloads") or m["downloads"] == "N/A"]

    if not to_crawl:
        print("All models already have detail data, skipping.")
        return

    print(f"Crawling details for {len(to_crawl)} models (concurrency={DETAIL_CONCURRENCY}) …")
    sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async def _one(m: dict) -> dict:
        async with sem:
            try:
                detail = await _scrape_detail(context, m["url"])
                m.update(detail)
                _save_models(env, [m])
            except Exception as e:
                print(f"    FAIL {m['path']}: {e}")
            return m

    tasks = [_one(m) for m in to_crawl]
    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        await coro
        if i % 20 == 0:
            print(f"  detail progress: {i}/{len(to_crawl)}")
    print(f"  detail done: {len(to_crawl)}")


# ── Entry point ───────────────────────────────────────────────────────

async def main():
    env = _init_db()
    stop = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows

    async with create_playwright_context(profile_path="/home/guozr/.mozilla/firefox/2pydknyh.default-release", headless=False) as context:
        if not stop.is_set():
            print("=== Phase 1: crawling list pages ===")
            n = await _crawl_list_pages(env, context)
            print(f"  total models collected: {n}")

        if not stop.is_set():
            print("\n=== Phase 2: crawling model details ===")
            await _crawl_details(env, context)

    print("\n=== Results ===")
    top = _sorted_models(env)[:MAX_MODELS]
    print(f"Total in DB: {len(_all_models(env))}")
    print(f"Top {len(top)} by downloads:")

    for i, m in enumerate(top, 1):
        print(f"  {i:4d}. {m['path']:<50} {m.get('downloads','?'):>10}")

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        fields = ["path", "name", "modelType", "author", "likes", "downloads", "publishTime", "url"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows({k: m.get(k, "N/A") for k in fields} for m in top)

    print(f"\nSaved CSV: {CSV_PATH}")
    env.close()


if __name__ == "__main__":
    asyncio.run(main())
