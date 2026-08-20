import re
from urllib.parse import quote
from bs4 import BeautifulSoup
import requests
import bot

_original_product_page = bot.product_page


def _clean_name(value):
    return re.sub(r'\s+', ' ', value or 'Ürün').strip()[:300]


def _first_price(page, selectors):
    """Amazon'da ilk güvenilir CURRENT-price kaynağını seç; tüm fiyatları karıştırma."""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 10)):
                raw = loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=500)
                v = bot.price(raw)
                if v is not None and 0 < v < 10000000:
                    return v
        except Exception:
            pass
    return None


def _amazon_price_values(page, html):
    # Öncelik sırası önemli: strike/list fiyatları ve JSON-LD fiyatlarını current diye seçme.
    current_selectors = [
        '#corePrice_feature_div .a-price .a-offscreen',
        '#corePrice_feature_div .a-offscreen',
        '.apexPriceToPay .a-offscreen',
        '#priceblock_dealprice', '#priceblock_ourprice', '#priceblock_saleprice',
        'meta[property="product:price:amount"]', 'meta[itemprop="price"]',
    ]
    v = _first_price(page, current_selectors)
    if v is not None:
        return [v]

    # Amazon'un dinamik JSON alanlarında yalnızca açıkça current olan fiyatları ara.
    patterns = [
        r'"priceToPay"\s*:\s*\{[^{}]{0,700}?"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"ourPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"currentPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"priceAmount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
    ]
    for pat in patterns:
        m = re.search(pat, html or '', re.I)
        if m:
            x = bot.price(m.group(1))
            if x is not None and 0 < x < 10000000:
                return [x]

    # Son çare: whole+fraction çifti. Fraction tek başına ASLA fiyat kabul edilmez.
    try:
        whole_loc = page.locator('.a-price-whole')
        frac_loc = page.locator('.a-price-fraction')
        for i in range(min(whole_loc.count(), 10)):
            whole = whole_loc.nth(i).inner_text(timeout=500)
            frac = frac_loc.nth(i).inner_text(timeout=500) if i < frac_loc.count() else '00'
            whole_digits = re.sub(r'[^0-9]', '', whole)
            frac_digits = re.sub(r'[^0-9]', '', frac)[:2].ljust(2, '0')
            if whole_digits:
                x = float(f'{whole_digits}.{frac_digits}')
                if 0 < x < 10000000:
                    return [x]
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
                if v is not None and current * 1.03 < v < 10000000:
                    values.append(v)
        except Exception:
            pass
    for pat in [
        r'"listPrice"\s*:\s*\{[^{}]{0,700}?"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"basisPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"wasPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
    ]:
        for m in re.finditer(pat, html or '', re.I):
            v = bot.price(m.group(1))
            if v is not None and current * 1.03 < v < 10000000:
                values.append(v)
    return list(dict.fromkeys(values))


def _search_prices(query, domain=None):
    engines = [
        f'https://www.google.com/search?q={quote(query)}&num=10&filter=0',
        f'https://www.bing.com/search?q={quote(query)}&count=10',
        f'https://search.yahoo.com/search?p={quote(query)}&n=10',
    ]
    for url in engines:
        try:
            r = requests.get(url, headers=bot.HEADERS, timeout=12)
            if r.status_code >= 400:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            text = soup.get_text(' ', strip=True)
            if domain and domain.lower() not in text.lower():
                continue
            vals = sorted(set(bot.prices(text)))
            if vals:
                return vals
        except Exception:
            pass
    return []


def _amazon_search_prices(url, title):
    m = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Za-z0-9]{8,})', url or '')
    asin = m.group(1) if m else None
    queries = []
    if asin:
        queries += [f'site:amazon.com.tr {asin} TL', f'"{asin}" Amazon Türkiye fiyat']
    if title:
        queries.append(f'site:amazon.com.tr "{title[:100]}" TL')
    for qtext in queries:
        found = _search_prices(qtext, 'amazon.com.tr')
        if found:
            # Arama sonuçlarında en düşük fiyat current olabilir; bir üst anlamlı fiyatı referans olarak tut.
            print(f'Amazon arama son çare fiyatları: {found[:12]}')
            return found[0], next((v for v in found if v > found[0] * 1.03), None)
    return None, None


