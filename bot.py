import os
import re
import json
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
    "Sec-CH-UA": '"Chromium";v="151", "Google Chrome";v="151", "Not_A Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
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
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json", "User-Agent": "bahadir-indirim-bot/1.0", "Accept": "application/json"}
    if SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    if prefer:
        headers["Prefer"] = prefer
    return headers


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
        prefix = text[max(0, start - 5):start]
        if "%" in prefix:
            continue
        p = clean_price(match.group(1))
        if p is not None and p > 0:
            vals.append(p)
    return vals


def labeled_price(text, labels):
    for label in labels:
        m = re.search(re.escape(label) + r"\s*(?:₺\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)", text, re.I)
        if m:
            value = clean_price(m.group(1))
            if value is not None and value > 0:
                return value
    return None


def hepsiburada_labeled_price(text, labels):
    for label in labels:
        pattern = re.escape(label) + r"[^0-9₺]{0,70}(?:₺\s*)?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)"
        m = re.search(pattern, text, re.I)
        if m:
            value = clean_price(m.group(1))
            if value is not None and value > 0:
                return value
    return None


def hepsiburada_price_candidates(text):
    candidates = []
    pattern = r"(?:₺\s*)?(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)"
    negative = [
        "taksit", "ay x", "x ay", "puan", "worldpuan", "parapuan", "bonus",
        "parafpara", "parafpuan", "chip-para", "maximum puan", "kupon kazan",
        "kupon tutarı", "indirim tutarı", "kazanç", "hediye çek", "çek kazan"
    ]
    positive = [
        "fiyat", "satış fiyatı", "güncel fiyat", "peşin fiyat", "sepette", "sepetteki fiyat",
        "sepette indirimli fiyat", "hemen al", "kampanyalı fiyat"
    ]
    old_price_words = ["eski fiyat", "önceki fiyat", "piyasa fiyatı"]

    for match in re.finditer(pattern, text, re.I):
        value = clean_price(match.group(1))
        if value is None or value <= 0:
            continue
        start = match.start(1)
        context = text[max(0, start - 120):min(len(text), match.end() + 100)].lower()
        score = 0
        if any(word in context for word in negative):
            score -= 15
        if any(word in context for word in positive):
            score += 8
        if any(word in context for word in old_price_words):
            score -= 8
        if value < 50:
            score -= 8
        elif value < 100:
            score -= 3
        elif value >= 500:
            score += 2
        candidates.append((score, value, context))

    return candidates


def hepsiburada_prices(text):
    current = hepsiburada_labeled_price(text, [
        "Sepetteki Fiyat", "Sepette indirimli fiyat", "Sepette", "Peşin Fiyat",
        "Güncel Fiyat", "Satış Fiyatı", "Kampanyalı Fiyat"
    ])

    if current is None:
        candidates = hepsiburada_price_candidates(text)
        if candidates:
            candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
            current = candidates[0][1]

    if current is None:
        return None, None

    previous = None
    for label in ["Eski Fiyat", "Önceki Fiyat", "Piyasa Fiyatı"]:
        previous = hepsiburada_labeled_price(text, [label])
        if previous is not None and previous > current:
            break
        previous = None
    return current, previous


