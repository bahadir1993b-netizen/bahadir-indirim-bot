import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, urlunparse, quote, parse_qs

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
}
SEEDS = {
    "Amazon": "https://www.amazon.com.tr/gp/goldbox",
    "Hepsiburada": "https://www.hepsiburada.com/ara?q=indirim",
    "Trendyol": "https://www.trendyol.com/sr?q=indirim",
}
MIN_DISCOUNT = 10.0
REPOST_COOLDOWN_HOURS = 12
MAX_PRODUCTS_PER_SITE = 10
PRICE_HISTORY_DAYS = 30
MIN_HISTORY_SAMPLES = 3


def sb_headers(prefer=None):
    h = {"apikey": SUPABASE_KEY, "Content-Type": "application/json", "Accept": "application/json"}
    if SUPABASE_KEY.startswith("eyJ"):
        h["Authorization"] = f"Bearer {SUPABASE_KEY}"
    if prefer:
        h["Prefer"] = prefer
    return h


def sb_get(path, params=None):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=sb_headers(), params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def sb_upsert(p):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_url", headers=sb_headers("resolution=merge-duplicates,return=representation"), json=p, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data[0] if data else p


def record_price(url, site, value, at):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/price_history", headers=sb_headers("return=minimal"), json={"product_url": url, "site": site, "price": value, "recorded_at": at}, timeout=20)
    r.raise_for_status()


def history(url):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=PRICE_HISTORY_DAYS)).isoformat()
    return sb_get("price_history", {"select": "price,recorded_at", "product_url": f"eq.{url}", "recorded_at": f"gte.{cutoff}", "order": "recorded_at.desc"})


