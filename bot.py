import os
import re
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

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
    "Hepsiburada": "https://www.hepsiburada.com/",
    "Trendyol": "https://www.trendyol.com/tr/butik-x-b175196",
    "Pazarama": "https://www.pazarama.com/son-30-gunun-en-dusuk-fiyatli-urunleri-k-VRTCTGRY",
}

MIN_DISCOUNT = 30.0
REPOST_COOLDOWN_HOURS = 12
MAX_PRODUCTS_PER_SITE = 12
PRICE_HISTORY_DAYS = 30
MIN_HISTORY_SAMPLES = 3

session = requests.Session()
session.headers.update(HEADERS)


def supabase_headers(prefer=None):
    h = {"apikey": SUPABASE_KEY, "Content-Type": "application/json", "User-Agent": "bahadir-indirim-bot/1.0", "Accept": "application/json"}
    if SUPABASE_KEY.startswith("eyJ"):
        h["Authorization"] = f"Bearer {SUPABASE_KEY}"
    if prefer:
        h["Prefer"] = prefer
    return h


def supabase_get(path, params=None):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=supabase_headers(), params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def supabase_upsert(product):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_url", headers=supabase_headers("resolution=merge-duplicates,return=representation"), json=product, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data[0] if data else product


def record_price(url, site, price, recorded_at):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/price_history", headers=supabase_headers("return=minimal"), json={"product_url": url, "site": site, "price": price, "recorded_at": recorded_at}, timeout=20)
    r.raise_for_status()


def get_price_history(url, days=PRICE_HISTORY_DAYS):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return supabase_get("price_history", params={"select": "price,recorded_at", "product_url": f"eq.{url}", "recorded_at": f"gte.{cutoff}", "order": "recorded_at.desc"})


