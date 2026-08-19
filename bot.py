import os
import re
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

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


def supabase_headers():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}


def supabase_get(query):
    r = session.get(f"{SUPABASE_URL}/rest/v1/{query}", headers=supabase_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def supabase_upsert(product):
    r = session.post(
        f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_url",
        headers={**supabase_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
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
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def prices_from_text(text):
    vals = []
    for m in re.findall(r"(?:₺\s*)?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)", text, re.I):
        p = clean_price(m)
        if p is not None and p > 0:
            vals.append(p)
    return vals


def jsonld_products(soup, page_url):
    found = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        queue = list(items)
        while queue:
            item = queue.pop(0)
            if not isinstance(item, dict):
                continue
            if item.get("@graph"):
                queue.extend(item["@graph"])
            typ = item.get("@type")
            if typ == "Product" or (isinstance(typ, list) and "Product" in typ):
                name = item.get("name")
                offers = item.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    price = clean_price(offers.get("price"))
                    currency = offers.get("priceCurrency", "TRY")
                    url = offers.get("url") or item.get("url") or page_url
                else:
                    price, currency, url = None, "TRY", item.get("url") or page_url
                if name and price and currency in ("TRY", "TL", "YTL"):
                    found.append({"name": name.strip(), "price": price, "url": urljoin(page_url, url)})
    return found


def likely_product_url(site, path):
    p = path.lower()
    if site == "Amazon":
        return "/dp/" in p or "/gp/product/" in p
    if site == "Hepsiburada":
        return "-p-" in p
    if site == "Trendyol":
        return "/urun/" in p
    if site == "Pazarama":
        return "/" in p and len(p.strip("/")) > 10
    return False


def card_product(site, a, base_url):
    href = urljoin(base_url, a.get("href", ""))
    parsed = urlparse(href)
    if not likely_product_url(site, parsed.path):
        return None
    text = a.get_text(" ", strip=True)
    if len(text) < 10:
        return None
    node = a
    best = text
    for _ in range(4):
        node = node.parent
        if not node:
            break
        t = node.get_text(" ", strip=True)
        if len(t) > len(best) and len(t) < 1800:
            best = t
    prices = prices_from_text(best)
    if not prices:
        return None
    current = min(prices)
    previous = max(prices) if len(prices) > 1 else None
    if previous is not None and previous <= current:
        previous = None
    name = re.sub(r"\s+", " ", text).strip()
    name = re.sub(r"(?:Sepete Ekle|Hızlı Bakış|Kargo Bedava)$", "", name).strip()
    return {"name": name[:300], "price": current, "previous_display_price": previous, "url": href, "site": site}


def discover(site, seed_url):
    try:
        r = session.get(seed_url, timeout=30, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        print(f"Sayfa HTTP: {r.status_code} | {len(r.text)} karakter")
    except Exception as e:
        print(f"{site} seed okunamadı: {e}")
        return []

    results = []
    seen = set()

    # Önce standart Product JSON-LD.
    for p in jsonld_products(soup, r.url):
        p["site"] = site
        if p["url"] not in seen:
            seen.add(p["url"])
            results.append(p)
            if len(results) >= MAX_PRODUCTS_PER_SITE:
                return results

    # Sonra ürün kartlarının görünen HTML metnini kullan.
    for a in soup.find_all("a", href=True):
        p = card_product(site, a, r.url)
        if not p or p["url"] in seen:
            continue
        seen.add(p["url"])
        results.append(p)
        if len(results) >= MAX_PRODUCTS_PER_SITE:
            break

    return results


def get_existing(url):
    encoded = requests.utils.quote(url, safe="")
    return supabase_get(f"products?select=*&product_url=eq.{encoded}")[0:1]


def send_telegram(text):
    r = session.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHANNEL_ID, "text": text, "disable_web_page_preview": False},
        timeout=20,
    )
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
            text = (
                "🔥 CİDDİ İNDİRİM!\n\n"
                f"🛒 {product['name']}\n\n"
                f"💰 Önceki fiyat: {display_old:,.2f} TL\n"
                f"🔥 Yeni fiyat: {current:,.2f} TL\n"
                f"📉 İndirim: %{display_discount:.1f}\n"
                f"🏪 {product['site']}\n\n"
                f"🔗 {url}"
            )
            send_telegram(text)
        supabase_upsert({
            "product_url": url,
            "product_name": product["name"],
            "site": product["site"],
            "current_price": current,
            "previous_price": current,
            "lowest_price": current,
            "last_posted_price": current if should_post else None,
            "last_posted_at": last_posted,
            "last_seen_at": now.isoformat(),
            "updated_at": now.isoformat(),
        })
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
        text = (
            "🔥 YENİ DÜŞÜŞ!\n\n"
            f"🛒 {product['name']}\n\n"
            f"💰 Önceki fiyat: {previous:,.2f} TL\n"
            f"🔥 Yeni fiyat: {current:,.2f} TL\n"
            f"📉 Ekstra düşüş: %{discount:.1f}\n"
            f"🏪 {product['site']}\n\n"
            f"🔗 {url}"
        )
        send_telegram(text)
        last_posted = now.isoformat()

    supabase_upsert({
        "product_url": url,
        "product_name": product["name"],
        "site": product["site"],
        "current_price": current,
        "previous_price": previous,
        "lowest_price": min(lowest, current),
        "last_posted_price": current if should_post else existing.get("last_posted_price"),
        "last_posted_at": last_posted,
        "last_seen_at": now.isoformat(),
        "updated_at": now.isoformat(),
    })
    print(f"Kontrol: {product['site']} | {previous:.2f} -> {current:.2f} TL | %{discount:.1f} | paylaş={should_post}")


def main():
    total = 0
    for site, seed in SEEDS.items():
        print(f"\n=== {site} ===")
        products = discover(site, seed)
        print(f"Bulunan ürün: {len(products)}")
        for product in products:
            try:
                process(product)
                total += 1
            except Exception as e:
                print(f"Ürün işlenemedi: {product.get('url')} -> {e}")
    print(f"\nToplam işlenen ürün: {total}")


if __name__ == "__main__":
    main()
