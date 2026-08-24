"""Tissot sitemap collector probe — validates the reusable SitemapDeltaCollector design.

Fetches the en-us product sitemap, extracts SKUs from URLs, and reports:
  - total product URLs
  - distinct SKUs (reference identity = URL slug, no page fetch needed)
  - cross-locale SKU overlap (regional presence evidence)
"""
import re
import urllib.request

SITEMAP_URL = "https://www.tissotwatches.com/en-us/sitemap_0.xml"
SKU_RE = re.compile(r"tissotwatches\.com/[a-z]{2}-[a-z]{2}/([A-Za-z0-9.]+)\.html")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


xml = fetch(SITEMAP_URL)
locs = re.findall(r"<loc>([^<]+)</loc>", xml)
skus = set()
for loc in locs:
    m = SKU_RE.search(loc)
    if m:
        skus.add(m.group(1))

print(f"product URLs: {len(locs)}")
print(f"distinct SKUs: {len(skus)}")
print("sample SKUs:", sorted(skus)[:10])