def clean_price(value):
    if value is None:
        return None
    text = str(value).replace("TL", "").replace("₺", "").replace(" ", "")
    text = re.sub(r"[^0-9,.]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        a, b = text.rsplit(",", 1)
        text = a.replace(".", "") + "." + b if len(b) in (1, 2) else text.replace(",", "")
    elif "." in text:
        a, b = text.rsplit(".", 1)
        if len(b) not in (1, 2):
            text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def prices_from_text(text):
    vals = []
    pattern = r"(?:₺\s*)?(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)"
    for m in re.finditer(pattern, text, re.I):
        if "%" in text[max(0, m.start(1)-5):m.start(1)]:
            continue
        p = clean_price(m.group(1))
        if p and p > 0:
            vals.append(p)
    return vals


def labeled_price(text, labels):
    for label in labels:
        m = re.search(re.escape(label) + r"[^0-9]{0,50}(?:₺\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)", text, re.I)
        if m:
            p = clean_price(m.group(1))
            if p and p > 0:
                return p
    return None


def hepsiburada_prices(text):
    positive = ["Sepetteki Fiyat", "Sepette indirimli fiyat", "Sepette", "Peşin Fiyat", "Güncel Fiyat", "Satış Fiyatı", "Kampanyalı Fiyat"]
    negative = ["taksit", "puan", "worldpuan", "parapuan", "bonus", "parafpara", "chip-para", "kupon kazan", "kupon tutarı", "indirim tutarı", "kazanç", "hediye çek"]
    current = labeled_price(text, positive)
    if current is None:
        pattern = r"(?:₺\s*)?(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)"
        candidates = []
        for m in re.finditer(pattern, text, re.I):
            p = clean_price(m.group(1))
            if not p or p <= 0:
                continue
            ctx = text[max(0, m.start(1)-140):min(len(text), m.end()+100)].lower()
            score = 0
            if any(x in ctx for x in negative): score -= 30
            if any(x in ctx for x in ["fiyat", "satış", "sepette", "hemen al"]): score += 10
            if p < 50: score -= 12
            elif p < 100: score -= 5
            elif p >= 500: score += 3
            candidates.append((score, p))
        if candidates:
            candidates.sort(reverse=True)
            current = candidates[0][1]
    if current is None:
        return None, None
    previous = None
    for label in ["Eski Fiyat", "Önceki Fiyat", "Piyasa Fiyatı"]:
        p = labeled_price(text, [label])
        if p and p > current:
            previous = p
            break
    return current, previous


def extract_campaign(text, current):
    result = {"campaign_price": None, "coupon_code": None, "campaign_note": None}
    if not text or not current:
        return result

    for pattern in [
        r"(?:tanesi|adet fiyatı|birim fiyatı)\s*(?:₺\s*)?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)",
        r"(?:sepette|ödeme adımında|ödemede)\s*(?:₺\s*)?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)",
    ]:
        m = re.search(pattern, text, re.I)
        if m:
            p = clean_price(m.group(1))
            if p and p < current:
                result["campaign_price"] = p
                break

    if result["campaign_price"] is None:
        m = re.search(r"(\d+)\s*(?:al\s*)?(\d+)\s*öde", text, re.I)
        if m:
            buy_n, pay_n = int(m.group(1)), int(m.group(2))
            if buy_n > pay_n > 0:
                result["campaign_price"] = current * pay_n / buy_n

    if result["campaign_price"] is None:
        m = re.search(r"(\d{2,6})\s*/\s*(\d{1,4})\s*TL\s*(?:kupon|indirim)?", text, re.I)
        if m:
            threshold, discount = float(m.group(1)), float(m.group(2))
            if current >= threshold and 0 < discount < current:
                result["campaign_price"] = current - discount

    for pattern in [r"(?:kod|kupon kodu|kodu)\s*[:：]?\s*([A-Z0-9_-]{4,30})", r"\b([A-Z]{3,}[0-9]{1,4})\b\s*(?:kodu|ile)"]:
        m = re.search(pattern, text, re.I)
        if m:
            code = m.group(1).upper()
            if code not in {"FIRSAT", "KUPON", "TL"}:
                result["coupon_code"] = code
                break

    phrases = ["Prime Üyelere Özel", "Premium Üyelik", "Ücretsiz Kargo", "Mobil Uygulamada Geçerli", "2'li alımda", "3'lü alımda", "4 al 3 öde", "2 al 1 öde", "Çok Al Az Öde"]
    notes = [p for p in phrases if re.search(re.escape(p), text, re.I)]
    if result["coupon_code"]:
        notes.append(f"Kod: {result['coupon_code']}")
    if notes:
        result["campaign_note"] = " • ".join(dict.fromkeys(notes))
    return result


def canonical_url(href):
    p = urlparse(href)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


def likely_product_url(site, path):
    p = path.lower()
    if site == "Amazon": return "/dp/" in p or "/gp/product/" in p
    if site == "Hepsiburada": return "-p-" in p
    if site == "Trendyol": return "-p-" in p and len(p.strip("/")) > 10
    if site == "Pazarama": return bool(re.search(r"-p-\d+", p))
    return False


def card_product(site, a, base_url):
    href = canonical_url(urljoin(base_url, a.get("href", "")))
    if not likely_product_url(site, urlparse(href).path):
        return None
    text = a.get_text(" ", strip=True)
    if len(text) < 5:
        return None
    node, best = a, text
    for _ in range(5):
        node = node.parent
        if not node: break
        t = node.get_text(" ", strip=True)
        if len(t) > len(best) and len(t) < 2200: best = t

    if site == "Amazon":
        current = labeled_price(best, ["Fırsatın Fiyatı:", "Fırsatın Fiyatı", "Teklif Fiyatı:", "Teklif Fiyatı"])
        previous = labeled_price(best, ["Önceki:", "Önceki Fiyat:"])
        if current is None:
            prices = prices_from_text(best); current = prices[0] if prices else None; previous = max(prices[1:]) if len(prices) > 1 else None
    elif site == "Hepsiburada":
        current, previous = hepsiburada_prices(best)
    elif site == "Pazarama":
        prices = prices_from_text(best); current = prices[-1] if prices else None; previous = prices[0] if len(prices) > 1 and prices[0] > current else None
    else:
        prices = prices_from_text(best); current = prices[0] if prices else None; previous = max(prices[1:]) if len(prices) > 1 else None
    if current is None:
        return None
    if previous is not None and previous <= current:
        previous = None
    campaign = extract_campaign(best, current)
    name = a.get("aria-label") or a.get("title") or text
    name = re.sub(r"\s+", " ", name).strip()[:300]
    return {"name": name, "price": current, "previous_display_price": previous, "campaign_price": campaign["campaign_price"], "coupon_code": campaign["coupon_code"], "campaign_note": campaign["campaign_note"], "url": href, "site": site}


def hepsiburada_detail(page, product):
    try:
        response = page.goto(product["url"], wait_until="domcontentloaded", timeout=30000)
        if not response or response.status >= 400: return product
        page.wait_for_timeout(700)
        detail_current = None
        for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
            try: data = json.loads(raw)
            except Exception: continue
            items = data.get("@graph", []) if isinstance(data, dict) else data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict) or item.get("@type") != "Product": continue
                offers = item.get("offers") or {}
                if isinstance(offers, list): offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    detail_current = clean_price(offers.get("price"))
                    if detail_current and detail_current > 0:
                        product["price"] = detail_current
                        product["name"] = str(item.get("name") or product["name"])[:300]
                        break
            if detail_current: break
        text = page.locator("body").inner_text(timeout=10000)
        body_price, body_previous = hepsiburada_prices(text)
        if body_price and (product["price"] < 50 or abs(body_price-product["price"]) > max(100, product["price"]*0.25)):
            product["price"] = body_price
        if body_previous and body_previous > product["price"]:
            product["previous_display_price"] = body_previous
        c = extract_campaign(text, product["price"])
        for k in ["campaign_price", "coupon_code", "campaign_note"]:
            if c.get(k): product[k] = c[k]
    except Exception as e:
        print(f"HB detay uyarısı: {type(e).__name__}: {e}")
    return product


