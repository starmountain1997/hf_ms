"""Crawl ModelScope top models using g-playwright + JS DOM extraction."""

import asyncio
import csv
from pathlib import Path

from g_playwright.firefox import create_playwright_context, find_firefox_profile, human_delay

CSV_PATH = Path(__file__).parent / "modelscope_top50.csv"

PAGE1 = "https://modelscope.cn/models?page=1&sort=downloads"
PAGE2 = "https://modelscope.cn/models?page=2&sort=downloads"


async def scrape_page(context, url: str) -> list[dict]:
    """Scrape one page of ModelScope models."""
    page = await context.new_page()
    await page.set_viewport_size({"width": 1920, "height": 1080})
    await page.goto(url, wait_until="networkidle")

    for _ in range(6):
        await page.mouse.wheel(0, 3000)
        await asyncio.sleep(human_delay(0.8, 1.5))

    await page.wait_for_timeout(2000)

    models_data = await page.evaluate("""() => {
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
                path: path,
                name: path.split('/').pop(),
                url: 'https://modelscope.cn/models/' + path,
            });
        }
        return models;
    }""")

    await page.close()
    return models_data


async def scrape_model_detail(context, url: str) -> dict:
    """Navigate to a model detail page and extract info."""
    page = await context.new_page()
    await page.set_viewport_size({"width": 1920, "height": 1080})
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(3000)

    result = await page.evaluate("""() => {
        const text = (el) => el ? el.innerText.trim() : null;
        const num = (s) => {
            if (!s) return 'N/A';
            const m = s.replace(/[^\\d]/g, '');
            return m ? m : s;
        };

        // Model type — usually a tag/chip near the title
        const typeEl = document.querySelector('[class*="tag"], [class*="badge"], [class*="type"]');
        const modelType = text(typeEl) || 'N/A';

        // Author
        const authorEl = document.querySelector(
            'a[href*="/profile/"], a[href*="/user/"], [class*="author"], [class*="creator"], [class*="owner"]'
        );
        let author = 'N/A';
        if (authorEl) {
            author = text(authorEl) || authorEl.getAttribute('href')?.split('/').pop() || 'N/A';
        }

        // Likes
        let likes = 'N/A';
        const likeEls = document.querySelectorAll(
            '[class*="like"], [class*="star"], [class*="favorite"], [class*="collect"]'
        );
        for (const el of likeEls) {
            const t = text(el) || '';
            const digits = t.match(/\\d[\\d,]*/);
            if (digits) { likes = digits[0]; break; }
        }

        // Downloads
        let downloads = 'N/A';
        const dlEl = document.querySelector('[class*="download"]');
        if (dlEl) {
            const t = text(dlEl) || '';
            const digits = t.match(/\\d[\\d,]*/);
            if (digits) downloads = digits[0];
        }

        // Publish time / update time
        const timeEl = document.querySelector(
            '[class*="time"], [class*="date"], [class*="update"], time, [datetime]'
        );
        let publishTime = 'N/A';
        if (timeEl) {
            publishTime = timeEl.getAttribute('datetime') || text(timeEl) || 'N/A';
        }

        return {
            modelType,
            author,
            likes,
            downloads,
            publishTime,
        };
    }""")

    await page.close()
    return result


async def main():
    async with create_playwright_context(profile=find_firefox_profile(), headless=False) as context:
        all_models = []

        for url in (PAGE1, PAGE2):
            print(f"Crawling: {url}")
            models = await scrape_page(context, url)
            all_models.extend(models)
            print(f"  Got {len(models)} models")

        # Remove duplicates by path
        seen = set()
        unique = []
        for m in all_models:
            if m["path"] not in seen:
                seen.add(m["path"])
                unique.append(m)
        all_models = unique

        # Click into each model to get detail info
        for i, m in enumerate(all_models[:50], 1):
            print(f"Fetching detail {i}/50: {m['url']}")
            try:
                detail = await scrape_model_detail(context, m["url"])
                m.update(detail)
            except Exception as e:
                print(f"  Failed: {e}")

    # Print results
    print("\n" + "=" * 100)
    print("# Top 50 Models on ModelScope (by Downloads)")
    print("=" * 100)
    header = f"{'#':<4} {'Type':<16} {'Likes':>10} {'Downloads':>12} {'Author':<20} {'Published':<14} {'Path':<40}"
    print(header)
    print("-" * 130)

    for i, m in enumerate(all_models[:50], 1):
        print(
            f"{i:<4} {m['modelType']:<16} {m['likes']:>10} {m['downloads']:>12} {m['author']:<20} {m['publishTime']:<14} {m['path']:<40}"
        )

    print("\n\n" + "=" * 130)
    print("--- Detailed Model Info ---")
    print("=" * 130)
    for i, m in enumerate(all_models[:50], 1):
        print(f"\n{i:02d}. {m['path']}")
        print(f"    Type:        {m['modelType']}")
        print(f"    Author:      {m['author']}")
        print(f"    Likes:       {m['likes']}")
        print(f"    Downloads:   {m['downloads']}")
        print(f"    Published:   {m['publishTime']}")
        print(f"    URL:         {m['url']}")

    print(f"\n\nTotal models scraped: {len(all_models)}")

    # Save CSV
    csv_fields = [
        "path",
        "name",
        "modelType",
        "author",
        "likes",
        "downloads",
        "publishTime",
        "url",
    ]
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields)
        w.writeheader()
        for m in all_models[:50]:
            w.writerow({k: m.get(k, "N/A") for k in csv_fields})
    print(f"Saved CSV to {CSV_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
