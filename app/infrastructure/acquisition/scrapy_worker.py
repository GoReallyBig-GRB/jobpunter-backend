"""One-shot Scrapy worker so each crawl owns a fresh Twisted reactor."""
import json
import sys

def main():
    # Worker implementation is intentionally isolated from the long-lived API process.
    from app.infrastructure.acquisition.scrapy_worker_runtime import crawl
    try:
        config = json.load(sys.stdin)
        print(json.dumps({"records": crawl(config)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print("Scrapy worker failed: " + type(exc).__name__, file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