def _akakce_market_reference(url, title, current):
    """Akakçe yalnızca piyasa/önceki fiyat referansıdır; Amazon güncel fiyatı yerine kullanılmaz."""
    m = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Za-z0-9]{8,})', url or '')
    queries = []
    if m:
        queries.append(f'site:akakce.com {m.group(1)}')
    if title:
        queries.append(f'site:akakce.com "{title[:100]}"')
    candidates = []
    for q in queries:
        vals = _search_prices(q, 'akakce.com')
        if vals:
            candidates.extend(vals)
    candidates = sorted(set(v for v in candidates if v > current * 1.03))
    if candidates:
        print(f'Akakçe piyasa/geçmiş referansı: {candidates[:10]}')
        return min(candidates)
    return None


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
    page.set_default_timeout(5000)
    page.set_default_navigation_timeout(15000)
    try:
        r = page.goto(url, wait_until='domcontentloaded')
        status = r.status if r else 0
        print(f'{site} ürün HTTP: {status} | {url}')
        if r and status < 400:
            page.wait_for_timeout(1800)
            html = page.content()
            current = _amazon_price_values(page, html)
            if current:
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
                print(f'{site} güvenilir güncel fiyat: {cur:.2f}')
                print(f'{site} sayfa referansı: {prev or 0:.2f}')
                return {'name': _clean_name(name), 'price': cur, 'previous': prev, 'url': bot.canonical(url), 'site': site}

        print(f'{site} DOM/HTML fiyatı bulunamadı; Amazon arama + Akakçe referansı deneniyor | {url}')
        cur, prev = _amazon_search_prices(url, title)
        if cur:
            ak_prev = _akakce_market_reference(url, title, cur)
            if ak_prev and (prev is None or ak_prev > prev):
                prev = ak_prev
            return {'name': _clean_name(title), 'price': cur, 'previous': prev, 'url': bot.canonical(url), 'site': site}
        print(f'{site} güvenilir güncel fiyat bulunamadı | {url}')
        return None
    except Exception as e:
        print(f'{site} ürün hata: {type(e).__name__}: {e}')
        return None
    finally:
        page.close()


def process_with_real_history(p):
    """İndirim bazını Amazon liste fiyatından değil, botun gerçek gözlem geçmişinden al."""
    try:
        rows = bot.sb('GET', 'products', params={'select': '*', 'product_url': f'eq.{p["url"]}', 'limit': '1'})
        now = bot.datetime.now(bot.timezone.utc).isoformat()
        old = bot.history(p['url'])
        previous_observed = old[0] if old else None
        if previous_observed is None:
            print(f'Kontrol: {p["site"]} | mevcut={p["price"]:.2f} | önceki gözlem yok -> paylaşılmadı')
        else:
            print(f'Kontrol: {p["site"]} | mevcut={p["price"]:.2f} | son gözlenen={previous_observed:.2f} | geçmiş={len(old)}')

        payload = {
            'product_name': p['name'], 'current_price': p['price'],
            'previous_price': previous_observed, 'product_url': p['url'],
            'site': p['site'], 'updated_at': now,
        }
        if rows:
            row = rows[0]
            bot.sb('PATCH', f'products?id=eq.{row["id"]}', json=payload)
        else:
            row = (bot.sb('POST', 'products', json=payload) or [payload])[0]

        bot.sb('POST', 'price_history', json={'price': p['price'], 'product_url': p['url'], 'site': p['site'], 'recorded_at': now})
        if previous_observed is None or previous_observed <= p['price']:
            return False
        disc = (previous_observed - p['price']) / previous_observed * 100
        if disc < bot.MIN_DISCOUNT:
            return False
        last = row.get('last_posted_at') if isinstance(row, dict) else None
        if last:
            try:
                if bot.datetime.now(bot.timezone.utc) - bot.datetime.fromisoformat(last.replace('Z', '+00:00')) < bot.timedelta(hours=bot.COOLDOWN):
                    return False
            except Exception:
                pass
        msg = (f'🔥 %{disc:.0f} İNDİRİM\n\n{p["name"]}\n\n'
               f'💰 {p["price"]:,.2f} TL\n🏷️ Önce: {previous_observed:,.2f} TL\n'
               f'🛍️ {p["site"]}\n🔗 {p["url"]}')
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
