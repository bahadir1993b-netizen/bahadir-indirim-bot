import os
import re
from datetime import datetime, timezone, timedelta
from statistics import median
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
HISTORY_DAYS = 30
MIN_HISTORY_OBSERVATIONS = 3
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


def get_price_history(url):
    encoded = requests.utils.quote(url, safe="")
    since = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    query = (
        f"price_history?select=price,observed_at&product_url=eq.{encoded}"
        f"&observed_at=gte.{requests.utils.quote(since, safe='')}&order=observed_at.desc&limit=500"
    )
    return supabase_get(query)


def record_price_history(url, site, price, observed_at):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/price_history",
        headers=supabase_headers("return=minimal"),
        json={
            "product_url": url,
            "site": site,
            "price": price,
            "observed_at": observed_at,
        },
        timeout=20,
    )
    r.raise_for_status()


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

    if site == "Amazon":
        current = labeled_price(best, ["Fırsatın Fiyatı:", "Fırsatın Fiyatı", "Teklif Fiyatı:", "Teklif Fiyatı"])
        previous = labeled_price(best, ["Önceki:", "Önceki Fiyat:", "Önceki fiyat:"])
        if current is None:
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
    current = float(product["price"])
    now = datetime.now(timezone.utc)

    # Önce geçmişi oku; böylece Amazon'un "Önceki" alanındaki aylar önceki
    # yüksek fiyatı gerçek indirim kabul etmiyoruz.
    history = get_price_history(url)
    historical_prices = []
    for row in history:
        try:
            p = float(row.get("price"))
            if p > 0:
                historical_prices.append(p)
        except (TypeError, ValueError):
            pass

    reference_price = median(historical_prices) if len(historical_prices) >= MIN_HISTORY_OBSERVATIONS else None
    history_low = min(historical_prices) if historical_prices else None
    discount = ((reference_price - current) / reference_price * 100) if reference_price and current < reference_price else 0

    rows = get_existing(url)
    existing = rows[0] if rows else None
    last_posted_price = float(existing["last_posted_price"]) if existing and existing.get("last_posted_price") else None
    last_posted_at = existing.get("last_posted_at") if existing else None

    cooldown_ok = True
    if last_posted_at:
        try:
            cooldown_ok = now - datetime.fromisoformat(last_posted_at.replace("Z", "+00:00")) >= timedelta(hours=REPOST_COOLDOWN_HOURS)
        except Exception:
            pass

    # İlk gözlemde paylaşım yok. En az birkaç geçmiş gözlem oluştuktan sonra
    # son 30 günün tipik (medyan) fiyatına göre gerçek düşüş hesaplanır.
    should_post = (
        reference_price is not None
        and discount >= MIN_DISCOUNT
        and cooldown_ok
        and (last_posted_price is None or current < last_posted_price)
    )

    # Fiyat geçmişine bu taramayı ekle.
    try:
        record_price_history(url, product["site"], current, now.isoformat())
    except Exception as e:
        # Geçmiş tablosu yoksa/erişilemiyorsa güvenli tarafta kal: yanlış indirim paylaşma.
        print(f"Fiyat geçmişi kaydedilemedi: {type(e).__name__}: {e}")
        should_post = False

    lowest = min(float(existing.get("lowest_price") or current), current) if existing else current

    if should_post:
        ref_text = f"{reference_price:,.2f} TL"
        text = f"🔥 CİDDİ İNDİRİM!\n\n🛒 {product['name']}\n\n💰 Son 30 gün referans fiyatı: {ref_text}\n🔥 Yeni fiyat: {current:,.2f} TL\n📉 Gerçek indirim: %{discount:.1f}\n🏪 {product['site']}\n\n🔗 {url}"
        send_telegram(text)
        last_posted_price = current
        last_posted_at = now.isoformat()

    supabase_upsert({
        "product_url": url,
        "product_name": product["name"],
        "site": product["site"],
        "current_price": current,
        "previous_price": float(existing.get("current_price") or current) if existing else current,
        "lowest_price": lowest,
        "last_posted_price": last_posted_price,
        "last_posted_at": last_posted_at,
        "last_seen_at": now.isoformat(),
        "updated_at": now.isoformat(),
    })

    print(
        f"Kontrol: {product['site']} | {current:.2f} TL | "
        f"30g medyan={reference_price:.2f} TL" if reference_price else
        f"Kontrol: {product['site']} | {current:.2f} TL | 30g geçmiş yetersiz"
    )
    if history_low is not None:
        print(f"30g en düşük: {history_low:.2f} TL | gerçek indirim: %{discount:.1f} | paylaş={should_post}")
    else:
        print(f"Gerçek indirim: %{discount:.1f} | paylaş={should_post}")


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
