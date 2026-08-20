import re
from urllib.parse import quote
from bs4 import BeautifulSoup
import requests
import bot

_original_product_page = bot.product_page


def _clean_name(value):
    return re.sub(r'\s+', ' ', value or 'Ürün').strip()[:300]


def _amazon_price_values(page, html):
    values = []
    full_selectors = [
        '.a-price .a-offscreen', '#corePrice_feature_div .a-offscreen',
        '.apexPriceToPay .a-offscreen', '#priceblock_ourprice',
        '#priceblock_dealprice', '#priceblock_saleprice',
        'meta[property="product:price:amount"]', 'meta[itemprop="price"]',
    ]
    for sel in full_selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 10)):
                raw = loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=400)
                v = bot.price(raw)
                if v is not None and 0 < v < 10000000:
                    values.append(v)
        except Exception:
            pass

    raw_patterns = [
        r'"priceToPay"\s*:\s*\{[^{}]{0,500}?"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"priceAmount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"ourPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"currentPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"displayPrice"\s*:\s*"([^" ]+)"',
    ]
    for pat in raw_patterns:
        try:
            for m in re.finditer(pat, html or '', re.I):
                v = bot.price(m.group(1))
                if v is not None and 0 < v < 10000000:
                    values.append(v)
        except Exception:
            pass

    try:
        _, jd_prices = bot.jsonld_all(html)
        values.extend(v for v in jd_prices if 0 < v < 10000000)
    except Exception:
        pass

    if not values:
        try:
            whole_loc = page.locator('.a-price-whole')
            frac_loc = page.locator('.a-price-fraction')
            for i in range(min(whole_loc.count(), 10)):
                whole = whole_loc.nth(i).inner_text(timeout=400).strip()
                frac = frac_loc.nth(i).inner_text(timeout=400).strip() if i < frac_loc.count() else '00'
                whole_digits = re.sub(r'[^0-9]', '', whole)
                frac_digits = re.sub(r'[^0-9]', '', frac)[:2].ljust(2, '0')
                if whole_digits:
                    v = float(f'{whole_digits}.{frac_digits}')
                    if 0 < v < 10000000:
                        values.append(v)
        except Exception:
            pass
    return list(dict.fromkeys(values))


def _amazon_previous_prices(page, html, current):
    values = []
    for sel in ['.a-text-price .a-offscreen', '.priceBlockStrikePriceString', '.basisPrice .a-offscreen', '#priceblock_listprice']:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 10)):
                v = bot.price(loc.nth(i).inner_text(timeout=400))
                if v is not None and current * 1.03 < v < 10000000:
                    values.append(v)
        except Exception:
            pass
    for pat in [
        r'"listPrice"\s*:\s*\{[^{}]{0,500}?"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"basisPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"wasPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
    ]:
        try:
            for m in re.finditer(pat, html or '', re.I):
                v = bot.price(m.group(1))
                if v is not None and current * 1.03 < v < 10000000:
                    values.append(v)
        except Exception:
            pass
    try:
        _, jd_prices = bot.jsonld_all(html)
        values.extend(v for v in jd_prices if current * 1.03 < v < 10000000)
    except Exception:
        pass
    return list(dict.fromkeys(values))


def _amazon_search_prices(url, title):
    m = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Za-z0-9]{8,})', url or '')
    asin = m.group(1) if m else None
    queries = [f'site:amazon.com.tr {asin} TL'] if asin else []
    if title:
        queries.append(f'site:amazon.com.tr "{title[:100]}" TL')
    for qtext in queries:
        try:
            q = quote(qtext)
            r = requests.get(f'https://www.google.com/search?q={q}&num=10', headers=bot.HEADERS, timeout=10)
            if r.status_code >= 400:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            found = []
            blocks = soup.select('div.MjjYud') or soup.find_all('a', href=True)
            for block in blocks:
                text = block.get_text(' ', strip=True) if hasattr(block, 'get_text') else str(block)
                if asin and asin.lower() not in (text + ' ' + str(block)).lower():
                    continue
                found.extend(bot.prices(text))
            found = sorted(set(v for v in found if 0 < v < 10000000))
            if found:
                cur = found[0]
                prev = next((v for v in found if v > cur * 1.03), None)
                print(f'Amazon arama son çare fiyatları: {found[:8]}')
                return cur, prev
        except Exception as e:
            print(f'Amazon arama fiyatı hata: {type(e).__name__}: {e}')
    return None, None


def _jsonld_product_name(html):
    try:
        name, _ = bot.jsonld_all(html)
        return name
    except Exception:
        return None


def product_page(site, url, title, browser, search_ps=None):
    if site != 'Amazon':
        return _original_product_page(site, url, title, browser, search_ps)
    page = browser.new_page()
    page.set_default_timeout(4500)
    page.set_default_navigation_timeout(15000)
    try:
        r = page.goto(url, wait_until='domcontentloaded')
        status = r.status if r else 0
        print(f'{site} ürün HTTP: {status} | {url}')
        if not r or status >= 400:
            cur, prev = _amazon_search_prices(url, title)
            if cur:
                return {'name': _clean_name(title), 'price': cur, 'previous': prev, 'url': bot.canonical(url), 'site': site}
            return None

        page.wait_for_timeout(1500)
        html = page.content()
        current = _amazon_price_values(page, html)
        if not current:
            print(f'{site} DOM/HTML fiyatı bulunamadı, arama son çare deneniyor | {url}')
            cur, prev = _amazon_search_prices(url, title)
            if cur:
                return {'name': _clean_name(title), 'price': cur, 'previous': prev, 'url': bot.canonical(url), 'site': site}
            print(f'{site} güvenilir tam fiyat bulunamadı | {url}')
            return None

        cur = current[0]
        previous = _amazon_previous_prices(page, html, cur)
        prev = min(previous) if previous else None
        name = _jsonld_product_name(html) or title or 'Ürün'
        try:
            og = page.locator('meta[property="og:title"]').get_attribute('content')
            if og:
                name = og
        except Exception:
            pass
        print(f'{site} güvenilir fiyatlar: {[round(x, 2) for x in current[:8]]}')
        print(f'{site} fiyat: {cur:.2f} | önceki: {prev or 0:.2f}')
        return {'name': _clean_name(name), 'price': cur, 'previous': prev, 'url': bot.canonical(url), 'site': site}
    except Exception as e:
        print(f'{site} ürün hata: {type(e).__name__}: {e}')
        return None
    finally:
        page.close()


bot.product_page = product_page
# run_bot.py yalnızca Amazon'u çalıştırır. HB/Trendyol ayrı marketplaces.py ile çalışır.
bot.SEEDS = {'Amazon':'https://www.amazon.com.tr/gp/goldbox'}
bot.main()
