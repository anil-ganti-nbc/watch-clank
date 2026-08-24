"""Tissot sitemap collector probe v2 — cross-locale SKU presence.

Demonstrates the Regional Presence Collector pattern on real data:
one locale sitemap -> SKU set; compare locales to see regional deltas.
"""
import re
import urllib.request

SKU_RE = re.compile(r"tissotwatches\.com/[a-z]{2}-[a-z]{2}/([A-Za-z0-9.]+)\.html")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def skus_for_locale(locale):
    xml = fetch(f"https://www.tissotwatches.com/{locale}/sitemap_0.xml")
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    out = set()
    for loc in locs:
        m = SKU_RE.search(loc)
        if m:
            out.add(m.group(1))
    return out


us = skus_for_locale("en-us")
ca = skus_for_locale("en-ca") if False else None
print(f"en-us SKUs: {len(us)}")

# second locale for the delta demo
xml_ca = fetch("https://www.tissotwatches.com/fr-ca/sitemap_0.xml")
locs_ca = re.findall(r"<loc>([^<]+)</loc>", xml_ca)
ca = {SKU_RE.search(item_loc).group(1) for item_loc in locs_ca if SKU_RE.search(item_loc)}
print(f"fr-ca SKUs: {len(ca)}")

only_us = us - ca
only_ca = ca - us
print(f"in US but not CA: {len(only_us)}  (sample: {sorted(only_us)[:5]})")
print(f"in CA but not US: {len(only_ca)}  (sample: {sorted(only_ca)[:5]})")
