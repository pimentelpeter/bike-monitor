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


def load_seen_listings() -> set:
    if os.path.exists(SEEN_LISTINGS_FILE):
        with open(SEEN_LISTINGS_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_listings(seen: set) -> None:
    with open(SEEN_LISTINGS_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)


def send_sms(message: str) -> None:
    gmail_address = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_address = os.environ["SMS_RECIPIENT"]  # e.g. 5551234567@vtext.com

    msg = MIMEText(message)
    msg["From"] = gmail_address
    msg["To"] = to_address
    msg["Subject"] = ""  # Carriers often prepend subject, keep it blank

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_address, app_password)
        server.sendmail(gmail_address, to_address, msg.as_string())

    print(f"SMS sent: {message[:80]}...")


def search_marketplace(page, query: str) -> list[dict]:
    # Victoria BC latitude/longitude for 100km radius filter
    # FB Marketplace uses latitude, longitude, radius (in km) URL params
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
        full_url = f"https://www.facebook.com/marketplace/item/{item_id}/"
        listings.append({
            "id": item_id,
            "title": text[:120] if text else query,
            "url": full_url,
            "query": query,
        })

    print(f"  Found {len(listings)} listing(s) for '{query}'")
    return listings


def main() -> None:
    fb_cookies = json.loads(os.environ["FB_COOKIES"])
    seen = load_seen_listings()
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
                    new_listings.append(listing)
            time.sleep(2)

        browser.close()

    # Save before sending SMS so listings are always persisted even if SMS fails
    save_seen_listings(seen)
    print(f"\nFound {len(new_listings)} new listing(s).")

    for listing in new_listings:
        msg = (
            f"New bike on FB Marketplace!\n"
            f"Search: {listing['query']}\n"
            f"{listing['title'][:80]}\n"
            f"{listing['url']}"
        )
        try:
            send_sms(msg)
            time.sleep(1)  # Avoid Gmail rate limiting
        except Exception as e:
            print(f"  SMS failed for listing {listing['id']}: {e}")


if __name__ == "__main__":
    main()