def trendyol_public_products(limit=MAX_PRODUCTS_PER_SITE):
    """Trendyol'un TR web sayfası ülke seçimine yönlendirilse bile public discovery servisinden liste al."""
    endpoint = "https://public.trendyol.com/discovery-web-searchgw-service/v2/api/infinite-scroll/sr"
    params = {
        "q": "indirim",
        "os": "1", "sk": "1", "pi": "1", "culture": "tr-TR", "userGenderId": "1", "pId": "0",
        "scoringAlgorithmId": "2", "categoryRelevancyEnabled": "false", "isLegalRequirementConfirmed": "false",
        "searchStrategyType": "DEFAULT", "productStampType": "TypeA", "fixSlotProductAdsIncluded": "true",
        "searchAbDecider": ",Suggestion_A,Relevancy_1,FilterRelevancy_1,Smartlisting_2,FlashSales_1,SuggestionBadges_A",
    }
    try:
        r = session.get(endpoint, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()
        products = (data.get("result") or {}).get("products") or []
        out, seen = [], set()
        for item in products:
            url = item.get("url") or item.get("productUrl")
            if not url:
                cid = item.get("id") or item.get("contentId")
                if cid: url = f"https://www.trendyol.com/urun-p-{cid}"
            if not url or url in seen: continue
            current = clean_price(item.get("price") or item.get("discountedPrice") or item.get("sellingPrice") or item.get("priceWithDiscount"))
            if not current: continue
            name = item.get("name") or item.get("title") or "Trendyol ürünü"
            previous = clean_price(item.get("originalPrice") or item.get("listPrice") or item.get("struckPrice"))
            campaign_text = json.dumps(item, ensure_ascii=False)
            c = extract_campaign(campaign_text, current)
            out.append({"name": str(name)[:300], "price": current, "previous_display_price": previous if previous and previous > current else None, "campaign_price": c["campaign_price"], "coupon_code": c["coupon_code"], "campaign_note": c["campaign_note"], "url": canonical_url(url), "site": "Trendyol"})
            seen.add(url)
            if len(out) >= limit: break
        print(f"Trendyol public servis: {len(out)} ürün bulundu")
        return out
    except Exception as e:
        print(f"Trendyol public servis uyarısı: {type(e).__name__}: {e}")
        return []


def discover(site, seed_url, browser):
    if site == "Trendyol":
        return trendyol_public_products()
    context = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"], viewport={"width": 1440, "height": 1000}, extra_http_headers=HEADERS)
    page = context.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        print(f"Tarayıcı açılıyor: {seed_url}")
        response = page.goto(seed_url, wait_until="domcontentloaded", timeout=60000)
        status = response.status if response else "?"
        print(f"Sayfa HTTP: {status} | URL: {page.url}")
        if site == "Hepsiburada" and status == 403:
            for fallback in ["https://www.hepsiburada.com/aradiginburada", "https://www.hepsiburada.com/premium-kupon"]:
                try:
                    response = page.goto(fallback, wait_until="domcontentloaded", timeout=45000)
                    status = response.status if response else "?"
                    print(f"Hepsiburada fallback HTTP: {status} | URL: {page.url}")
                    if status == 200: break
                except Exception as e: print(f"HB fallback: {e}")
        page.wait_for_timeout(2500)
        for _ in range(3): page.mouse.wheel(0, 2500); page.wait_for_timeout(900)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        print(f"Tarayıcı HTML: {len(html)} karakter")
        results, seen = [], set()
        for a in soup.find_all("a", href=True):
            p = card_product(site, a, page.url)
            if not p or p["url"] in seen: continue
            seen.add(p["url"]); results.append(p)
            if len(results) >= MAX_PRODUCTS_PER_SITE: break
        if site == "Hepsiburada":
            print("Hepsiburada: ürün detay fiyatları doğrulanıyor...")
            for p in results: hepsiburada_detail(page, p)
        for p in results:
            extra = f" | kampanya={p['campaign_price']:.2f} TL" if p.get("campaign_price") else ""
            print(f"Bulundu: {site} | {p['price']:.2f} TL{extra} | {p['url']}")
        print(f"{site}: {len(results)} ürün bulundu")
        return results
    finally:
        context.close()


def telegram_send(text):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHANNEL_ID, "text": text, "disable_web_page_preview": False}, timeout=20)
    r.raise_for_status()


