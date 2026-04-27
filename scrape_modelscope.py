from playwright.sync_api import sync_playwright
import re

url = "https://modelscope.cn/models?page=1&sort=downloads"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(url)
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(5000)

    # Scroll to load all content on page 1
    for _ in range(8):
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1500)

    # Get the page source and parse it
    html = page.content()

    # Look for download counts - pattern is typically at end of model text
    # Format appears to be: ...242.9m221 (size=242.9M, downloads=221)
    # or sometimes downloads are listed separately

    # Get all model links with their text content
    model_links = page.locator('a[href*="/models/"]').all()

    models = []
    for link in model_links:
        href = link.get_attribute('href')
        if href and href.startswith('/models/') and '/tags/' not in href:
            model_path = href.replace('/models/', '')

            # Skip if duplicate
            if any(m['path'] == model_path for m in models):
                continue

            text = link.text_content()
            text = ' '.join(text.split())

            # Parse the text to extract info
            # Pattern: Name ... Size ... Downloads
            # Examples: "242.9m221", "221" at end

            # Try to find download count - look for number at end or before common words
            download_match = re.search(r'(\d+)\s*$', text)
            downloads = download_match.group(1) if download_match else 'N/A'

            # Try to find model size
            size_match = re.search(r'(\d+\.?\d*)([kmg])', text)
            size = size_match.group(0) if size_match else 'N/A'

            # Extract author/org (usually appears at end with date)
            author_match = re.search(r'([一-龥a-zA-Z]+)\s*(\d{4}[./]\d{2}[./]\d{2})', text)
            author = author_match.group(1) if author_match else 'N/A'

            # Extract model type (usually first meaningful chunk)
            parts = text.split()
            name = parts[0] if parts else model_path

            # Get category/task type
            task_keywords = ['语音识别', '语音端点检测', '说话人确认', '标点', '人脸检测',
                           '文字检测', '文字识别', '图像', '文本生成', '情感识别',
                           '抠图', '目标检测', 'NLP', 'OCR', '地址', '填充', '地址']

            task = 'N/A'
            for kw in task_keywords:
                if kw in text:
                    task = kw
                    break

            models.append({
                'path': model_path,
                'name': name,
                'downloads': downloads,
                'size': size,
                'author': author,
                'task': task,
                'text': text[:150]
            })

    # Sort by downloads (descending) - but downloads field may not be numeric
    # Actually we know page 1 is already sorted by downloads

    print("# Top 50 Models on ModelScope (by Downloads)")
    print("=" * 100)
    print(f"{'#':<4} {'Model Name':<60} {'Downloads':<10} {'Size':<10} {'Author':<15}")
    print("-" * 100)

    for i, m in enumerate(models[:50], 1):
        # Extract just the model name part from path
        model_name = m['path'].split('/')[-1] if '/' in m['path'] else m['path']
        print(f"{i:<4} {model_name:<60} {m['downloads']:<10} {m['size']:<10} {m['author']:<15}")

    print("\n\n--- Full Model List with Details ---")
    for i, m in enumerate(models[:50], 1):
        print(f"\n{i}. {m['path']}")
        print(f"   Text: {m['text']}")

    browser.close()
