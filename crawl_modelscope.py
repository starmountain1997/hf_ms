"""Crawl ModelScope top models using g-playwright + JS DOM extraction."""

import asyncio
import csv
import re
from pathlib import Path

from g_playwright.firefox import create_playwright_context, human_delay

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

            let card = link.closest('[class*="card"], [class*="Card"], [class*="item"], [class*="Item"], li, article');
            if (!card) card = link.parentElement;

            const innerText = (card.innerText || '').trim();

            models.push({
                path: path,
                name: path.split('/').pop(),
                url: 'https://modelscope.cn/models/' + path,
                innerText: innerText.substring(0, 800)
            });
        }
        return models;
    }""")

    await page.close()
    return models_data


def parse_model_card(text: str) -> dict:
    """Parse a model card's innerText into structured fields.

    Model card structure (each line is a field):
      Line 1:  Title
      Line 2:  Task type
      Line 3:  Framework (PyTorch, ONNX, etc.) or size for some cards
      Line 4:  License (or framework if line 3 is something else)
      ...middle lines: Tags (varies)
      Last-3:  Author/Org
      Last-2:  Date (YYYY.MM.DD)
      Last-1:  Size (e.g., "242.9m")
      Last-0:  Downloads (e.g., "221")
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    result = {
        "title": "N/A",
        "task": "N/A",
        "framework": "N/A",
        "license": "N/A",
        "author": "N/A",
        "date": "N/A",
        "size": "N/A",
        "downloads": "N/A",
    }

    if not lines:
        return result

    result["title"] = lines[0]

    # Task: line 2 is usually the task
    task_keywords = [
        "语音识别",
        "语音合成",
        "说话人确认",
        "说话人日志",
        "人脸检测",
        "人脸识别",
        "文字检测",
        "文字识别",
        "图像分类",
        "图像生成",
        "文本生成",
        "情绪识别",
        "情感识别",
        "抠图",
        "目标检测",
        "NLP",
        "OCR",
        "标点预测",
        "标点恢复",
        "地址解析",
        "语义分割",
        "实例分割",
        "机器翻译",
        "文本分类",
        "命名实体识别",
        "语音端点检测",
        "逆文本正则化",
        "困惑度计算",
        "语音语种识别",
        "音频分类",
        "音频生成",
        "语音降噪",
        "回声消除",
        "语音分离",
        "视觉检测跟踪",
        "光学字符识别",
        "人脸人体",
        "视觉分类",
        "视觉编辑",
        "视觉分割",
        "视觉生成",
        "视觉表征",
        "视觉评价",
        "底层视觉",
        "三维视觉",
        "蛋白质结构生成",
        "蛋白质功能预测",
        "视觉多模态理解",
        "多模态",
        "统一多模态",
        "视觉问答",
        "视频问答",
        "图文检索",
        "文档理解",
        "文本生成图片",
        "文本生成视频",
        "图片生成视频",
        "分词",
        "翻译",
        "文本摘要",
        "句子相似度",
        "预训练",
        "文本纠错",
        "文本向量",
        "特征抽取",
        "关系抽取",
        "零样本分类",
        "表格问答",
        "问答",
        "词性标注",
        "实体分类",
        "序列标注",
        "任务型对话",
        "语音唤醒",
        "时间戳预测",
        "图片生成图片",
    ]
    for line in lines[1:]:
        for kw in task_keywords:
            if line == kw or (kw in line and len(line) < 30):
                result["task"] = line
                break
        if result["task"] != "N/A":
            break

    # Framework: look for known framework names
    known_frameworks = {
        "PyTorch",
        "TensorFlow",
        "ONNX",
        "Safetensors",
        "MindSpore",
        "JAX",
        "PaddlePaddle",
        "c++",
        "C++",
    }
    for line in lines[2:]:
        fw = line.strip()
        if fw in known_frameworks or ("Safetensors" in fw and "框架" in fw):
            result["framework"] = line
            break

    # License: contains "License"
    for line in lines:
        if "License" in line:
            result["license"] = line
            break

    # Date: YYYY.MM.DD pattern
    date_re = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$")
    for line in lines:
        if date_re.match(line):
            result["date"] = line
            break

    # Size: number+unit pattern (e.g., "242.9m", "1.55B")
    size_re = re.compile(r"^([\d,.]+)\s*([kmgbt])$", re.IGNORECASE)
    for line in lines:
        if size_re.match(line.strip()):
            result["size"] = line.strip().upper()
            break

    # Downloads: last numeric-only value (not size, not date year)
    # Downloads is typically the very last line of the card
    # It's a standalone integer, possibly with commas
    num_re = re.compile(r"^(\d[\d,]*)$")
    for line in reversed(lines):
        m = num_re.match(line.strip())
        if m:
            val = m.group(1).replace(",", "")
            # Check it's not a year (2023-2026) or likely date component
            if 2020 <= int(val) <= 2030:
                continue
            result["downloads"] = val
            break

    # Author: look for known orgs and the line before date
    known_authors = [
        "通义实验室",
        "OpenDataLab",
        "孤鸿",
        "小橙鱼",
        "thuduj",
        "pengzhendong",
    ]
    for line in lines:
        for author in known_authors:
            if author in line:
                result["author"] = line
                break
        if result["author"] != "N/A":
            break

    # If no author found by keyword, try positional: line before date
    if result["author"] == "N/A" and result["date"] != "N/A":
        date_idx = None
        for i, line in enumerate(lines):
            if line == result["date"]:
                date_idx = i
                break
        if date_idx is not None and date_idx > 0:
            candidate = lines[date_idx - 1]
            # Skip if it looks like a tag (single lowercase word)
            if not re.match(r"^[a-z_]+$", candidate):
                result["author"] = candidate

    return result


