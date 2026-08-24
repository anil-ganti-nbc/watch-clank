"""Live integration probe for the Tissot sitemap collector (EXPERIMENTAL lane).

Run manually: python -m scripts.probe_tissot_live
Not part of CI. Read-only against tissotwatches.com; writes nothing.
"""
from app.collectors.tissot_sitemap import TissotSitemapCollector


def main() -> None:
    c = TissotSitemapCollector()
    result = c.run(max_items=20)
    print("component_status:", result.metadata.get("component_status"))
    print("candidate_count:", result.metadata.get("candidate_count"))
    print("discovered_count:", result.metadata.get("discovered_count"))
    skus = [f for f in result.fetched if f.success][:10]
    print("sample SKUs discovered:")
    for f in skus:
        print(" ", f.url.rsplit("/", 1)[-1])


if __name__ == "__main__":
    main()
