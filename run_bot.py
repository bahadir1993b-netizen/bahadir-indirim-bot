import re
from urllib.parse import quote
from bs4 import BeautifulSoup
import requests
import bot

_original_product_page = bot.product_page


def _clean_name(value):
    return re.sub(r'\s+', ' ', value or 'Ürün').strip()[:300]


def _valid_price(v):
    return v is not None and 0 < v < 10000000


def _first_price(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 15)):
                raw = loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=500)
                v = bot.price(raw)
                if _valid_price(v):
                    return v
        except Exception:
            pass
    return None


def _amazon_price_values(page, html):
    selectors = [
        '#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen',
        '#corePrice_feature_div .priceToPay .a-offscreen',
        '#corePriceDisplay_desktop_feature_div .a-price .a-offscreen',
        '#corePrice_feature_div .a-price .a-offscreen',
        '#apex_desktop .priceToPay .a-offscreen',
        '#apex_desktop .a-price .a-offscreen',
        '.reinventPricePriceToPayMargin .a-offscreen',
        '.apexPriceToPay .a-offscreen',
        '#price_inside_buybox', '#newBuyBoxPrice',
        '#priceblock_dealprice', '#priceblock_ourprice', '#priceblock_saleprice',
        'meta[property="product:price:amount"]', 'meta[itemprop="price"]',
    ]
    v = _first_price(page, selectors)
    if _valid_price(v):
        return [v]

    soup = BeautifulSoup(html or '', 'html.parser')
    for tag in soup.select('input[name="items[0.base][customerVisiblePrice][amount]"], input[name*="customerVisiblePrice"][name*="amount"], input[name="displayedPrice"]'):
        v = bot.price(tag.get('value'))
        if _valid_price(v):
            return [v]

    patterns = [
        r'"priceToPay"\s*:\s*\{.{0,1800}?"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"priceToPay"\s*:\s*\{.{0,1800}?"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"customerVisiblePrice"\s*:\s*\{.{0,1200}?"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"ourPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"currentPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"priceAmount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
    ]
    flat = (html or '').replace('\n', ' ')
    for pat in patterns:
        m = re.search(pat, flat, re.I | re.S)
        if m:
            v = bot.price(m.group(1))
            if _valid_price(v):
                return [v]

    try:
        whole_loc = page.locator('.priceToPay .a-price-whole, #corePrice_feature_div .a-price-whole, #apex_desktop .a-price-whole')
        for i in range(min(whole_loc.count(), 10)):
            whole = re.sub(r'[^0-9]', '', whole_loc.nth(i).inner_text(timeout=500))
            if whole:
                v = float(whole)
                if _valid_price(v):
                    return [v]
    except Exception:
        pass
    return []


def _amazon_previous_prices(page, html, current):
    values = []
    for sel in ['.a-text-price .a-offscreen', '.priceBlockStrikePriceString', '.basisPrice .a-offscreen', '#priceblock_listprice']:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 10)):
                v = bot.price(loc.nth(i).inner_text(timeout=500))
                if _valid_price(v) and current * 1.03 < v:
                    values.append(v)
        except Exception:
            pass
    return list(dict.fromkeys(values))


def _search_exact_amazon_price(query, asin):
    engines = [f'https://www.google.com/search?q={quote(query)}&num=10&filter=0', f'https://www.bing.com/search?q={quote(query)}&count=10']
    asin = (asin or '').upper()
    for url in engines:
        try:
            r = requests.get(url, headers=bot.HEADERS, timeout=12)
            if r.status_code >= 400: continue
            soup = BeautifulSoup(r.text, 'html.parser')
            for block in soup.select('li.b_algo, div.MjjYud, div.g, article'):
                text = block.get_text(' ', strip=True)
                links = [a.get('href', '') for a in block.select('a[href]')]
                blob = (text + ' ' + ' '.join(links)).upper()
                if asin not in blob or 'AMAZON.COM.TR' not in blob: continue
                vals = sorted(set(v for v in bot.prices(text) if _valid_price(v)))
                if vals: return vals[0], next((v for v in vals if v > vals[0] * 1.03), None)
        except Exception: pass
    return None, None


def _amazon_search_prices(url, title):
    m = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Za-z0-9]{8,})', url or '')
    asin = m.group(1) if m else None
    if not asin: return None, None
    for q in [f'site:amazon.com.tr {asin} TL', f'"{asin}" Amazon Türkiye fiyat', f'site:amazon.com.tr "{title[:100]}" {asin}']:
        cur, prev = _search_exact_amazon_price(q, asin)
        if cur is not None: return cur, prev
    return None, None


