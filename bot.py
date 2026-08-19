import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = "-1004424116637"
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SEEDS = {
    "Amazon": "https://www.amazon.com.tr/gp/goldbox",
    "Hepsiburada": "https://www.hepsiburada.com/kampanyalar",
    "Trendyol": "https://www.trendyol.com/butik-x-b175196",
    "Pazarama": "https://www.pazarama.com/son-30-gunun-en-dusuk-fiyatli-urunleri-k-VRTCTGRY",
}

MIN_DISCOUNT = 40.0
REPOST_COOLDOWN_HOURS = 12
MAX_PRODUCTS_PER_SITE = 12

session = requests.Session()
session.headers.update(HEADERS)


def supabase_headers(prefer=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
        "User-Agent": "bahadir-indirim-bot/1.0",
        "Accept": "application/json",
    }
    if SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    if prefer:
        headers["Prefer"] = prefer
    return headers


def supabase_get(query):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{query}", headers=supabase_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def supabase_upsert(product):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_url",
        headers=supabase_headers("resolution=merge-duplicates,return=representation"),
        json=product,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data[0] if data else product


def clean_price(value):
    if value is None:
        return None
    text = str(value).replace("TL", "").replace("₺", "").replace(" ", "")
    text = re.sub(r"[^0-9,.]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.rsplit(",", 1)
        if len(parts[1]) in (1, 2):
            text = parts[0].replace(".", "") + "." + parts[1]
        else:
            text = text.replace(",", "")
    elif "." in text:
        parts = text.rsplit(".", 1)
        if len(parts[1]) not in (1, 2):
            text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def prices_from_text(text):
    vals = []
    pattern = r"(?:₺\s*)?(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)"
    for match in re.finditer(pattern, text, re.I):
        start = match.start(1)
        prefix = text[max(0, start - 3):start]
        if "%" in prefix:
            continue
        p = clean_price(match.group(1))
        if p is not None and p > 0:
            vals.append(p)
    return vals


def labeled_price(text, labels):
    for label in labels:
        m = re.search(re.escape(label) + r"\s*(?:₺\s*)?(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)", text, re.I)
        if m:
            value = clean_price(m.group(1))
            if value is not None and value > 0:
                return value
    return None


def canonical_url(href):
    parsed = urlparse(href)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def likely_product_url(site, path):
    p = path.lower()
    if site == "Amazon":
        return "/dp/" in p or "/gp/product/" in p
    if site == "Hepsiburada":
        return "-p-" in p
    if site == "Trendyol":
        return "-p-" in p and len(p.strip("/")) > 10
    if site == "Pazarama":
        parts = [x for x in p.split("/") if x]
        return len(parts) >= 2 and any(ch.isdigit() for ch in p)
    return False


def card_product(site, a, base_url):
    href = canonical_url(urljoin(base_url, a.get("href", "")))
    parsed = urlparse(href)
    if not likely_product_url(site, parsed.path):
        return None
    text = a.get_text(" ", strip=True)
    if len(text) < 5:
        return None
    node = a
    best = text
    for _ in range(5):
        node = node.parent
        if not node:
            break
        t = node.get_text(" ", strip=True)
        if len(t) > len(best) and len(t) < 2200:
            best = t

    # Amazon kartlarında "Fırsatın Fiyatı" doğrudan ürünün güncel fiyatını,
    # "Önceki" ise referans fiyatını gösteriyor. Kartın tamamındaki en küçük
    # sayıyı seçmek güvenli değil; taksit/puan/başka ürün tutarları karışabiliyor.
    if site == "Amazon":
        current = labeled_price(best, ["Fırsatın Fiyatı:", "Fırsatın Fiyatı", "Teklif Fiyatı:", "Teklif Fiyatı"])
        previous = labeled_price(best, ["Önceki:", "Önceki Fiyat:", "Önceki fiyat:"])
        if current is None:
            # Etiket bulunamazsa ilk açık TL tutarına geri dön.
            prices = prices_from_text(best)
            current = prices[0] if prices else None
            other_prices = prices[1:] if prices else []
            previous = max(other_prices) if other_prices else None
        if current is None:
            return None
    else:
        prices = prices_from_text(best)
        if not prices:
            return None
        current = prices[0]
        other_prices = prices[1:]
        previous = max(other_prices) if other_prices else None

    if previous is not None and previous <= current:
        previous = None

    name = a.get("aria-label") or a.get("title") or text
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"(?:Sepete Ekle|Hızlı Bakış|Kargo Bedava)$", "", name).strip()
    if len(name) < 5:
        return None
    return {"name": name[:300], "price": current, "previous_display_price": previous, "url": href, "site": site}


