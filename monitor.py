#!/usr/bin/env python3
"""
Facebook Marketplace bike monitor.
Searches for specific bikes and sends SMS alerts via Twilio when new listings appear.
"""

import json
import os
import re
import smtplib
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText

from playwright.sync_api import sync_playwright

SEARCHES = [
    "Canyon Grizl small",
    "Cannondale Topstone small",
    "Trek Checkpoint 54",
    "Specialized Diverge 54",
    "Giant Revolt small",
]

SEEN_LISTINGS_FILE = "seen_listings.json"
LISTINGS_FILE = "listings.json"


def load_seen_listings() -> set:
    if os.path.exists(SEEN_LISTINGS_FILE):
        with open(SEEN_LISTINGS_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_listings(seen: set) -> None:
    with open(SEEN_LISTINGS_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)


def load_all_listings() -> list:
    if os.path.exists(LISTINGS_FILE):
        with open(LISTINGS_FILE) as f:
            return json.load(f)
    return []


def save_all_listings(listings: list) -> None:
    with open(LISTINGS_FILE, "w") as f:
        json.dump(listings, f, indent=2)


SMS_LIMIT = 10


def shorten_url(url: str) -> str:
    """Shorten via TinyURL's free API (no account needed).
    Falls back to the full URL if the request fails.
    """
    try:
        api = f"https://tinyurl.com/api-create.php?url={urllib.parse.quote(url)}"
        with urllib.request.urlopen(api, timeout=5) as resp:
            return resp.read().decode().strip()
    except Exception:
        return url


def send_all_sms(listings: list[dict]) -> None:
    """Send SMS for listings using a single reused SMTP connection.
    URLs are omitted — Telus's gateway silently drops messages containing them.
    """
    if not listings:
        return

    gmail_address = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_address = os.environ["SMS_RECIPIENT"]  # e.g. 6041234567@msg.telus.com

    sent = 0
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_address, app_password)
        for listing in listings:
            link = shorten_url(listing["url"])
            text = (
                f"New bike on FB Marketplace!\n"
                f"{listing['query']}\n"
                f"{listing['title'][:80]}\n"
                f"{link}"
            )
            msg = MIMEText(text)
            msg["From"] = gmail_address
            msg["To"] = to_address
            msg["Subject"] = ""
            try:
                server.sendmail(gmail_address, to_address, msg.as_string())
                print(f"  SMS sent: {listing['query']} – {listing['title'][:50]}")
                sent += 1
            except Exception as e:
                print(f"  SMS failed for {listing['id']}: {e}")
            time.sleep(1)

    print(f"SMS: {sent}/{len(listings)} sent.")


def title_matches_query(title: str, query: str) -> bool:
    """Require the first two words of the query (brand + model) to appear in the title.

    FB Marketplace returns many loosely related results; this filters out junk.
    Example: query 'Canyon Grizl small' → title must contain 'canyon' AND 'grizl'.
    """
    title_lower = title.lower()
    key_words = query.lower().split()[:2]  # brand + model
    return all(w in title_lower for w in key_words)


def search_marketplace(page, query: str) -> list[dict]:
    # Victoria BC latitude/longitude for 100km radius filter
    encoded = query.replace(" ", "%20")
    url = (
        f"https://www.facebook.com/marketplace/search/"
        f"?query={encoded}"
        f"&sortBy=creation_time_descend"
        f"&latitude=48.4284"
        f"&longitude=-123.3656"
        f"&radiusKm=100"
    )

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
    except Exception as e:
        print(f"  Error loading page for '{query}': {e}")
        return []

    listings = []
    seen_ids: set[str] = set()

    items = page.query_selector_all('a[href*="/marketplace/item/"]')
    for item in items:
        href = item.get_attribute("href") or ""
        match = re.search(r"/marketplace/item/(\d+)", href)
        if not match:
            continue

        item_id = match.group(1)
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        text = item.inner_text().strip().replace("\n", " ")
        if not title_matches_query(text, query):
            continue  # Skip listings that don't mention the brand + model

        full_url = f"https://www.facebook.com/marketplace/item/{item_id}/"
        listings.append({
            "id": item_id,
            "title": text[:120] if text else query,
            "url": full_url,
            "query": query,
        })

    print(f"  Found {len(listings)} matching listing(s) for '{query}'")
    return listings


def main() -> None:
    fb_cookies = json.loads(os.environ["FB_COOKIES"])
    seen = load_seen_listings()
    all_listings = load_all_listings()
    new_listings: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        context.add_cookies(fb_cookies)
        page = context.new_page()

        for query in SEARCHES:
            print(f"Searching: {query}")
            listings = search_marketplace(page, query)
            for listing in listings:
                if listing["id"] not in seen:
                    seen.add(listing["id"])
                    listing["found_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    new_listings.append(listing)
                    all_listings.append(listing)
            time.sleep(2)

        browser.close()

    # Save before sending SMS so listings are always persisted even if SMS fails
    save_seen_listings(seen)
    save_all_listings(all_listings)
    print(f"\nFound {len(new_listings)} new listing(s).")

    alerts = new_listings[:SMS_LIMIT]
    if len(new_listings) > SMS_LIMIT:
        print(f"Capping alerts at {SMS_LIMIT} (skipping {len(new_listings) - SMS_LIMIT}).")
    send_all_sms(alerts)


if __name__ == "__main__":
    main()
