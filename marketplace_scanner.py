import os
import re
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlparse, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ['SUPABASE_SERVICE_KEY']
CHAT = '-1004424116637'
MIN_DISCOUNT = 10.0
COOLDOWN = 12
AMAZON_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', '').strip()
HEAD = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36', 'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8'}

TRACKING = {'utm_source','utm_medium','utm_campaign','utm_content','utm_term','fbclid','gclid','ref','ref_','tag','ascsubtag','linkcode','creative','creativeasin','camp','adid','dib','dib_tag','pd_rd_i','pd_rd_r','pd_rd_w','pd_rd_wg','pf_rd_i','pf_rd_m','pf_rd_p','pf_rd_r','pf_rd_s','pf_rd_t','_encoding','aff_fcid','aff_fsk','aff_platform','aff_trace_key','spm','partner_id'}
MARKETS = {
    'Amazon': ('https://www.amazon.com.tr/s?k=', 'amazon.com.tr', re.compile(r'/(?:dp|gp/product)/[A-Z0-9]{8,}', re.I)),
    'Hepsiburada': ('https://www.hepsiburada.com/ara?q=', 'hepsiburada.com', re.compile(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)', re.I)),
    'Trendyol': ('https://www.trendyol.com/sr?q=', 'trendyol.com', re.compile(r'-p-\d+(?:[/?#&]|$)', re.I)),
}
# Her üç pazaryerini gerçekten tarayacak kısa ama çeşitli arama seti.
QUERIES = ['indirimli elektronik','telefon kulaklık','televizyon ev yaşam','mutfak kişisel bakım']
BOOK_RE = re.compile(r'\b(kitap|roman|dergi|e-?kitap|yayınevi|yayıncılık|paperback|hardcover|ciltli|kitabevi)\b', re.I)
MONEY_RE = re.compile(r'(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])', re.I)
BAD_PRICE_CONTEXT = re.compile(r'(kupon|kod|kazan[çc]|avantaj|indirim|tasarruf|kargo|shipping|aylık|ayda|/ay|x\s*ay|taksit|puan|cashback|bonus|hediye)', re.I)


def sb(method, path, **kw):
    headers = {'apikey': KEY, 'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json', 'Accept': 'application/json'}
    if method == 'POST': headers['Prefer'] = 'return=representation'
    r = requests.request(method, f'{SB}/rest/v1/{path}', headers=headers, timeout=10, **kw)
    r.raise_for_status()
    return r.json() if r.text else []


def money(value):
    s = re.sub(r'[^0-9,.]', '', str(value))
    if not s: return None
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        a, b = s.rsplit(',', 1); s = a.replace('.', '') + '.' + b if len(b) <= 2 else s.replace(',', '')
    elif '.' in s:
        a, b = s.rsplit('.', 1); s = s.replace('.', '') if len(b) > 2 else s
    try:
        x = float(s); return x if 0 < x < 10000000 else None
    except Exception: return None


def prices(text):
    result = []
    for m in MONEY_RE.finditer(text or ''):
        x = money(m.group())
        if not x: continue
        context = text[max(0, m.start()-42):min(len(text), m.end()+42)]
        if BAD_PRICE_CONTEXT.search(context): continue
        result.append(x)
    return result


def normalize(site, url):
    p = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING]
    if site == 'Amazon' and AMAZON_TAG: query.append(('tag', AMAZON_TAG))
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(query, doseq=True), ''))


def valid(site, url):
    try:
        p = urlparse(url)
        return p.netloc.lower().replace('www.', '').endswith(MARKETS[site][1]) and bool(MARKETS[site][2].search(p.path))
    except Exception: return False


def clean_title(text):
    text = ' '.join((text or '').split())
    text = re.sub(r'\b(?:Sepete ekle|Hızlı Teslimat|Çok Satan|Sponsorlu|Reklam)\b', ' ', text, flags=re.I)
    return re.sub(r'\s{2,}', ' ', text).strip()[:220]


def extract_search_candidates(page, site, query):
    page.goto(MARKETS[site][0] + quote(query), wait_until='domcontentloaded', timeout=8000)
    page.wait_for_timeout(350)
    raw = page.locator('a[href]').evaluate_all("""els => els.map(a => { let p=a, card=''; for(let i=0;i<6&&p;i++,p=p.parentElement){let t=(p.innerText||'').replace(/\\s+/g,' ').trim(); if((t.match(/(?:TL|₺)/gi)||[]).length>=2){card=t;break}} return {href:a.href,text:(a.innerText||'').trim(),card}; })""")
    out, seen = [], set()
    for item in raw:
        url, text, card = item.get('href') or '', item.get('text') or '', item.get('card') or ''
        if BOOK_RE.search(text + ' ' + card) or not valid(site, url): continue
        url = normalize(site, url)
        if url in seen: continue
        seen.add(url)
        vals = prices(card)
        if len(vals) < 2: continue
        current, previous = min(vals), max(vals)
        if previous <= current: continue
        discount = (previous-current)/previous*100
        if discount < MIN_DISCOUNT: continue
        title = clean_title(text) if len(clean_title(text)) >= 10 else clean_title(card)
        if len(title) >= 10 and not BOOK_RE.search(title):
            out.append((url, title, current, previous))
        if len(out) >= 4: break
    print(f'BAĞIMSIZ ARAMA | {site} | "{query}" | aday={len(out)}')
    return out


