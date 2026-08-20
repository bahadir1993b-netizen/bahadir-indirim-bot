import os
import re
import html as htmlmod
from datetime import datetime, timezone, timedelta
from io import BytesIO
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
import pytesseract
from bs4 import BeautifulSoup
from PIL import Image

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
CHAT = "-1004424116637"
MAX_AGE = 30
MIN_DISCOUNT = 6.0
COOLDOWN = 12
AMAZON_TAG = os.getenv("AMAZON_ASSOCIATE_TAG", "").strip()

SOURCES = {
    "FirsatMerkezi": "firsatmerkez",
    "OzelFirsatlar": "ozelfirsat",
    "AmazonOzel": "amazonozel",
    "FirsatZ": "firsatz",
    "IndirimDeal": "indirimdeal",
}
MARKET = {"amazon.com.tr": "Amazon", "hepsiburada.com": "Hepsiburada", "trendyol.com": "Trendyol"}
SHORT = {"app.hb.biz": "Hepsiburada", "hps.im": "Hepsiburada", "ty.gl": "Trendyol", "tyml.gl": "Trendyol", "amzn.to": "Amazon", "amzn.eu": "Amazon"}
TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "gclid", "ref", "ref_", "tag", "ascsubtag", "linkcode", "creative", "creativeasin", "camp", "adid", "dib", "dib_tag", "pd_rd_i", "pd_rd_r", "pd_rd_w", "pd_rd_wg", "pf_rd_i", "pf_rd_m", "pf_rd_p", "pf_rd_r", "pf_rd_s", "pf_rd_t", "_encoding", "spm", "partner_id"}
MONEY_RE = re.compile(r"(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)", re.I)
CODE_RE = re.compile(r"\b[A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23}\b", re.I)
HEAD = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36", "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"}


def sb(method, path, **kw):
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Accept": "application/json"}
    if method == "POST":
        h["Prefer"] = "return=representation"
    r = requests.request(method, f"{SB}/rest/v1/{path}", headers=h, timeout=15, **kw)
    r.raise_for_status()
    return r.json() if r.text else []


def clean(u):
    return htmlmod.unescape(u or "").replace("\\/", "/").split("#", 1)[0].rstrip("/")


def money(s):
    s = re.sub(r"[^0-9,.]", "", str(s).replace("TL", "").replace("₺", "").replace(" ", ""))
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
        x = float(s)
        return x if 0 < x < 10000000 else None
    except Exception:
        return None


def prices(text):
    return [money(m.group()) for m in MONEY_RE.finditer(text or "") if money(m.group()) is not None]


def site(url):
    h = urlparse(url).netloc.lower().replace("www.", "")
    if h in MARKET:
        return MARKET[h]
    for k, v in SHORT.items():
        if h == k or h.endswith("." + k):
            return v
    return None


def valid(s, u):
    p = urlparse(u)
    h, path = p.netloc.lower().replace("www.", ""), p.path
    if s == "Amazon":
        return h.endswith("amazon.com.tr") and bool(re.search(r"/(?:dp|gp/product)/[A-Z0-9]{8,}", path, re.I))
    if s == "Hepsiburada":
        return h.endswith("hepsiburada.com") and bool(re.search(r"-p-[A-Za-z0-9]+(?:[/?#&]|$)", path, re.I))
    if s == "Trendyol":
        return h.endswith("trendyol.com") and bool(re.search(r"-p-\d+(?:[/?#&]|$)", path, re.I))
    return False


def normalize(s, u):
    p = urlparse(clean(u))
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING]
    if s == "Amazon" and AMAZON_TAG:
        q.append(("tag", AMAZON_TAG))
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), ""))


def tokens(text):
    stop = {"ürün", "ürünü", "fırsat", "indirim", "adet", "parça", "set", "marka", "model", "yeni", "şimdi", "tl", "sadece", "stok", "kampanya", "sepette", "kod", "kupon"}
    return {x for x in re.findall(r"[a-zçğıöşü0-9]{3,}", (text or "").lower()) if x not in stop}


def title_score(title, candidate):
    a, b = tokens(title), tokens(candidate)
    return len(a & b) / max(1, len(a)) if a and b else 0


def resolve_marketplace(url, source_site):
    try:
        r = requests.get(clean(url), headers=HEAD, timeout=7, allow_redirects=True)
        final = clean(r.url)
        if site(final) == source_site and valid(source_site, final):
            return normalize(source_site, final)
    except Exception:
        pass
    return None


