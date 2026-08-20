import os
import re
import html as htmlmod
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ['SUPABASE_SERVICE_KEY']
CHAT = '-1004424116637'
MAX_AGE_MINUTES = 20
MIN_DISCOUNT = 10.0
COOLDOWN = 12
MAX_PRODUCTS = 30

HEAD = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
}

SOURCES = {
    'OnuAl': 'onual_firsat',
    'EnesOzen': 'enesozen',
}

SHORT_HOSTS = {
    'app.hb.biz': 'Hepsiburada',
    'hps.im': 'Hepsiburada',
    'ty.gl': 'Trendyol',
    'tyml.gl': 'Trendyol',
    'link.amazon': 'Amazon',
    'amzn.to': 'Amazon',
    'amzn.eu': 'Amazon',
    'publicis.link': None,
    'onu.al': None,
}

MARKET_HOSTS = {
    'amazon.com.tr': 'Amazon',
    'hepsiburada.com': 'Hepsiburada',
    'trendyol.com': 'Trendyol',
}


def sb(method, path, **kwargs):
    h = {
        'apikey': KEY,
        'Authorization': f'Bearer {KEY}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    if method == 'POST':
        h['Prefer'] = 'return=representation'
    r = requests.request(method, f'{SB}/rest/v1/{path}', headers=h, timeout=15, **kwargs)
    r.raise_for_status()
    return r.json() if r.text else []


def price(v):
    s = re.sub(r'[^0-9,.]', '', str(v or '').replace('TL', '').replace('₺', '').replace(' ', ''))
    if not s:
        return None
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        a, b = s.rsplit(',', 1)
        s = a.replace('.', '') + '.' + b if len(b) <= 2 else s.replace(',', '')
    elif '.' in s:
        a, b = s.rsplit('.', 1)
        s = s.replace('.', '') if len(b) > 2 else s
    try:
        x = float(s)
        return x if 0 < x < 10000000 else None
    except Exception:
        return None


def money_values(text):
    out = []
    for m in re.finditer(r'(?<![A-ZÇĞİÖŞÜ])\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?', text or '', re.I):
        raw = m.group(0)
        end = text[m.end():m.end()+4]
        if re.match(r'\s*(?:TL|₺)', end, re.I):
            x = price(raw)
            if x is not None:
                out.append((m.start(), m.end(), x))
    return out


def extract_prices(text):
    vals = money_values(text)
    if not vals:
        return None, None

    # "1.800 TL yerine 1.299 TL" -> current=1.299, previous=1.800
    m = re.search(
        r'(?P<old>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)\s*(?:yerine|yerine)\s*'
        r'(?P<new>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',
        text, re.I)
    if m:
        return price(m.group('new')), price(m.group('old'))

    # "yeni 352 ... şuan 307 TL" gibi açık fiyat değişimleri.
    m = re.search(
        r'(?:yeni|önceki|onceki)\s*[:=]?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺).*?'
        r'(?:şuan|şu an|simdi|şimdi)\s*[:=]?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',
        text, re.I | re.S)
    if m:
        return price(m.group(2)), price(m.group(1))

    # İlk açık fiyat genellikle ilan edilen satış fiyatıdır.
    return vals[0][2], None


def site_from_url(url):
    host = urlparse(url).netloc.lower().replace('www.', '')
    if host in MARKET_HOSTS:
        return MARKET_HOSTS[host]
    for short, site in SHORT_HOSTS.items():
        if host == short or host.endswith('.' + short):
            return site
    return None


def valid_product_url(site, url):
    host = urlparse(url).netloc.lower().replace('www.', '')
    path = urlparse(url).path
    if site == 'Amazon':
        return host.endswith('amazon.com.tr') and bool(re.search(r'/(?:dp|gp/product)/[A-Z0-9]{8,}', path, re.I))
    if site == 'Hepsiburada':
        return host.endswith('hepsiburada.com') and bool(re.search(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)', path, re.I))
    if site == 'Trendyol':
        return host.endswith('trendyol.com') and bool(re.search(r'-p-\d+(?:[/?#&]|$)', path, re.I))
    return False


def resolve_url_requests(url):
    try:
        r = requests.get(url, headers=HEAD, timeout=12, allow_redirects=True)
        return r.url
    except Exception:
        return url


def resolve_url_playwright(page, url):
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=15000)
        page.wait_for_timeout(1000)
        return page.url
    except Exception:
        try:
            return page.url
        except Exception:
            return url


def resolve_link(page, href):
    resolved = resolve_url_requests(href)
    if site_from_url(resolved) and valid_product_url(site_from_url(resolved), resolved):
        return resolved
    host = urlparse(href).netloc.lower().replace('www.', '')
    if host in SHORT_HOSTS:
        resolved = resolve_url_playwright(page, href)
    return resolved


def clean_product_url(site, url):
    if not url:
        return None
    u = htmlmod.unescape(url).replace('\\/', '/').split('#', 1)[0].rstrip('/')
    if not valid_product_url(site, u):
        return None
    return u


def post_seen(key):
    rows = sb('GET', 'price_history', params={
        'select': 'recorded_at',
        'product_url': f'eq.telegram://{key}',
        'limit': '1',
    })
    return bool(rows)


def remember_post(key):
    sb('POST', 'price_history', json={
        'price': 0,
        'product_url': f'telegram://{key}',
        'site': 'telegram',
        'recorded_at': datetime.now(timezone.utc).isoformat(),
    })


def history(url):
    rows = sb('GET', 'price_history', params={
        'select': 'price,recorded_at',
        'product_url': f'eq.{url}',
        'order': 'recorded_at.desc',
        'limit': '50',
    })
    return [float(x['price']) for x in rows if float(x.get('price', 0) or 0) > 0]


def save_observation(site, url, title, current):
    now = datetime.now(timezone.utc).isoformat()
    rows = sb('GET', 'products', params={'select':'*','product_url':f'eq.{url}','limit':'1'})
    payload = {
        'product_name': title,
        'current_price': current,
        'previous_price': None,
        'product_url': url,
        'site': site,
        'updated_at': now,
    }
    if rows:
        sb('PATCH', f'products?id=eq.{rows[0]["id"]}', json=payload)
        return rows[0]
    return (sb('POST', 'products', json=payload) or [payload])[0]


def send_product(site, url, title, current, previous, source, post_url, signal):
    key = f'{source}:{post_url.rsplit("/",1)[-1]}'
    if post_seen(key):
        return False

    # Source açıkça eski/yeni fiyat vermişse yüzdeyi göster.
    disc = None
    if previous and previous > current:
        disc = (previous - current) / previous * 100
        if disc < MIN_DISCOUNT:
            remember_post(key)
            return False

    # Sadece "fiyat" yazan sıradan ürünleri değil, gerçekten fırsat sinyali taşıyanları paylaş.
    strong_signal = bool(previous and previous > current) or bool(re.search(
        r'son\s+(?:\d+\s+)?(?:gün|ay|yıl)|dip\s+fiyat|en\s+düşük|ortalama fiyatın|düştü|sepette|kupon|kod(?:u)?|'
        r'2\s*al\s*1|3\s*al\s*2|4\s*al\s*3|kampanya|indirim',
        signal, re.I))
    if not strong_signal:
        remember_post(key)
        return False

    now = datetime.now(timezone.utc).isoformat()
    row = save_observation(site, url, title, current)
    last = row.get('last_posted_at') if isinstance(row, dict) else None
    if last:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(last.replace('Z','+00:00')) < timedelta(hours=COOLDOWN):
                remember_post(key)
                return False
        except Exception:
            pass

    lines = []
    if disc is not None:
        lines += [f'🔥 %{disc:.0f} İNDİRİM', '', title, '', f'💰 {current:,.2f} TL', f'🏷️ Önce: {previous:,.2f} TL']
    else:
        lines += ['🔥 SICAK FIRSAT', '', title, '', f'💰 {current:,.2f} TL']
    lines += [f'🛍️ {site}', f'🔗 {url}']
    if signal.strip():
        compact = re.sub(r'\s+', ' ', signal).strip()
        if len(compact) > 260:
            compact = compact[:257] + '...'
        lines += ['', f'📌 {compact}']
    lines += ['', f'Kaynak: {source}']

    requests.post(
        'https://api.telegram.org/bot' + TOKEN + '/sendMessage',
        json={'chat_id': CHAT, 'text': '\n'.join(lines), 'disable_web_page_preview': False},
        timeout=15,
    ).raise_for_status()
    sb('PATCH', f'products?id=eq.{row["id"]}', json={'last_posted_at': now})
    remember_post(key)
    print(f'GÖNDERİLDİ | {site} | {title[:70]} | {current:.2f} | kaynak={source}')
    return True


def parse_message(block, source, page):
    tm = block.select_one('time[datetime]')
    if not tm:
        return None
    try:
        dt = datetime.fromisoformat(tm['datetime'].replace('Z', '+00:00'))
    except Exception:
        return None
    age = datetime.now(timezone.utc) - dt
    if age < timedelta(0) or age > timedelta(minutes=MAX_AGE_MINUTES):
        return None

    text_node = block.select_one('.tgme_widget_message_text')
    if not text_node:
        return None
    raw = text_node.get_text('\n', strip=True)

    links = []
    for a in text_node.select('a[href]'):
        href = htmlmod.unescape(a.get('href', '')).replace('\\/', '/')
        if not href.startswith('http'):
            continue
        host = urlparse(href).netloc.lower().replace('www.', '')
        if host not in SHORT_HOSTS and host not in MARKET_HOSTS:
            continue
        resolved = resolve_link(page, href)
        site = site_from_url(resolved) or site_from_url(href)
        if site and valid_product_url(site, resolved):
            clean = clean_product_url(site, resolved)
            if clean and clean not in [x[1] for x in links]:
                links.append((site, clean))

    if not links:
        return None

    # Mesajda açık mağaza adı yoksa çözülen linkten mağazayı belirleriz.
    site, url = links[0]
    current, previous = extract_prices(raw)
    if current is None:
        return None

    # Başlık: ilk anlamlı satır; emoji ve işaretleri temizle.
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    title = 'Ürün'
    for line in lines:
        if re.search(r'(TL|₺|yerine|kupon|kod(?:u)?|sepette|indirim|kampanya)', line, re.I):
            continue
        if line.startswith(('http://', 'https://', '#')):
            continue
        title = re.sub(r'^[✅🔥🛍️🎁📌🏷️💬⚙️🟣🟠🟤👟🕶️☕️]+\s*', '', line).strip()
        if len(title) >= 4:
            break

    post_id = block.get('data-post', '').split('/')[-1]
    post_url = f'https://t.me/{source}/{post_id}' if post_id.isdigit() else f'https://t.me/s/{source}'
    signal_parts = []
    for line in lines[1:]:
        if re.search(r'son\s+(?:\d+\s+)?(?:gün|ay|yıl)|dip|en düşük|ortalama|düştü|sepette|kupon|kod(?:u)?|al\s*\d|kampanya|indirim|aktif', line, re.I):
            signal_parts.append(line)
    signal = ' | '.join(signal_parts[:3])
    return site, url, title[:300], current, previous, dt, post_url, signal


def scan_source(name, channel, browser):
    url = f'https://t.me/s/{channel}'
    try:
        r = requests.get(url, headers=HEAD, timeout=20)
        print(f'Telegram kaynak {name}: HTTP {r.status_code}')
        if r.status_code >= 400:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        page = browser.new_page()
        page.set_default_timeout(15000)
        out = []
        try:
            for block in soup.select('.tgme_widget_message'):
                item = parse_message(block, channel, page)
                if item:
                    out.append((name, channel, item))
                if len(out) >= MAX_PRODUCTS:
                    break
        finally:
            page.close()
        return out
    except Exception as e:
        print(f'Telegram kaynak hata {name}: {type(e).__name__}: {e}')
        return []


def main():
    print('=== Telegram fırsat keşfi başladı ===')
    sent = 0
    seen_urls = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            for name, channel in SOURCES.items():
                for source, channel_name, item in scan_source(name, channel, browser):
                    site, url, title, current, previous, dt, post_url, signal = item
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    print(f'Aday: {site} | {title[:80]} | {current:.2f} TL | kaynak={source}')
                    try:
                        if send_product(site, url, title, current, previous, source, post_url, signal):
                            sent += 1
                    except Exception as e:
                        print(f'Ürün işlem hata: {type(e).__name__}: {e}')
        finally:
            browser.close()
    print(f'=== Telegram fırsat keşfi bitti. Aday={len(seen_urls)} Gönderilen={sent} ===')


if __name__ == '__main__':
    main()