async def main():
    all_models = []

    async with create_playwright_context(headless=True, no_profile=True) as context:
        for url in (PAGE1, PAGE2):
            print(f"Crawling: {url}")
            models = await scrape_page(context, url)
            all_models.extend(models)
            print(f"  Got {len(models)} models")

    # Parse each model's text
    for m in all_models:
        parsed = parse_model_card(m["innerText"])
        m.update(parsed)

    # Remove duplicates by path
    seen = set()
    unique = []
    for m in all_models:
        if m["path"] not in seen:
            seen.add(m["path"])
            unique.append(m)
    all_models = unique

    # Print results
    print("\n" + "=" * 130)
    print("# Top 50 Models on ModelScope (by Downloads)")
    print("=" * 130)
    header = f"{'#':<4} {'Title':<60} {'Downloads':>10} {'Size':>10} {'Author':<20} {'Task':<18}"
    print(header)
    print("-" * 130)

    for i, m in enumerate(all_models[:50], 1):
        title = m["title"][:57] + "..." if len(m["title"]) > 60 else m["title"]
        print(
            f"{i:<4} {title:<60} {m['downloads']:>10} {m['size']:>10} {m['author']:<20} {m['task']:<18}"
        )

    print("\n\n" + "=" * 130)
    print("--- Detailed Model Info ---")
    print("=" * 130)
    for i, m in enumerate(all_models[:50], 1):
        print(f"\n{i:02d}. {m['title']}")
        print(f"    Path:      {m['path']}")
        print(f"    Downloads: {m['downloads']}")
        print(f"    Size:      {m['size']}")
        print(f"    Author:    {m['author']}")
        print(f"    Task:      {m['task']}")
        print(f"    Framework: {m['framework']}")
        print(f"    License:   {m['license']}")
        print(f"    Date:      {m['date']}")
        print(f"    URL:       {m['url']}")

    print(f"\n\nTotal models scraped: {len(all_models)}")

    # Save CSV
    csv_fields = [
        "title",
        "path",
        "downloads",
        "size",
        "author",
        "task",
        "framework",
        "license",
        "date",
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