def search_marketplace(title):
    best = None
    for source_site, base in {
        "Amazon": "https://www.amazon.com.tr/s?k=",
        "Hepsiburada": "https://www.hepsiburada.com/ara?q=",
        "Trendyol": "https://www.trendyol.com/sr?q=",
    }.items():
        try:
            r = requests.get(base + requests.utils.quote(title[:140]), headers=HEAD, timeout=7)
            if r.status_code >= 400:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href]"):
                u = clean(a.get("href") or "")
                if not valid(source_site, u):
                    continue
                score = title_score(title, (a.get_text(" ", strip=True) or "")[:600] + " " + u)
                if score >= 0.35 and (best is None or score > best[0]):
                    best = (score, source_site, normalize(source_site, u))
        except Exception:
            continue
    return best[1:] if best else (None, None)


def marketplace_price(source_site, url):
    try:
        r = requests.get(url, headers=HEAD, timeout=8)
        if r.status_code >= 400:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        vals = []
        for sel in ['meta[property="product:price:amount"]', 'meta[itemprop="price"]', '[itemprop="price"]', '[data-price]']:
            for el in soup.select(sel):
                x = money(el.get("content") or el.get("value") or el.get("data-price") or el.get_text(" ", strip=True))
                if x:
                    vals.append(x)
        return min(vals) if vals else None
    except Exception:
        return None


def ocr_photo(url):
    try:
        r = requests.get(url, headers=HEAD, timeout=10)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        return pytesseract.image_to_string(img, lang="tur+eng", config="--psm 6")
    except Exception as e:
        print(f"OCR hata: {e}")
        return ""


def code_amount(code):
    m = re.search(r"(\d{2,4})$", code or "")
    return float(m.group(1)) if m else None


def extract_offers(caption, ocr):
    text = re.sub(r"\r", "", (caption or "") + "\n" + (ocr or ""))
    lines = [re.sub(r"\s+", " ", x).strip(" -•") for x in text.split("\n") if x.strip()]
    offers = []
    for i, line in enumerate(lines):
        m = re.search(r"([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23})\s+(?:KODU|KOD)\s*(?:İLE|ILE)?", line, re.I)
        if not m:
            continue
        code = m.group(1).upper()
        if not re.search(r"[A-ZÇĞİÖŞÜ]", code) or not re.search(r"\d", code):
            continue
        before = line[:m.start()].strip(" -•:")
        after = line[m.end():]
        p = prices(after)
        if not p and i + 1 < len(lines):
            p = prices(lines[i + 1])
        if not p:
            continue
        price = p[0]
        if len(before) < 8:
            continue
        offers.append((before, price, code))
    # Caption may contain a normal coupon code with a product title on the same line.
    if not offers:
        for line in lines:
            m = re.search(r"(?:KUPON|KODU?|PROMOSYON)\s*[:=-]?\s*([A-ZÇĞİÖŞÜ0-9_-]{5,24})", line, re.I)
            if not m:
                continue
            p = prices(line)
            if p:
                title = re.sub(r"(?:KUPON|KODU?|PROMOSYON).*", "", line, flags=re.I).strip(" -:")
                if len(title) >= 8:
                    offers.append((title, p[-1], m.group(1).upper()))
    # Deduplicate OCR/caption copies.
    out, seen = [], set()
    for title, price, code in offers:
        key = (re.sub(r"\W", "", title.lower())[:80], round(price, 2), code)
        if key not in seen:
            seen.add(key)
            out.append((title, price, code))
    return out


def seen(key):
    return bool(sb("GET", "price_history", params={"select": "recorded_at", "product_url": f"eq.telegram_image://{key}", "limit": "1"}))


def remember(key):
    sb("POST", "price_history", json={"price": 0, "product_url": f"telegram_image://{key}", "site": "telegram", "recorded_at": datetime.now(timezone.utc).isoformat()})


