def scraper_run(collector_id: str, url: str) -> list[dict]:
    _ = (collector_id, url)
    return [{"name": "Widget A", "price": 49.0, "currency": "USD", "availability": "in_stock"}]


def scraper_heal(collector_id: str, prompt: str, url: str) -> dict:
    _ = (collector_id, prompt, url)
    return {
        "status": "awaiting_approval",
        "preview_result": [],
        "next_step": "bdata scraper approve <collector-id>",
    }


def scraper_approve(cmd: str) -> bool:
    _ = cmd
    return True


def scrape_markdown(url: str) -> str:
    _ = url
    return "# Pulse\n\nWidget A - $49.00"