def price(value):
    if value is None:
        return None
    s = str(value).replace("TL", "").replace("₺", "").replace(" ", "")
    s = re.sub(r"[^0-9,.]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        a, b = s.rsplit(",", 1)
        s = a.replace(".", "") + "." + b if len(b) <= 2 else s.replace(",", "")
    elif "." in s:
        a, b = s.rsplit(".", 1)
        s = s.replace(".", "") if len(b) > 2 else s
    try:
        return float(s)
    except ValueError:
        return None


# Accept both "1.299,90 TL" and "1.299,90" when the currency is nearby.
PRICE_RE = re.compile(r"(?:₺\s*)?(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)", re.I)
NUMBER_RE = re.compile(r"(?<![\d.])\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?(?![\d.])")


def prices(text):
    return [p for m in PRICE_RE.finditer(text or "") if (p := price(m.group(1))) and p > 0]


def number_prices(text):
    out = []
    for m in NUMBER_RE.finditer(text or ""):
        p = price(m.group(0))
        if p and p >= 20:
            out.append(p)
    return out


def labeled(text, labels):
    for label in labels:
        m = re.search(re.escape(label) + r"[^0-9]{0,120}(?:₺\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)?", text or "", re.I)
        if m and (p := price(m.group(1))):
            return p
    return None


def canonical(url):
    p = urlparse(url)
    return urlunparse((p.scheme or "https", p.netloc.lower(), p.path.rstrip("/"), "", "", ""))


def product_url(site, url):
    p = urlparse(url).path.lower() if "://" in url else url.lower()
    if site == "Amazon":
        return bool(re.search(r"/(?:dp|gp/product|gp/aw/d)/[a-z0-9]{6,}", p))
    if site in ("Hepsiburada", "Trendyol"):
        return bool(re.search(r"-p-\d+", p))
    return False


def make_product(site, name, url, text, current=None, previous=None):
    url = canonical(url)
    if not product_url(site, url):
        return None
    text = re.sub(r"\s+", " ", text or " ").strip()
    if current is None:
        current = labeled(text, ["Sepette", "İndirimli Fiyat", "Fırsatın Fiyatı", "Teklif Fiyatı", "Güncel Fiyat", "Satış Fiyatı", "Peşin Fiyat"])
    if current is None:
        ps = prices(text)
        current = ps[0] if ps else None
    if not current or current <= 0:
        return None
    if previous is None:
        for label in ["Önce", "Eski Fiyat", "Liste Fiyatı", "Piyasa Fiyatı"]:
            previous = labeled(text, [label])
            if previous and previous > current:
                break
    if not previous:
        ps = prices(text)
        higher = [x for x in ps if x > current * 1.05]
        if higher:
            previous = min(higher)
    campaign_price = labeled(text, ["Sepette", "Sepetteki Fiyat"])
    coupon = None
    m = re.search(r"(?:kupon kodu|kupon|kod)\s*[:：]?\s*([A-Z0-9_-]{4,30})", text, re.I)
    if m:
        coupon = m.group(1).upper()
    return {
        "name": re.sub(r"\s+", " ", name or "Ürün").strip()[:300],
        "price": current,
        "previous_display_price": previous if previous and previous > current else None,
        "campaign_price": campaign_price if campaign_price and campaign_price < current else None,
        "coupon_code": coupon,
        "campaign_note": None,
        "url": url,
        "site": site,
    }


def extract_candidate_urls(site, html, base):
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            href = (qs.get("q") or qs.get("url") or [""])[0]
        u = canonical(urljoin(base, href))
        if product_url(site, u) and u not in seen:
            seen.add(u)
            title = a.get("title") or a.get_text(" ", strip=True)
            out.append((u, title))
            if len(out) >= MAX_PRODUCTS_PER_SITE:
                break
    return out


def page_product(site, url, title, browser):
    ctx = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"], viewport={"width": 1440, "height": 1000}, extra_http_headers=HEADERS)
    page = ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        r = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if not r or r.status >= 400:
            return None
        page.wait_for_timeout(1800)
        text = page.locator("body").inner_text(timeout=10000)
        soup = BeautifulSoup(page.content(), "html.parser")
        # JSON-LD gives reliable product name/price on many pages.
        json_price = None
        json_name = title
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = script.string or script.get_text()
                obj = __import__("json").loads(data)
                objs = obj if isinstance(obj, list) else [obj]
                for x in objs:
                    if isinstance(x, dict) and x.get("@type") in ("Product", ["Product"]):
                        json_name = x.get("name") or json_name
                        offers = x.get("offers") or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        json_price = price(offers.get("price")) if isinstance(offers, dict) else None
            except Exception:
                pass
        current = json_price or labeled(text, ["Sepette", "İndirimli Fiyat", "Satış Fiyatı", "Güncel Fiyat", "Fiyat"])
        if current is None:
            ps = prices(text)
            current = ps[0] if ps else None
        ps = prices(text)
        previous = None
        if current and ps:
            higher = [x for x in ps if x > current * 1.05]
            previous = min(higher) if higher else None
        p = make_product(site, json_name or title, url, text, current, previous)
        if p:
            return p
        return make_product(site, title, url, text, current, previous)
    except Exception as e:
        print(f"{site} ürün sayfası hata: {type(e).__name__}: {e}")
        return None
    finally:
        ctx.close()


def search_engine_candidates(site, browser):
    domains = {"Amazon": "amazon.com.tr", "Hepsiburada": "hepsiburada.com", "Trendyol": "trendyol.com"}
    dom = domains[site]
    queries = [f"site:{dom} indirim TL", f"site:{dom} fırsat TL", f"site:{dom} ucuz TL"]
    engines = [
        "https://www.google.com/search?q={q}&hl=tr&num=20",
        "https://www.bing.com/search?q={q}&setlang=tr&count=20",
        "https://html.duckduckgo.com/html/?q={q}",
    ]
    found = []
    seen = set()
    ctx = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    try:
        for q in queries:
            for template in engines:
                try:
                    u = template.format(q=quote(q, safe=""))
                    r = page.goto(u, wait_until="domcontentloaded", timeout=20000)
                    if not r or r.status >= 400:
                        continue
                    page.wait_for_timeout(700)
                    for candidate in extract_candidate_urls(site, page.content(), page.url):
                        if candidate[0] not in seen:
                            seen.add(candidate[0])
                            found.append(candidate)
                    if len(found) >= MAX_PRODUCTS_PER_SITE:
                        return found[:MAX_PRODUCTS_PER_SITE]
                except Exception as e:
                    print(f"{site} arama motoru hata: {type(e).__name__}: {e}")
    finally:
        ctx.close()
    return found[:MAX_PRODUCTS_PER_SITE]


def direct_discover(site, seed, browser):
    ctx = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"], viewport={"width": 1440, "height": 1000}, extra_http_headers=HEADERS)
    page = ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        r = page.goto(seed, wait_until="domcontentloaded", timeout=60000)
        status = r.status if r else 0
        print(f"{site} web HTTP: {status}")
        if status != 200:
            return []
        page.wait_for_timeout(3000)
        for _ in range(5):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(500)
        candidates = extract_candidate_urls(site, page.content(), page.url)
        print(f"{site} web: {len(candidates)} ürün linki bulundu")
    except Exception as e:
        print(f"{site} web: {type(e).__name__}: {e}")
        candidates = []
    finally:
        ctx.close()
    return candidates


def discover(site, seed, browser):
    candidates = direct_discover(site, seed, browser)
    if not candidates:
        candidates = search_engine_candidates(site, browser)
        print(f"{site} arama fallback: {len(candidates)} ürün linki bulundu")
    if not candidates:
        return []
    products = []
    for url, title in candidates:
        p = page_product(site, url, title, browser)
        if p:
            products.append(p)
        if len(products) >= MAX_PRODUCTS_PER_SITE:
            break
    print(f"{site}: {len(products)} fiyatlı ürün bulundu")
    return products


def telegram(text):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHANNEL_ID, "text": text, "disable_web_page_preview": False}, timeout=20)
    print(f"Telegram HTTP: {r.status_code} | {r.text[:300]}")
    r.raise_for_status()