def post(source, post_id, source_site, url, title, final_price, reference_price, coupon):
    key = f"{source}:{post_id}:{coupon}"
    if seen(key):
        return False
    if not reference_price or reference_price <= final_price:
        print(f"ATLANDI | {key} | referans fiyat yok")
        return False
    disc = (reference_price - final_price) / reference_price * 100
    if disc < MIN_DISCOUNT:
        print(f"ATLANDI | {key} | %{disc:.1f} < %{MIN_DISCOUNT}")
        remember(key)
        return False
    rows = sb("GET", "products", params={"select": "*", "product_url": f"eq.{url}", "limit": "1"})
    now = datetime.now(timezone.utc).isoformat()
    if rows and rows[0].get("last_posted_at"):
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(rows[0]["last_posted_at"].replace("Z", "+00:00")) < timedelta(hours=COOLDOWN):
                print(f"ATLANDI | {key} | cooldown")
                remember(key)
                return False
        except Exception:
            pass
    payload = {"product_name": title, "current_price": final_price, "previous_price": reference_price, "product_url": url, "site": source_site, "updated_at": now}
    row = sb("PATCH", f"products?id=eq.{rows[0]['id']}", json=payload)[0] if rows else (sb("POST", "products", json=payload) or [payload])[0]
    fmt = lambda x: f"{x:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")
    lines = [f"🔥 %{disc:.0f} İNDİRİM", "", f"🛍️ {title}", f"💰 {fmt(final_price)}", f"🏷️ Önceki: {fmt(reference_price)}", f"🎟️ Kupon: {coupon}", "", "👇 Fırsata git"]
    payload_tg = {"chat_id": CHAT, "text": "\n".join(lines), "disable_web_page_preview": False, "reply_markup": {"inline_keyboard": [[{"text": "🛒 FIRSATA GİT", "url": url}]]}}
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload_tg, timeout=15).raise_for_status()
    if row.get("id"):
        sb("PATCH", f"products?id=eq.{row['id']}", json={"last_posted_at": now})
    remember(key)
    print(f"GÖNDERİLDİ | {source} | {title} | {final_price:.2f} TL | referans={reference_price:.2f} | kod={coupon}")
    return True


def process_message(source, msg):
    post_id = msg.get("data-post", "").split("/")[-1]
    if not post_id:
        return 0
    time_el = msg.select_one("time")
    if not time_el or not time_el.get("datetime"):
        return 0
    try:
        dt = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - dt > timedelta(minutes=MAX_AGE):
            return 0
    except Exception:
        return 0
    photo = msg.select_one(".tgme_widget_message_photo_wrap")
    if not photo:
        return 0
    m = re.search(r"url\(['\"]?([^'\")]+)", photo.get("style", ""))
    if not m:
        return 0
    photo_url = htmlmod.unescape(m.group(1))
    caption_el = msg.select_one(".tgme_widget_message_text")
    caption = caption_el.get_text(" ", strip=True) if caption_el else ""
    ocr = ocr_photo(photo_url)
    offers = extract_offers(caption, ocr)
    if not offers:
        print(f"ATLANDI | {source}:{post_id} | OCR kuponlu teklif bulunamadı")
        return 0
    links = [clean(a.get("href") or "") for a in msg.select("a[href]")]
    market_links = [(site(u), u) for u in links if site(u)]
    sent = 0
    for title, source_price, coupon in offers:
        if market_links:
            source_site, raw_url = market_links[0]
            url = resolve_marketplace(raw_url, source_site) or normalize(source_site, raw_url)
        else:
            source_site, url = search_marketplace(title)
        if not url or not source_site or not valid(source_site, url):
            print(f"ATLANDI | {source}:{post_id} | {title} | ürün linki bulunamadı")
            continue
        live = marketplace_price(source_site, url)
        if not live:
            print(f"ATLANDI | {source}:{post_id} | {title} | canlı fiyat okunamadı")
            continue
        if live > source_price:
            gap = live - source_price
            amount = code_amount(coupon)
            # Exact numeric coupon: FISSLER1200 -> 1,200 TL off.
            if amount is not None and abs(gap - amount) <= max(5.0, amount * 0.015):
                reference = live
            elif gap / live <= 0.12:
                # Small extraction difference; still require the resulting discount to pass.
                reference = live
            else:
                print(f"ATLANDI | {source}:{post_id} | {title} | kupon fiyatı doğrulanamadı | canlı={live:.2f} kaynak={source_price:.2f}")
                continue
        else:
            print(f"ATLANDI | {source}:{post_id} | {title} | kaynak fiyat canlıdan yüksek/eşit")
            continue
        if post(source, post_id, source_site, url, title, source_price, reference, coupon):
            sent += 1
    return sent


def fetch_source(source, username):
    try:
        r = requests.get(f"https://t.me/s/{username}", headers=HEAD, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        boxes = soup.select("div.tgme_widget_message")
        photo_count = sum(1 for b in boxes if b.select_one(".tgme_widget_message_photo_wrap"))
        print(f"OCR kaynak {source}: HTTP {r.status_code} mesaj={len(boxes)} foto={photo_count}")
        total = 0
        for box in boxes:
            total += process_message(source, box)
        return total
    except Exception as e:
        print(f"OCR kaynak {source}: HATA {e}")
        return 0


if __name__ == "__main__":
    print(f"=== Telegram görsel kupon OCR başladı | yaş={MAX_AGE} dk ===")
    total = sum(fetch_source(s, u) for s, u in SOURCES.items())
    print(f"=== OCR bitti. Gönderilen={total} ===")
