import os
import re
import html as htmlmod
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
import pytesseract
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO

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
MONEY_RE = re.compile(r"(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])", re.I)
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
        r = requests.get(clean(url), headers=HEAD, timeout=6, allow_redirects=True)
        final = clean(r.url)
        if site(final) == source_site and valid(source_site, final):
            return normalize(source_site, final)
    except Exception:
        pass
    return None


def search_marketplace(source_site, title):
    base = {"Amazon": "https://www.amazon.com.tr/s?k=", "Hepsiburada": "https://www.hepsiburada.com/ara?q=", "Trendyol": "https://www.trendyol.com/sr?q="}.get(source_site)
    if not base or not title:
        return None
    try:
        r = requests.get(base + requests.utils.quote(title[:140]), headers=HEAD, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        candidates = []
        for a in soup.select("a[href]"):
            u = clean(a.get("href") or "")
            if valid(source_site, u):
                candidates.append((title_score(title, (a.get_text(" ", strip=True) or "")[:600] + " " + u), u))
        if candidates:
            score, u = max(candidates, key=lambda x: x[0])
            if score >= 0.30:
                return normalize(source_site, u)
    except Exception:
        pass
    return None


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
        if not vals:
            return None
        return min(vals)
    except Exception:
        return None


def ocr_photo(url):
    try:
        r = requests.get(url, headers=HEAD, timeout=10)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        # Turkish + English catches marketplace labels and coupon codes.
        text = pytesseract.image_to_string(img, lang="tur+eng", config="--psm 6")
        return text
    except Exception as e:
        print(f"OCR hata: {e}")
        return ""


def extract_code(text):
    for m in CODE_RE.finditer(text or ""):
        code = m.group().upper().strip("_-.")
        if len(code) < 5 or code.isdigit():
            continue
        if code in {"FIRSAT", "MERKEZI", "AMAZON", "HEPSIBURADA", "TRENDYOL", "KAMPANYA", "INDIRIM", "SEPETE", "KODU", "KUPÖN"}:
            continue
        if re.search(r"[A-ZÇĞİÖŞÜ]", code) and re.search(r"\d", code):
            return code
    return None


def code_amount(code):
    if not code:
        return None
    m = re.search(r"(?:^|[^0-9])(\d{2,4})(?:$|[^0-9])", code)
    if not m:
        m = re.search(r"(\d{2,4})$", code)
    return float(m.group(1)) if m else None


def extract_product_text(caption, ocr):
    combined = (caption or "") + "\n" + (ocr or "")
    lines = [re.sub(r"\s+", " ", x).strip(" -•") for x in combined.splitlines() if x.strip()]
    bad = re.compile(r"(?:TL|₺|KOD|KUPON|KODU|KUPONU|FIRSAT|INDIRIM|KAMPANYA|KARGO|SEPET|TIKLA|GIT|GİT|WASP|KODU ILE|KODU İLE)", re.I)
    for line in lines:
        if 15 <= len(line) <= 180 and not bad.search(line) and len(prices(line)) == 0:
            if title_score(line, combined) >= 0.15:
                return line
    # Prefer caption text when it contains a normal product sentence.
    for line in lines:
        if 15 <= len(line) <= 180 and not bad.search(line) and len(prices(line)) == 0:
            return line
    return None


def seen(key):
    return bool(sb("GET", "price_history", params={"select": "recorded_at", "product_url": f"eq.telegram_image://{key}", "limit": "1"}))


def remember(key):
    sb("POST", "price_history", json={"price": 0, "product_url": f"telegram_image://{key}", "site": "telegram", "recorded_at": datetime.now(timezone.utc).isoformat()})


def post(source, post_id, source_site, url, title, final_price, reference_price, coupon):
    key = f"{source}:{post_id}"
    if seen(key):
        return False
    if reference_price and reference_price > final_price:
        disc = (reference_price - final_price) / reference_price * 100
        if disc < MIN_DISCOUNT:
            print(f"ATLANDI | {key} | %{disc:.1f} < %{MIN_DISCOUNT}")
            remember(key)
            return False
    else:
        disc = None
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
    lines = [f"🔥 %{disc:.0f} İNDİRİM" if disc is not None else "🎟️ KUPONLU FIRSAT", "", f"🛍️ {title}", f"💰 {final_price:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")]
    if reference_price and reference_price > final_price:
        lines.append(f"🏷️ Önceki: {reference_price:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", "."))
    if coupon:
        lines.append(f"🎟️ Kupon: {coupon}")
    lines += ["", "👇 Fırsata git"]
    payload_tg = {"chat_id": CHAT, "text": "\n".join(lines), "disable_web_page_preview": False, "reply_markup": {"inline_keyboard": [[{"text": "🛒 FIRSATA GİT", "url": url}]]}}
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload_tg, timeout=15).raise_for_status()
    if row.get("id"):
        sb("PATCH", f"products?id=eq.{row['id']}", json={"last_posted_at": now})
    remember(key)
    print(f"GÖNDERİLDİ | {source} | {title} | {final_price:.2f} TL | kod={coupon}")
    return True


