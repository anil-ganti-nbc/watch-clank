"""Wave-1 brand infrastructure probe — structured, resumable.

Probes each candidate brand's official storefront for:
  - Shopify products.json (the proven cheap path)
  - sitemap.xml availability
  - platform fingerprints
Writes results to a JSON file for the source matrix.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave1_probe_results.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}

BRANDS = {
    "tissot": {
        "base": "https://www.tissotwatches.com",
        "products_json": "/products.json?limit=1",
        "sitemap": "/en-en/sitemap_index.xml",
        "notes": "SFCC multi-locale; SKU in URL path (T1374071104100.html); JSON-LD confirmed with sku+price+availability",
    },
    "hamilton": {
        "base": "https://www.hamiltonwatch.com",
        "products_json": "/products.json?limit=1",
        "sitemap": "/sitemap.xml",
        "notes": "edge flaky from this network; intermittent 200s then timeouts (rate limit?)",
    },
    "longines": {"base": "https://www.longines.com", "sitemap": "/robots.txt", "notes": "connection refused/reset from this network"},
    "bulova": {"base": "https://www.bulova.com", "sitemap": "/sitemap.xml", "notes": "SFCC (on/demandware.store paths); sitemap returns HTML error page"},
    "swatch": {"base": "https://www.swatch.com", "sitemap": "/sitemap.xml", "notes": "Access Denied edge page on some paths"},
    "orient_jp": {"base": "https://www.orient-watches.com", "products_json": "/en/products.json?limit=1", "notes": "WordPress-ish landing at /en; products.json 404"},
    "orient_usa": {"base": "https://www.orientwatchusa.com", "products_json": "/products.json?limit=1", "notes": "Cloudflare challenge on products.json (Shopify underneath? check /collections/all)"},
}


def probe(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        # Windows DNS via venv python can fail getaddrinfo; fall back to curl.
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                ct = resp.headers.get("content-type", "")
                body = resp.read(400).decode("utf-8", errors="ignore") if "json" in ct else ""
                return {"status": resp.status, "ct": ct[:40], "json_head": body[:200]}
        except urllib.error.HTTPError:
            raise
        except Exception:
            import subprocess

            out = subprocess.run(
                ["curl", "-sL", "--max-time", "15", "-A", UA["User-Agent"], "-w", "__CURL_STATUS__%{http_code}", url],
                capture_output=True, text=True, timeout=25,
            )
            if "__CURL_STATUS__" in out.stdout:
                body, _, status = out.stdout.rpartition("__CURL_STATUS__")
                return {"status": status, "head": body[:150]}
            return {"status": "?", "head": out.stdout[:150]}
    except urllib.error.HTTPError as e:
        return {"status": e.code}
    except Exception as e:
        return {"error": str(e)[:80]}


results = {}
for name, cfg in BRANDS.items():
    entry = dict(cfg)
    for key, path in (("products_json", "products_json"), ("sitemap", "sitemap")):
        if key in cfg:
            entry[f"{key}_result"] = probe(cfg["base"] + path)
            time.sleep(1)
    results[name] = entry
    print(name, "->", {k: v for k, v in entry.items() if k.endswith("_result")})

OUT.write_text(json.dumps(results, indent=1))
print("saved:", OUT)
