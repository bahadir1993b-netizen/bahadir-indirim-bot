import os
import re
import json
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
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}

SEEDS = {
    "Amazon": "https://www.amazon.com.tr/gp/goldbox",
    "Hepsiburada": "https://www.hepsiburada.com/ara?q=indirim",
    "Trendyol": "https://www.trendyol.com/sr?q=indirim",
}

MIN_DISCOUNT = 10.0
REPOST_COOLDOWN_HOURS = 12
MAX_PRODUCTS_PER_SITE = 12
PRICE_HISTORY_DAYS = 30
MIN_HISTORY_SAMPLES = 3
session = requests.Session()
session.headers.update(HEADERS)


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
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_url",
        headers=sb_headers("resolution=merge-duplicates,return=representation"),
        json=p,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data[0] if data else p


def record_price(url, site, value, at):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/price_history",
        headers=sb_headers("return=minimal"),
        json={"product_url": url, "site": site, "price": value, "recorded_at": at},
        timeout=20,
    )
    r.raise_for_status()


def history(url):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=PRICE_HISTORY_DAYS)).isoformat()
    return sb_get("price_history", {
        "select": "price,recorded_at",
        "product_url": f"eq.{url}",
        "recorded_at": f"gte.{cutoff}",
        "order": "recorded_at.desc",
    })


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


PRICE_RE = re.compile(r"(?:₺\s*)?(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)", re.I)


def prices(text):
    return [p for m in PRICE_RE.finditer(text or "") if (p := price(m.group(1))) and p > 0]


def labeled(text, labels):
    for label in labels:
        m = re.search(
            re.escape(label) + r"[^0-9]{0,100}(?:₺\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)",
            text or "", re.I,
        )
        if m and (p := price(m.group(1))):
            return p
    return None


def canonical(url):
    p = urlparse(url)
    return urlunparse((p.scheme or "https", p.netloc, p.path.rstrip("/"), "", "", ""))


def product_url(site, path):
    p = (path or "").lower()
    if site == "Amazon":
        return bool(re.search(r"/(?:dp|gp/product|gp/aw/d)/[a-z0-9]{6,}", p))
    if site == "Hepsiburada":
        return bool(re.search(r"-p-\d+", p))
    if site == "Trendyol":
        return bool(re.search(r"-p-\d+", p))
    return False


def make_product(site, name, url, text, current=None, previous=None):
    url = canonical(url)
    if not product_url(site, urlparse(url).path):
        return None
    text = text or ""
    if current is None:
        current = labeled(text, ["Sepette", "İndirimli Fiyat", "Fırsatın Fiyatı", "Teklif Fiyatı", "Güncel Fiyat", "Satış Fiyatı"])
        if current is None:
            ps = prices(text)
            current = ps[0] if ps else None
    if not current or current <= 0:
        return None
    if previous is None:
        for label in ["Önceki", "Eski Fiyat", "Liste Fiyatı"]:
            previous = labeled(text, [label])
            if previous and previous > current:
                break
        else:
            previous = None
    campaign_price = None
    m = re.search(r"sepette[^0-9]{0,60}(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)", text, re.I)
    if m and (p := price(m.group(1))) and p < current:
        campaign_price = p
    coupon = None
    m = re.search(r"(?:kupon kodu|kupon|kod)\s*[:：]?\s*([A-Z0-9_-]{4,30})", text, re.I)
    if m:
        coupon = m.group(1).upper()
    return {
        "name": re.sub(r"\s+", " ", name or "Ürün").strip()[:300],
        "price": current,
        "previous_display_price": previous if previous and previous > current else None,
        "campaign_price": campaign_price,
        "coupon_code": coupon,
        "campaign_note": None,
        "url": url,
        "site": site,
    }


def parse_anchor(site, a, base):
    href = a.get("href", "")
    if href.startswith("/url?"):
        qs = parse_qs(urlparse(href).query)
        href = (qs.get("q") or qs.get("url") or [""])[0]
    u = canonical(urljoin(base, href))
    if not product_url(site, urlparse(u).path):
        return None
    node = a
    best = a.get_text(" ", strip=True)
    for _ in range(5):
        node = node.parent
        if not node:
            break
        t = node.get_text(" ", strip=True)
        if len(t) > len(best) and len(t) < 1800:
            best = t
    ps = prices(best)
    if not ps:
        return None
    # Search snippets generally place current price before the crossed/list price.
    if site == "Amazon":
        current = labeled(best, ["Fiyat, ürün sayfası", "Fiyat", "Fırsatın Fiyatı"]) or ps[0]
        previous = None
        if len(ps) > 1:
            previous = max(ps[1:])
    elif site == "Trendyol":
        current = labeled(best, ["Sepette"]) or ps[0]
        previous = max(ps[1:]) if len(ps) > 1 else None
    else:
        current = labeled(best, ["Sepette", "Peşin Fiyat", "Güncel Fiyat", "Satış Fiyatı"]) or ps[0]
        previous = max(ps[1:]) if len(ps) > 1 else None
    name = a.get("title") or a.get_text(" ", strip=True) or best[:300]
    return make_product(site, name, u, best, current, previous)