def process_message(source, box):
    msg = box.select_one(".tgme_widget_message") if box.name != "div.tgme_widget_message" else box
    if not msg:
        return False
    post_id = msg.get("data-post", "").split("/")[-1]
    if not post_id:
        return False
    if seen(f"{source}:{post_id}"):
        return False
    time_el = msg.select_one("time")
    if not time_el or not time_el.get("datetime"):
        return False
    try:
        dt = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - dt > timedelta(minutes=MAX_AGE):
            return False
    except Exception:
        return False
    photo = msg.select_one(".tgme_widget_message_photo_wrap")
    if not photo:
        return False
    style = photo.get("style", "")
    m = re.search(r"url\(['\"]?([^'\")]+)", style)
    if not m:
        return False
    photo_url = htmlmod.unescape(m.group(1))
    caption_el = msg.select_one(".tgme_widget_message_text")
    caption = caption_el.get_text(" ", strip=True) if caption_el else ""
    ocr = ocr_photo(photo_url)
    combined = caption + "\n" + ocr
    coupon = extract_code(combined)
    vals = prices(combined)
    if not vals:
        print(f"ATLANDI | {source}:{post_id} | OCR fiyat bulunamadı")
        return False
    source_price = min(vals)
    title = extract_product_text(caption, ocr)
    if not title:
        print(f"ATLANDI | {source}:{post_id} | ürün adı bulunamadı")
        return False
    links = [clean(a.get("href") or "") for a in msg.select("a[href]")]
    market_links = [(site(u), u) for u in links if site(u)]
    if market_links:
        source_site, raw_url = market_links[0]
        url = resolve_marketplace(raw_url, source_site) or normalize(source_site, raw_url)
    else:
        # Image-only posts often have no visible marketplace URL; find the product directly.
        source_site = "Amazon" if re.search(r"amazon", combined, re.I) else "Hepsiburada" if re.search(r"hepsiburada|hb", combined, re.I) else "Trendyol" if re.search(r"trendyol", combined, re.I) else None
        if not source_site:
            print(f"ATLANDI | {source}:{post_id} | pazar yeri bulunamadı")
            return False
        url = search_marketplace(source_site, title)
    if not url or not valid(source_site, url):
        print(f"ATLANDI | {source}:{post_id} | ürün linki bulunamadı")
        return False
    live = marketplace_price(source_site, url)
    if not live:
        print(f"ATLANDI | {source}:{post_id} | canlı fiyat okunamadı")
        return False
    # Coupon prices may be lower than the normal marketplace price. Accept only when
    # the code's numeric suffix explains the exact gap (e.g. FISSLER1200 => 1,200 TL).
    reference = live
    if coupon and live > source_price:
        amount = code_amount(coupon)
        gap = live - source_price
        if amount is not None and abs(gap - amount) <= max(3.0, amount * 0.01):
            reference = live
        elif gap / live > 0.35:
            print(f"ATLANDI | {source}:{post_id} | kupon fiyatı doğrulanamadı | canlı={live:.2f} kaynak={source_price:.2f}")
            return False
        else:
            # Keep the live marketplace price as reference; source price is the coupon price.
            reference = live
    elif abs(live - source_price) / max(live, 1) > 0.05:
        print(f"ATLANDI | {source}:{post_id} | fiyat uyuşmazlığı | canlı={live:.2f} kaynak={source_price:.2f}")
        return False
    return post(source, post_id, source_site, url, title, source_price, reference, coupon)


def fetch_source(source, username):
    try:
        r = requests.get(f"https://t.me/s/{username}", headers=HEAD, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        boxes = soup.select("div.tgme_widget_message")
        print(f"OCR kaynak {source}: HTTP {r.status_code} foto={sum(1 for b in boxes if b.select_one('.tgme_widget_message_photo_wrap'))}")
        sent = 0
        for box in boxes:
            if process_message(source, box):
                sent += 1
        return sent
    except Exception as e:
        print(f"OCR kaynak {source}: HATA {e}")
        return 0


if __name__ == "__main__":
    print(f"=== Telegram görsel/OCR fırsat taraması başladı | yaş={MAX_AGE} dk ===")
    total = 0
    for source, username in SOURCES.items():
        total += fetch_source(source, username)
    print(f"=== OCR bitti. Gönderilen={total} ===")