def extract_campaign(text, current):
    """Rakip fırsat kanallarında sık görülen kampanya kalıplarını güvenli biçimde çıkar."""
    result = {"campaign_price": None, "coupon_code": None, "campaign_note": None}
    if not text or current is None:
        return result

    # Açıkça belirtilen "tanesi X TL" kalıbı en güvenilir kampanya fiyatıdır.
    patterns = [
        r"(?:tanesi|adet fiyatı|birim fiyatı)\s*(?:₺\s*)?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)",
        r"(?:sepette|ödeme adımında|ödemede)\s*(?:₺\s*)?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            value = clean_price(m.group(1))
            if value and value < current:
                result["campaign_price"] = value
                break

    # 2. ürün 1 TL / 2 al 1 öde / 3 al 2 öde gibi kampanyalar.
    if result["campaign_price"] is None:
        m = re.search(r"(\d+)\s*(?:\.\s*ürün|\.ürün|ürün)\s*(\d+(?:[.,]\d+)?)\s*TL", text, re.I)
        if m:
            buy_n = int(m.group(1))
            second_price = clean_price(m.group(2))
            if buy_n > 1 and second_price is not None:
                result["campaign_price"] = (current * (buy_n - 1) + second_price) / buy_n
        else:
            m = re.search(r"(\d+)\s*(?:al\s*)?(\d+)\s*öde", text, re.I)
            if m:
                buy_n, pay_n = int(m.group(1)), int(m.group(2))
                if buy_n > pay_n > 0:
                    result["campaign_price"] = current * pay_n / buy_n

    # X/Y TL kuponu: X alışveriş eşiği, Y indirim. Ürünün tek başına eşiği karşılayıp karşılamadığını varsayma;
    # yalnızca ürün fiyatı eşiği karşılıyorsa güvenli bir aday üret.
    if result["campaign_price"] is None:
        m = re.search(r"(\d{2,6})\s*/\s*(\d{1,4})\s*TL\s*(?:kupon|indirim)?", text, re.I)
        if m:
            threshold, discount = float(m.group(1)), float(m.group(2))
            if current >= threshold and 0 < discount < current:
                result["campaign_price"] = current - discount

    # Açık kampanya kodlarını kaydet; hesaplamayı kodun tutarı net değilse yapma.
    code_patterns = [
        r"(?:kod|kupon kodu|kodu)\s*[:：]?\s*([A-Z0-9_-]{4,30})",
        r"\b([A-Z]{3,}[0-9]{1,4})\b\s*(?:kodu|ile)",
    ]
    for pattern in code_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            code = m.group(1).upper()
            if code not in {"TL", "FIRSAT", "KUPON"}:
                result["coupon_code"] = code
                break

    # Kampanya notunu kısa ve Telegram'a uygun tut.
    notes = []
    for phrase in ["Prime Üyelere Özel", "Premium Üyelik", "Ücretsiz Kargo", "Mobil Uygulamada Geçerli", "2'li alımda", "3'lü alımda", "4 al 3 öde", "2 al 1 öde", "Çok Al Az Öde"]:
        if re.search(re.escape(phrase), text, re.I):
            notes.append(phrase)
    if result["coupon_code"]:
        notes.append(f"Kod: {result['coupon_code']}")
    if notes:
        result["campaign_note"] = " • ".join(dict.fromkeys(notes))
    return result


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
        return "-p-" in p and bool(re.search(r"-p-\d+", p))
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
            previous = max(prices[1:]) if len(prices) > 1 else None
        if current is None:
            return None
    elif site == "Hepsiburada":
        current, previous = hepsiburada_prices(best)
        if current is None:
            return None
    elif site == "Pazarama":
        prices = prices_from_text(best)
        if not prices:
            return None
        current = prices[-1]
        previous = None
        low_match = re.search(r"Son\s+30\s+Günün\s+En\s+Düşük\s+Fiyatı", best, re.I)
        if low_match and prices[0] > current:
            previous = prices[0]
    else:
        prices = prices_from_text(best)
        if not prices:
            return None
        current = prices[0]
        previous = max(prices[1:]) if len(prices) > 1 else None

    if previous is not None and previous <= current:
        previous = None

    campaign = extract_campaign(best, current)
    name = a.get("aria-label") or a.get("title") or text
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"(?:Sepete Ekle|Hızlı Bakış|Kargo Bedava)$", "", name).strip()
    if len(name) < 5:
        return None
    return {
        "name": name[:300], "price": current, "previous_display_price": previous,
        "campaign_price": campaign["campaign_price"], "coupon_code": campaign["coupon_code"],
        "campaign_note": campaign["campaign_note"], "url": href, "site": site
    }


def hepsiburada_detail(page, product):
    """HB kategori kartı bozuksa ürün sayfasındaki JSON-LD fiyatını kullan."""
    try:
        response = page.goto(product["url"], wait_until="domcontentloaded", timeout=30000)
        if not response or response.status >= 400:
            return product
        page.wait_for_timeout(900)

        # Önce yapılandırılmış ürün verisi.
        for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
            try:
                data = json.loads(raw)
            except Exception:
                continue
            items = data.get("@graph", []) if isinstance(data, dict) else data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict) or item.get("@type") != "Product":
                    continue
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = clean_price(offers.get("price")) if isinstance(offers, dict) else None
                if price and price > 0:
                    product["price"] = price
                    product["previous_display_price"] = product.get("previous_display_price")
                    product["name"] = str(item.get("name") or product["name"])[:300]
                    break

        # JSON-LD yoksa ürün sayfasının görünen metnini tekrar değerlendir.
        detail_text = page.locator("body").inner_text(timeout=10000)
        detail_current, detail_previous = hepsiburada_prices(detail_text)
        if detail_current and (product.get("price") is None or product["price"] < 50 or abs(detail_current - product["price"]) > max(100, product["price"] * 0.25)):
            product["price"] = detail_current
        if detail_previous and detail_previous > product["price"]:
            product["previous_display_price"] = detail_previous

        campaign = extract_campaign(detail_text, product["price"])
        if campaign["campaign_price"]:
            product["campaign_price"] = campaign["campaign_price"]
        if campaign["coupon_code"]:
            product["coupon_code"] = campaign["coupon_code"]
        if campaign["campaign_note"]:
            product["campaign_note"] = campaign["campaign_note"]
        return product
    except Exception as e:
        print(f"HB detay uyarısı: {type(e).__name__}: {e}")
        return product


