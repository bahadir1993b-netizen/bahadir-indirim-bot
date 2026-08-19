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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}

# İlk sürümde fırsat/indirim sayfalarından ürün keşfediyoruz.
SEEDS = {
    "Amazon": "https://www.amazon.com.tr/gp/goldbox",
    "Hepsiburada": "https://www.hepsiburada.com/kampanyalar",
    "Trendyol": "https://www.trendyol.com/",
    "Pazarama": "https://www.pazarama.com/son-30-gunun-en-dusuk-fiyatli-urunleri-k-VRTCTGRY",
}

MIN_DISCOUNT = 40.0
REPOST_COOLDOWN_HOURS = 12
MAX_PRODUCTS_PER_SITE = 8

session = requests.Session()
session.headers.update(HEADERS)


def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def supabase_get(url):
    r = session.get(f"{SUPABASE_URL}/rest/v1/{url}", headers=supabase_headers(), timeout=20)
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


def jsonld_products(soup, page_url):
    found = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@graph"):
                items += item["@graph"]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "Product" or "Product" in (item.get("@type") or []):
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


def product_links(soup, base_url):
    host = urlparse(base_url).netloc
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        parsed = urlparse(href)
        if parsed.netloc != host:
            continue
        if href in seen:
            continue
        text = a.get_text(" ", strip=True)
        if len(text) < 15:
            continue
        # Ürün bağlantılarında genellikle bu izler bulunur; kampanya/menü linklerini ele.
        bad = ("/kampanyalar", "/butik", "/magaza", "/kategori", "/blog", "/yardim", "/hesabim")
        if any(x in parsed.path.lower() for x in bad):
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= MAX_PRODUCTS_PER_SITE * 2:
            break
    return links


def fetch_product(site, url):
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        products = jsonld_products(soup, url)
        if products:
            p = products[0]
            p["site"] = site
            return p
    except Exception as e:
        print(f"{site} ürün okunamadı: {url} -> {e}")
    return None


def discover(site, seed_url):
    try:
        r = session.get(seed_url, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"{site} seed okunamadı: {e}")
        return []

    # Bazı sayfalar doğrudan Product JSON-LD içeriyor.
    direct = jsonld_products(soup, seed_url)
    if direct:
        for p in direct:
            p["site"] = site
        return direct[:MAX_PRODUCTS_PER_SITE]

    results = []
    for url in product_links(soup, seed_url):
        p = fetch_product(site, url)
        if p:
            results.append(p)
        if len(results) >= MAX_PRODUCTS_PER_SITE:
            break
    return results


def get_existing(url):
    rows = supabase_get(f"products?select=*&product_url=eq.{requests.utils.quote(url, safe='')}")
    return rows[0] if rows else None


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
    existing = get_existing(url)
    now = datetime.now(timezone.utc)

    if not existing:
        supabase_upsert({
            "product_url": url,
            "product_name": product["name"],
            "site": product["site"],
            "current_price": current,
            "previous_price": current,
            "lowest_price": current,
            "last_seen_at": now.isoformat(),
            "updated_at": now.isoformat(),
        })
        print(f"İlk kayıt: {product['site']} | {product['name']} | {current:.2f} TL")
        return

    previous = float(existing.get("current_price") or existing.get("previous_price") or current)
    lowest = float(existing.get("lowest_price") or previous)
    last_posted = existing.get("last_posted_at")
    cooldown_ok = True
    if last_posted:
        try:
            posted_dt = datetime.fromisoformat(last_posted.replace("Z", "+00:00"))
            cooldown_ok = now - posted_dt >= timedelta(hours=REPOST_COOLDOWN_HOURS)
        except Exception:
            pass

    # Botun kendi fiyat geçmişine göre gerçek düşüş.
    discount = ((previous - current) / previous * 100) if previous > 0 else 0
    new_low = current < lowest

    should_post = discount >= MIN_DISCOUNT and cooldown_ok

    # Aynı fiyat tekrar geldiyse paylaşma. Daha düşük fiyat geldiğinde tekrar paylaşabilsin.
    if current >= previous:
        should_post = False

    if should_post:
        label = "🔥 YENİ DÜŞÜŞ!" if new_low else "🔥 CİDDİ İNDİRİM!"
        text = (
            f"{label}\n\n"
            f"🛒 {product['name']}\n\n"
            f"💰 Önceki fiyat: {previous:,.2f} TL\n"
            f"🔥 Yeni fiyat: {current:,.2f} TL\n"
            f"📉 İndirim: %{discount:.1f}\n"
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
    print(f"Kontrol: {product['site']} | {current:.2f} TL | %{discount:.1f} | paylaş={should_post}")


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