def bing_fallback(site, browser):
    domains = {"Amazon": "amazon.com.tr", "Hepsiburada": "hepsiburada.com", "Trendyol": "trendyol.com"}
    dom = domains[site]
    queries = [f"site:{dom} indirim TL", f"site:{dom} fırsat TL", f"site:{dom} \"Sepette\" TL"]
    out, seen = [], set()
    ctx = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    try:
        for q in queries:
            try:
                url = "https://www.bing.com/search?" + quote("q=" + q + "&setlang=tr-tr&count=20", safe="=&")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(800)
                soup = BeautifulSoup(page.content(), "html.parser")
                anchors = soup.select("li.b_algo h2 a") or soup.find_all("a", href=True)
                for a in anchors:
                    p = parse_anchor(site, a, page.url)
                    if p and p["url"] not in seen:
                        out.append(p)
                        seen.add(p["url"])
                    if len(out) >= MAX_PRODUCTS_PER_SITE:
                        break
                if len(out) >= MAX_PRODUCTS_PER_SITE:
                    break
            except Exception as e:
                print(f"{site} Bing fallback: {type(e).__name__}: {e}")
    finally:
        ctx.close()
    print(f"{site} Bing fallback: {len(out)} ürün bulundu")
    return out


def direct_discover(site, seed, browser):
    ctx = browser.new_context(
        locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"],
        viewport={"width": 1440, "height": 1000}, extra_http_headers=HEADERS,
    )
    page = ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        r = page.goto(seed, wait_until="domcontentloaded", timeout=60000)
        status = r.status if r else 0
        print(f"{site} web HTTP: {status}")
        if status != 200:
            return []
        page.wait_for_timeout(2500)
        for _ in range(4):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(700)
        soup = BeautifulSoup(page.content(), "html.parser")
        out, seen = [], set()
        for a in soup.find_all("a", href=True):
            p = parse_anchor(site, a, page.url)
            if p and p["url"] not in seen:
                out.append(p)
                seen.add(p["url"])
            if len(out) >= MAX_PRODUCTS_PER_SITE:
                break
        print(f"{site} web: {len(out)} ürün bulundu")
        return out
    except Exception as e:
        print(f"{site} web: {type(e).__name__}: {e}")
        return []
    finally:
        ctx.close()


def discover(site, seed, browser):
    out = direct_discover(site, seed, browser)
    if out:
        return out
    return bing_fallback(site, browser)


def telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHANNEL_ID, "text": text, "disable_web_page_preview": False},
        timeout=20,
    )
    print(f"Telegram HTTP: {r.status_code} | {r.text[:300]}")
    r.raise_for_status()


def median(values):
    values = sorted(values)
    n = len(values)
    if not values:
        return None
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def process(p):
    now = datetime.now(timezone.utc)
    url = p["url"]
    current = float(p["price"])
    previous = p.get("previous_display_price")
    hist = history(url)
    hist_prices = [float(x["price"]) for x in hist if x.get("price") is not None]
    baseline = float(previous) if previous and float(previous) > current else None
    if len(hist_prices) >= MIN_HISTORY_SAMPLES:
        historical_median = median(hist_prices)
        if historical_median and historical_median > current:
            baseline = max(baseline or 0, historical_median)
    discount = ((baseline - current) / baseline * 100) if baseline and baseline > current else 0

    row = dict(p)
    row["price"] = current
    row["discount_percent"] = round(discount, 2)
    row["last_seen_at"] = now.isoformat()
    saved = sb_upsert(row)
    record_price(url, p["site"], current, now.isoformat())

    if discount < MIN_DISCOUNT:
        return False
    last_posted = saved.get("last_posted_at") if isinstance(saved, dict) else None
    if last_posted:
        try:
            last_dt = datetime.fromisoformat(last_posted.replace("Z", "+00:00"))
            if now - last_dt < timedelta(hours=REPOST_COOLDOWN_HOURS):
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

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/products",
        headers={**sb_headers(), "Prefer": "return=minimal"},
        params={"product_url": f"eq.{url}"},
        json={"last_posted_at": now.isoformat()},
        timeout=20,
    ).raise_for_status()
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