def discover(site, seed_url, browser):
    context = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"], viewport={"width": 1440, "height": 1000}, extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]})
    page = context.new_page()
    try:
        print(f"Tarayıcı açılıyor: {seed_url}")
        response = page.goto(seed_url, wait_until="domcontentloaded", timeout=60000)
        status = response.status if response else "?"
        print(f"Sayfa HTTP: {status} | URL: {page.url}")
        page.wait_for_timeout(3500)
        for _ in range(3):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1200)
        page.mouse.wheel(0, -8000)
        page.wait_for_timeout(1000)
        body_text = page.locator("body").inner_text(timeout=10000)[:4000]
        lowered = body_text.lower()
        if any(x in lowered for x in ["access denied", "erişim engellendi", "robot", "captcha", "verify you are human"]):
            print("Uyarı: sayfada erişim/bot doğrulama metni görüldü.")
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        print(f"Tarayıcı HTML: {len(html)} karakter")
        results = []
        seen = set()
        for a in soup.find_all("a", href=True):
            p = card_product(site, a, page.url)
            if not p or p["url"] in seen:
                continue
            seen.add(p["url"])
            results.append(p)
            if len(results) >= MAX_PRODUCTS_PER_SITE:
                break
        return results
    except PlaywrightTimeoutError as e:
        print(f"{site} zaman aşımı: {e}")
        return []
    except Exception as e:
        print(f"{site} tarama hatası: {type(e).__name__}: {e}")
        return []
    finally:
        context.close()


def get_existing(url):
    encoded = requests.utils.quote(url, safe="")
    return supabase_get(f"products?select=*&product_url=eq.{encoded}")[0:1]


def send_telegram(text):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHANNEL_ID, "text": text, "disable_web_page_preview": False}, timeout=20)
    r.raise_for_status()
    print("Telegram gönderildi")


def process(product):
    url = product["url"]
    current = product["price"]
    rows = get_existing(url)
    existing = rows[0] if rows else None
    now = datetime.now(timezone.utc)
    if not existing:
        display_old = product.get("previous_display_price")
        display_discount = ((display_old - current) / display_old * 100) if display_old and display_old > current else 0
        should_post = display_discount >= MIN_DISCOUNT
        last_posted = now.isoformat() if should_post else None
        if should_post:
            text = f"🔥 CİDDİ İNDİRİM!\n\n🛒 {product['name']}\n\n💰 Önceki fiyat: {display_old:,.2f} TL\n🔥 Yeni fiyat: {current:,.2f} TL\n📉 İndirim: %{display_discount:.1f}\n🏪 {product['site']}\n\n🔗 {url}"
            send_telegram(text)
        supabase_upsert({"product_url": url, "product_name": product["name"], "site": product["site"], "current_price": current, "previous_price": current, "lowest_price": current, "last_posted_price": current if should_post else None, "last_posted_at": last_posted, "last_seen_at": now.isoformat(), "updated_at": now.isoformat()})
        print(f"İlk kayıt: {product['site']} | {current:.2f} TL | ekrandaki eski={display_old} | paylaş={should_post}")
        return
    previous = float(existing.get("current_price") or current)
    lowest = float(existing.get("lowest_price") or previous)
    last_posted = existing.get("last_posted_at")
    cooldown_ok = True
    if last_posted:
        try:
            cooldown_ok = now - datetime.fromisoformat(last_posted.replace("Z", "+00:00")) >= timedelta(hours=REPOST_COOLDOWN_HOURS)
        except Exception:
            pass
    discount = ((previous - current) / previous * 100) if previous > current else 0
    should_post = discount >= MIN_DISCOUNT and cooldown_ok and current < previous
    if should_post:
        text = f"🔥 YENİ DÜŞÜŞ!\n\n🛒 {product['name']}\n\n💰 Önceki fiyat: {previous:,.2f} TL\n🔥 Yeni fiyat: {current:,.2f} TL\n📉 Ekstra düşüş: %{discount:.1f}\n🏪 {product['site']}\n\n🔗 {url}"
        send_telegram(text)
        last_posted = now.isoformat()
    supabase_upsert({"product_url": url, "product_name": product["name"], "site": product["site"], "current_price": current, "previous_price": previous, "lowest_price": min(lowest, current), "last_posted_price": current if should_post else existing.get("last_posted_price"), "last_posted_at": last_posted, "last_seen_at": now.isoformat(), "updated_at": now.isoformat()})
    print(f"Kontrol: {product['site']} | {previous:.2f} -> {current:.2f} TL | %{discount:.1f} | paylaş={should_post}")


def main():
    total = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"])
        try:
            for site, seed in SEEDS.items():
                print(f"\n=== {site} ===")
                products = discover(site, seed, browser)
                print(f"Bulunan ürün: {len(products)}")
                for product in products:
                    try:
                        process(product)
                        total += 1
                    except Exception as e:
                        print(f"Ürün işlenemedi: {product.get('url')} -> {e}")
        finally:
            browser.close()
    print(f"\nToplam işlenen ürün: {total}")


if __name__ == "__main__":
    main()