def median(values):
    values = sorted(values)
    if not values: return None
    n = len(values); m = n // 2
    return values[m] if n % 2 else (values[m-1] + values[m]) / 2


def get_product(url):
    rows = supabase_get("products", params={"select": "*", "product_url": f"eq.{url}", "limit": 1})
    return rows[0] if rows else None


def process_product(product):
    now = datetime.now(timezone.utc)
    url, site = product["url"], product["site"]
    current = float(product["price"])
    previous = float(product["previous_display_price"]) if product.get("previous_display_price") is not None else None
    campaign = float(product["campaign_price"]) if product.get("campaign_price") is not None else None
    if campaign is not None and campaign >= current: campaign = None
    effective = campaign or current
    existing = get_product(url)
    history = get_price_history(url)
    history_prices = [float(x["price"]) for x in history]
    if not history_prices or history_prices[-1] != current:
        record_price(url, site, current, now.isoformat()); history_prices.append(current)
    reference = median(history_prices) if len(history_prices) >= MIN_HISTORY_SAMPLES else None
    discount = ((reference-effective)/reference*100) if reference else None
    last_posted = float(existing["last_posted_price"]) if existing and existing.get("last_posted_price") is not None else None
    last_at = datetime.fromisoformat(existing["last_posted_at"].replace("Z", "+00:00")) if existing and existing.get("last_posted_at") else None
    should_post = bool(discount is not None and discount >= MIN_DISCOUNT and (last_posted is None or effective < last_posted) and (last_at is None or now-last_at >= timedelta(hours=REPOST_COOLDOWN_HOURS)))
    upsert = {"product_url": url, "product_name": product["name"], "site": site, "current_price": current, "previous_price": previous, "lowest_price": min(history_prices) if history_prices else current, "coupon_code": product.get("coupon_code"), "coupon_price": campaign, "last_seen_at": now.isoformat(), "updated_at": now.isoformat()}
    if should_post:
        old = f"\n🏷️ Görünen eski fiyat: {previous:,.2f} TL" if previous else ""
        camp = f"\n🎟️ Kampanyalı fiyat: {campaign:,.2f} TL" if campaign else ""
        if product.get("coupon_code"): camp += f"\n🏷️ Kod: {product['coupon_code']}"
        if product.get("campaign_note"): camp += f"\nℹ️ {product['campaign_note']}"
        telegram_send(f"🔥 YENİ DÜŞÜŞ!\n\n🛒 {product['name']}\n\n💰 Son 30 gün normal fiyatı: {reference:,.2f} TL\n🔥 Yeni fiyat: {effective:,.2f} TL\n📉 Gerçek indirim: %{discount:.1f}\n🏪 {site}{old}{camp}\n\n🔗 {url}")
        upsert["last_posted_price"], upsert["last_posted_at"] = effective, now.isoformat()
    supabase_upsert(upsert)
    if reference is None:
        print(f"Kontrol: {site} | anlık {current:.2f} -> efektif {effective:.2f} TL | 30g örnek={len(history_prices)} | referans=None | paylaş=False")
    else:
        print(f"Kontrol: {site} | anlık {current:.2f} -> efektif {effective:.2f} TL | 30g örnek={len(history_prices)} | referans={reference:.2f} | indirim=%{discount:.1f} | paylaş={should_post}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        try:
            all_products = []
            for site, seed in SEEDS.items():
                try:
                    products = discover(site, seed, browser)
                    all_products.extend(products)
                    for product in products: process_product(product)
                except Exception as e:
                    print(f"{site} hata: {type(e).__name__}: {e}")
            print(f"Toplam işlenen ürün: {len(all_products)}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