def median(values):
    values = sorted(values)
    n = len(values)
    return None if not values else values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def process(p):
    now = datetime.now(timezone.utc)
    url, current = p["url"], float(p["price"])
    previous = p.get("previous_display_price")
    hist = history(url)
    hist_prices = [float(x["price"]) for x in hist if x.get("price") is not None]
    baseline = float(previous) if previous and float(previous) > current else None
    if len(hist_prices) >= MIN_HISTORY_SAMPLES:
        hm = median(hist_prices)
        if hm and hm > current:
            baseline = max(baseline or 0, hm)
    discount = ((baseline - current) / baseline * 100) if baseline and baseline > current else 0
    row = dict(p)
    row.update({"price": current, "discount_percent": round(discount, 2), "last_seen_at": now.isoformat()})
    saved = sb_upsert(row)
    record_price(url, p["site"], current, now.isoformat())
    print(f"{p['site']} | %{discount:.1f} | {current:.2f} TL | {p['name'][:80]}")
    if discount < MIN_DISCOUNT:
        return False
    last_posted = saved.get("last_posted_at") if isinstance(saved, dict) else None
    if last_posted:
        try:
            if now - datetime.fromisoformat(last_posted.replace("Z", "+00:00")) < timedelta(hours=REPOST_COOLDOWN_HOURS):
                return False
        except Exception:
            pass
    msg = f"🔥 %{discount:.0f} İNDİRİM\n\n{p['name']}\n\n💰 {current:,.2f} TL"
    if baseline:
        msg += f"\n🏷️ Önce: {baseline:,.2f} TL"
    if p.get("campaign_price"):
        msg += f"\n🛒 Sepette: {p['campaign_price']:,.2f} TL"
    if p.get("coupon_code"):
        msg += f"\n🎟️ Kod: {p['coupon_code']}"
    msg += f"\n\n🛍️ {p['site']}\n🔗 {p['url']}"
    telegram(msg)
    requests.patch(f"{SUPABASE_URL}/rest/v1/products", headers={**sb_headers(), "Prefer": "return=minimal"}, params={"product_url": f"eq.{url}"}, json={"last_posted_at": now.isoformat()}, timeout=20).raise_for_status()
    return True


def main():
    print("=== İndirim botu başladı ===")
    sent = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for site, seed in SEEDS.items():
                try:
                    products = discover(site, seed, browser)
                    print(f"{site}: {len(products)} aday")
                    for p in products:
                        try:
                            if process(p):
                                sent += 1
                        except Exception as e:
                            print(f"{site} ürün işleme hatası: {type(e).__name__}: {e}")
                except Exception as e:
                    print(f"{site} genel hata: {type(e).__name__}: {e}")
        finally:
            browser.close()
    print(f"=== Bitti. Gönderilen: {sent} ===")


if __name__ == "__main__":
    main()