def _jsonld_product(html):
    try:
        name, vals = bot.jsonld_all(html)
        vals = [v for v in vals if _valid_price(v)]
        return name, vals
    except Exception:
        return None, []


def product_page(site, url, title, browser, search_ps=None):
    if site != 'Amazon': return _original_product_page(site, url, title, browser, search_ps)
    page = browser.new_page(locale='tr-TR')
    page.set_default_timeout(6000); page.set_default_navigation_timeout(20000)
    try:
        r = page.goto(url, wait_until='domcontentloaded')
        status = r.status if r else 0
        print(f'{site} ürün HTTP: {status} | {url}')
        if r and status < 400:
            page.wait_for_timeout(2500)
            html = page.content()
            current = _amazon_price_values(page, html)
            jd_name, jd_vals = _jsonld_product(html)
            if not current and jd_vals:
                current = [min(jd_vals)]
                print(f'{site} JSON-LD fiyatı kullanıldı: {current[0]:.2f}')
            if current:
                cur = current[0]
                previous = _amazon_previous_prices(page, html, cur)
                prev = min(previous) if previous else None
                name = jd_name or title or 'Ürün'
                try:
                    og = page.locator('meta[property="og:title"]').get_attribute('content')
                    if og: name = og
                except Exception: pass
                print(f'{site} güvenilir güncel fiyat: {cur:.2f}')
                return {'name': _clean_name(name), 'price': cur, 'previous': prev, 'url': bot.canonical(url), 'site': site}
            try:
                body = page.locator('body').inner_text(timeout=3000)
                print(f'{site} fiyat debug: {body[:220].replace(chr(10), " ")}')
            except Exception: pass
        print(f'{site} DOM/HTML fiyatı bulunamadı; exact ASIN araması deneniyor | {url}')
        cur, prev = _amazon_search_prices(url, title)
        if cur:
            return {'name': _clean_name(title), 'price': cur, 'previous': prev, 'url': bot.canonical(url), 'site': site}
        print(f'{site} güvenilir güncel fiyat bulunamadı | {url}')
        return None
    except Exception as e:
        print(f'{site} ürün hata: {type(e).__name__}: {e}')
        return None
    finally:
        page.close()


def process_with_real_history(p):
    try:
        rows = bot.sb('GET', 'products', params={'select': '*', 'product_url': f'eq.{p["url"]}', 'limit': '1'})
        now = bot.datetime.now(bot.timezone.utc).isoformat(); old = bot.history(p['url']); previous_observed = old[0] if old else None
        print(f'Kontrol: {p["site"]} | mevcut={p["price"]:.2f} | son gözlenen={previous_observed or 0:.2f} | geçmiş={len(old)}')
        payload = {'product_name': p['name'], 'current_price': p['price'], 'previous_price': previous_observed, 'product_url': p['url'], 'site': p['site'], 'updated_at': now}
        if rows:
            row = rows[0]; bot.sb('PATCH', f'products?id=eq.{row["id"]}', json=payload)
        else:
            row = (bot.sb('POST', 'products', json=payload) or [payload])[0]
        bot.sb('POST', 'price_history', json={'price': p['price'], 'product_url': p['url'], 'site': p['site'], 'recorded_at': now})
        if previous_observed is None or previous_observed <= p['price']: return False
        disc = (previous_observed - p['price']) / previous_observed * 100
        if disc < bot.MIN_DISCOUNT: return False
        last = row.get('last_posted_at') if isinstance(row, dict) else None
        if last:
            try:
                if bot.datetime.now(bot.timezone.utc) - bot.datetime.fromisoformat(last.replace('Z', '+00:00')) < bot.timedelta(hours=bot.COOLDOWN): return False
            except Exception: pass
        msg = f'🔥 %{disc:.0f} İNDİRİM\n\n{p["name"]}\n\n💰 {p["price"]:,.2f} TL\n🏷️ Önce: {previous_observed:,.2f} TL\n🛍️ {p["site"]}\n🔗 {p["url"]}'
        r = requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage', json={'chat_id': bot.CHANNEL_ID, 'text': msg}, timeout=8)
        print(f'Telegram gönderim HTTP: {r.status_code} | {r.text[:200]}')
        if r.ok and isinstance(row, dict) and row.get('id'):
            bot.sb('PATCH', f'products?id=eq.{row["id"]}', json={'last_posted_at': now, 'last_posted_price': p['price']})
        return r.ok
    except Exception as e:
        print(f'işlem hata: {type(e).__name__}: {e}')
        return False


bot.product_page = product_page
bot.process = process_with_real_history
bot.SEEDS = {'Amazon': 'https://www.amazon.com.tr/gp/goldbox'}
bot.main()
