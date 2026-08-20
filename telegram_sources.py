import re
import html as htmlmod
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from marketplaces_v4 import product_page, process, MAX_PRODUCTS

HEAD = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
}

SOURCES = {
    'OnuAl': 'https://t.me/s/onual_firsat',
    'EnesOzen': 'https://t.me/s/enesozen',
}
MARKET_HOSTS = ('amazon.com.tr', 'hepsiburada.com', 'trendyol.com')
SHORT_HOSTS = ('app.hb.biz', 'hps.im', 'amzn.eu', 'amzn.to', 'tinyurl.com', 'ty.gl', 'onu.al', 'sl.n11.com', 'publicis.link')
SITE_BY_HOST = {
    'amazon.com.tr': 'Amazon',
    'hepsiburada.com': 'Hepsiburada',
    'trendyol.com': 'Trendyol',
}

def resolve_url(url):
    try:
        r = requests.get(url, headers=HEAD, timeout=15, allow_redirects=True)
        return r.url
    except Exception as e:
        print(f'Link çözme hata: {url} | {type(e).__name__}: {e}')
        return url

def site_from_url(url):
    host = urlparse(url).netloc.lower().replace('www.', '')
    for h, site in SITE_BY_HOST.items():
        if host == h or host.endswith('.' + h):
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

def extract_links(source, html):
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    seen = set()
    for a in soup.select('a[href]'):
        href = htmlmod.unescape(a.get('href', '')).replace('\\/', '/')
        href = urljoin(source, href)
        if not href.startswith('http'):
            continue
        host = urlparse(href).netloc.lower().replace('www.', '')
        if host not in MARKET_HOSTS and not any(host == x or host.endswith('.' + x) for x in SHORT_HOSTS):
            continue
        resolved = resolve_url(href)
        site = site_from_url(resolved)
        if not site or not valid_product_url(site, resolved):
            continue
        clean = resolved.split('#', 1)[0].rstrip('/')
        if clean not in seen:
            seen.add(clean)
            title = a.get_text(' ', strip=True) or 'Ürün'
            out.append((site, clean, title[:300]))
        if len(out) >= MAX_PRODUCTS * 3:
            break
    return out

def scan_source(name, url):
    try:
        r = requests.get(url, headers=HEAD, timeout=20)
        print(f'Telegram kaynak {name}: HTTP {r.status_code}')
        if r.status_code >= 400:
            return []
        return extract_links(url, r.text)
    except Exception as e:
        print(f'Telegram kaynak {name} hata: {type(e).__name__}: {e}')
        return []

def main():
    print('=== Telegram kaynak keşfi başladı ===')
    candidates = []
    seen = set()
    for name, url in SOURCES.items():
        for site, product_url, title in scan_source(name, url):
            key = (site, product_url)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((site, product_url, title, name))
            print(f'Aday: {site} | {title[:80]} | {product_url} | kaynak={name}')
            if len(candidates) >= MAX_PRODUCTS * 2:
                break

    sent = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        for site, url, title, source in candidates:
            if site not in ('Hepsiburada', 'Trendyol'):
                # Amazon mevcut ayrı akışta doğrulanıyor; burada HB/Trendyol'a odaklanıyoruz.
                continue
            p = product_page(site, url, title, [], browser)
            if not p:
                continue
            try:
                if process(p, site):
                    sent += 1
                    print(f'Gönderildi: {site} | {p["title"][:80]} | kaynak={source}')
            except Exception as e:
                print(f'İşlem hata: {type(e).__name__}: {e}')
        browser.close()
    print(f'=== Telegram kaynak keşfi bitti. Aday={len(candidates)} Gönderilen={sent} ===')

if __name__ == '__main__':
    main()