def prepare_page(page, site):
    if site == "Trendyol":
        try:
            page.goto("https://www.trendyol.com/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1800)
            print(f"Trendyol TR ana sayfa URL: {page.url}")
        except Exception as e:
            print(f"Trendyol TR ön hazırlık uyarısı: {type(e).__name__}: {e}")


def discover(site, seed_url, browser):
    context = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"], viewport={"width": 1440, "height": 1000}, extra_http_headers=HEADERS)
    page = context.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        prepare_page(page, site)
        print(f"Tarayıcı açılıyor: {seed_url}")
        response = page.goto(seed_url, wait_until="domcontentloaded", timeout=60000)
        status = response.status if response else "?"
        print(f"Sayfa HTTP: {status} | URL: {page.url}")

        if site == "Hepsiburada" and status == 403:
            for fallback in ["https://www.hepsiburada.com/aradiginburada", "https://www.hepsiburada.com/premium-kupon"]:
                print(f"Hepsiburada fallback deneniyor: {fallback}")
                try:
                    response = page.goto(fallback, wait_until="domcontentloaded", timeout=45000)
                    status = response.status if response else "?"
                    print(f"Hepsiburada fallback HTTP: {status} | URL: {page.url}")
                    if status == 200:
                        break
                except Exception as e:
                    print(f"Hepsiburada fallback hatası: {type(e).__name__}: {e}")

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

        if site == "Hepsiburada":
            print("Hepsiburada: ürün detay fiyatları doğrulanıyor...")
            for p in results:
                p = hepsiburada_detail(page, p)

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
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def get_product(url):
    rows = supabase_get("products", params={"select": "*", "product_url": f"eq.{url}", "limit": 1})
    return rows[0] if rows else None


def process_product(product):
    now = datetime.now(timezone.utc)
    url = product["url"]
    site = product["site"]
    current = float(product["price"])
    previous_display = product.get("previous_display_price")
    if previous_display is not None:
        previous_display = float(previous_display)
    campaign_price = product.get("campaign_price")
    if campaign_price is not None:
        campaign_price = float(campaign_price)
        if campaign_price >= current:
            campaign_price = None

    effective_price = campaign_price or current
    existing = get_product(url)
    history = get_price_history(url)
    history_prices = [float(row["price"]) for row in history]

    # Geçmişte daima ana/normal satış fiyatını tutuyoruz; kuponlu fiyatı geçmişe karıştırmıyoruz.
    if not history_prices or history_prices[-1] != current:
        record_price(url, site, current, now.isoformat())
        history_prices.append(current)

    reference = median(history_prices) if len(history_prices) >= MIN_HISTORY_SAMPLES else None
    discount = ((reference - effective_price) / reference * 100) if reference and reference > 0 else None

    last_posted_price = float(existing["last_posted_price"]) if existing and existing.get("last_posted_price") is not None else None
    last_posted_at = datetime.fromisoformat(existing["last_posted_at"].replace("Z", "+00:00")) if existing and existing.get("last_posted_at") else None

    should_post = False
    if discount is not None and discount >= MIN_DISCOUNT:
        if last_posted_price is None or effective_price < last_posted_price:
            if last_posted_at is None or now - last_posted_at >= timedelta(hours=REPOST_COOLDOWN_HOURS):
                should_post = True

    upsert = {
        "product_url": url,
        "product_name": product["name"],
        "site": site,
        "current_price": current,
        "previous_price": previous_display,
        "lowest_price": min(history_prices) if history_prices else current,
        "coupon_code": product.get("coupon_code"),
        "coupon_price": campaign_price,
        "last_seen_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    if should_post:
        old_text = f"\n🏷️ Görünen eski fiyat: {previous_display:,.2f} TL" if previous_display else ""
        campaign_text = ""
        if campaign_price:
            campaign_text = f"\n🎟️ Kampanyalı fiyat: {campaign_price:,.2f} TL"
        if product.get("coupon_code"):
            campaign_text += f"\n🏷️ Kod: {product['coupon_code']}"
        if product.get("campaign_note"):
            campaign_text += f"\nℹ️ {product['campaign_note']}"
        text = (
            "🔥 YENİ DÜŞÜŞ!\n\n"
            f"🛒 {product['name']}\n\n"
            f"💰 Son 30 gün normal fiyatı: {reference:,.2f} TL\n"
            f"🔥 Yeni fiyat: {effective_price:,.2f} TL\n"
            f"📉 Gerçek indirim: %{discount:.1f}\n"
            f"🏪 {site}"
            f"{old_text}{campaign_text}\n\n"
            f"🔗 {url}"
        )
        telegram_send(text)
        upsert["last_posted_price"] = effective_price
        upsert["last_posted_at"] = now.isoformat()

    supabase_upsert(upsert)

    if reference is None:
        print(f"Kontrol: {site} | anlık {current:.2f} -> efektif {effective_price:.2f} TL | 30g örnek={len(history_prices)} | referans=None | paylaş=False")
    else:
        print(f"Kontrol: {site} | anlık {current:.2f} -> efektif {effective_price:.2f} TL | 30g örnek={len(history_prices)} | referans={reference:.2f} | indirim=%{discount:.1f} | paylaş={should_post}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        try:
            all_products = []
            for site, seed in SEEDS.items():
                try:
                    products = discover(site, seed, browser)
                    all_products.extend(products)
                    for product in products:
                        process_product(product)
                except Exception as e:
                    print(f"{site} hata: {type(e).__name__}: {e}")
            print(f"Toplam işlenen ürün: {len(all_products)}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