def history(site, url, current):
    try:
        rows = sb('GET', 'price_history', params={'select':'price,recorded_at','product_url':f'eq.{url}','site':f'eq.{site}','order':'recorded_at.desc','limit':'30'})
        vals = [float(r['price']) for r in rows if r.get('price') is not None and float(r['price']) > current]
        return min(vals) if vals else None
    except Exception as e:
        print('HISTORY HATA', site, e); return None


def record_price(site, url, current):
    try: sb('POST', 'price_history', json={'price': current, 'product_url': url, 'site': site, 'recorded_at': datetime.now(timezone.utc).isoformat()})
    except Exception as e: print('HISTORY KAYIT HATASI', site, e)


def verify(page, site, url, fallback_title, expected_current, candidate_previous):
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=7000)
        page.wait_for_timeout(250)
        soup = BeautifulSoup(page.content(), 'html.parser')
        page_text = soup.get_text(' ', strip=True)
        if BOOK_RE.search((fallback_title or '') + ' ' + page_text[:5000]): return None
        title_el = soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]')
        title = title_el.get('content', '').strip() if title_el else (soup.title.get_text(' ', strip=True) if soup.title else fallback_title)
        image_el = soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]')
        image = image_el.get('content', '').strip() if image_el else None
        vals = []
        for selector in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:
            for element in soup.select(selector):
                x = money(element.get('content') or element.get('value') or element.get('data-price') or element.get_text(' ', strip=True))
                if x: vals.append(x)
        if not vals: vals = prices(page_text)[:50]
        if not vals: return None
        plausible = [x for x in vals if x >= max(1, expected_current*0.50)] or vals
        current = min(plausible, key=lambda x: abs(x-expected_current))
        if abs(current-expected_current)/max(expected_current,1) > 0.35:
            print(f'VERIFY FİYAT UYUŞMAZ | {site} | beklenen={expected_current:.2f} | sayfa={current:.2f}')
            return None
        old = history(site, url, current)
        # İlk karşılaşmada geçmiş yoksa ürün sonsuza kadar bekletilmiyor:
        # pazaryerinin arama kartındaki önceki fiyat güvenilir referans olarak kullanılıyor.
        previous = old if old else candidate_previous
        if not previous or previous <= current:
            record_price(site, url, current); return None
        if previous/current > 4.0:
            print(f'VERIFY ESKİ FİYAT ŞÜPHELİ | {site} | mevcut={current:.2f} | eski={previous:.2f}')
            record_price(site, url, current); return None
        discount = (previous-current)/previous*100
        record_price(site, url, current)
        if discount < MIN_DISCOUNT: return None
        return clean_title(title), current, previous, discount, image
    except Exception as e:
        print('VERIFY HATA', site, url, e); return None


def send(site, url, title, current, previous, discount, image):
    rows = sb('GET', 'products', params={'select':'*','product_url':f'eq.{url}','limit':'1'})
    row = rows[0] if rows else None
    last = row.get('last_posted_at') if isinstance(row, dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00')) < timedelta(hours=COOLDOWN): return False
        except Exception: pass
    fmt = lambda x: f'{x:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.')
    text = f'⭐️ BOTUN BULDUĞU FIRSAT\n\n🔥 %{discount:.0f} İNDİRİM\n\n🛍️ {title}\n💰 {fmt(current)}\n🏷️ Önceki: {fmt(previous)}\n\n👇 Fırsata git'
    keyboard = {'inline_keyboard': [[{'text':'🛒 FIRSATA GİT','url':url}]]}
    try:
        sent = False
        if image:
            r = requests.post(f'https://api.telegram.org/bot{TOKEN}/sendPhoto', json={'chat_id':CHAT,'photo':image,'caption':text,'reply_markup':keyboard}, timeout=10)
            sent = r.ok
        if not sent:
            requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', json={'chat_id':CHAT,'text':text,'disable_web_page_preview':False,'reply_markup':keyboard}, timeout=10).raise_for_status()
    except Exception as e:
        print('GÖNDERME HATASI', site, url, e); return False
    now = datetime.now(timezone.utc).isoformat()
    if row and row.get('id'):
        sb('PATCH', f'products?id=eq.{row["id"]}', json={'last_posted_at':now,'current_price':current,'previous_price':previous,'updated_at':now})
    else:
        sb('POST', 'products', json={'product_name':title,'current_price':current,'previous_price':previous,'product_url':url,'site':site,'updated_at':now,'last_posted_at':now})
    print(f'⭐️ BAĞIMSIZ FIRSAT | {site} | %{discount:.1f} | {current:.2f} TL | {title[:90]}')
    return True


def main():
    total = candidates_total = verified_total = 0
    seen_urls = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEAD['User-Agent'], locale='tr-TR')
        detail = browser.new_page(user_agent=HEAD['User-Agent'], locale='tr-TR')
        for site in MARKETS:
            for query in QUERIES:
                try:
                    candidates = extract_search_candidates(page, site, query)
                    candidates_total += len(candidates)
                    for url, title, current, previous in candidates:
                        if url in seen_urls: continue
                        seen_urls.add(url)
                        verified = verify(detail, site, url, title, current, previous)
                        if verified:
                            verified_total += 1
                            t, c, p, d, img = verified
                            if send(site, url, t, c, p, d, img): total += 1
                except Exception as e:
                    print('ARAMA HATA', site, query, e)
        browser.close()
    print(f'Bağımsız tarama tamamlandı | aday={candidates_total} doğrulanan={verified_total} gönderilen={total}')


if __name__ == '__main__': main()
