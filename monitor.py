"""Alert when new products appear on a Square Online shop page.

First run saves the current products without alerting. Later runs alert only on
products not seen before. Alerts go out by email only. Gmail login comes from the
SMTP_USER and SMTP_PASS environment variables; recipients come from EMAIL_TO
(comma-separated).
"""
import json, os, smtplib, sys, urllib.parse
from email.message import EmailMessage
from pathlib import Path
from playwright.sync_api import sync_playwright

SHOP_URL = os.getenv("SHOP_URL", "https://allthinganime.square.site/s/shop")
SEEN_FILE = Path(os.getenv("SEEN_FILE", "seen.json"))
RECIPIENTS = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]


def fetch_products():
    """Return {product_url: name} for every product link found on the page."""
    products = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        try:
            # Don't wait for "networkidle": store pages never go fully quiet.
            page.goto(SHOP_URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_selector('a[href*="/product/"]', timeout=60000)
        except Exception as exc:
            print(f"Page did not show products: {exc}", file=sys.stderr)
            page.screenshot(path="debug.png", full_page=True)
            Path("debug.html").write_text(page.content())
            print("Page title:", page.title(), file=sys.stderr)
            browser.close()
            return {}
        # Scroll to trigger lazy loading / "load more" style listings.
        last = -1
        for _ in range(15):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1200)
            count = page.locator('a[href*="/product/"]').count()
            if count == last:
                break
            last = count
        for a in page.locator('a[href*="/product/"]').all():
            href = a.get_attribute("href")
            if not href:
                continue
            url = urllib.parse.urljoin(SHOP_URL, href.split("?")[0])
            name = (a.get_attribute("aria-label") or a.inner_text() or "").strip()
            name = " ".join(name.split()) or url.rsplit("/", 2)[-2].replace("-", " ")
            # Keep the longest name seen for a URL (image links have no text).
            if len(name) > len(products.get(url, "")):
                products[url] = name
        browser.close()
    return products


def send_email(subject, body):
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user, pwd = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
    if not (user and pwd and RECIPIENTS):
        print("SMTP_USER / SMTP_PASS / EMAIL_TO not set; skipping email.", file=sys.stderr)
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, user
    msg["To"] = ", ".join(RECIPIENTS)
    msg.set_content(body)
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)


def main():
    current = fetch_products()
    if not current:
        print("No products found; page layout may have changed. Not updating.", file=sys.stderr)
        sys.exit(1)

    first_run = not SEEN_FILE.exists()
    seen = {} if first_run else json.loads(SEEN_FILE.read_text())
    new = {u: n for u, n in current.items() if u not in seen}

    if new and not first_run:
        lines = [f"- {n}\n  {u}" for u, n in new.items()]
        subject = f"New product drop: {len(new)} new item(s)"
        body = "New on the shop:\n\n" + "\n".join(lines)
        send_email(subject, body)
        print(body)
    else:
        print("First run: saved baseline." if first_run else "No new products.")

    # Keep everything ever seen, so a product that sells out and returns won't re-alert.
    seen.update(current)
    SEEN_FILE.write_text(json.dumps(seen, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
