import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import bot

_original_product_page = bot.product_page


def _clean_name(value):
    return re.sub(r'\s+', ' ', value or 'Ürün').strip()[:300]


def _amazon_price_values(page, html):
    """Amazon'da fiyatı parçalarından değil, güvenilir tam fiyat alanından oku."""
    values = []

    # 1) Amazon'un tam fiyatı gösteren alanları.
    full_selectors = [
        '.a-price .a-offscreen',
        '#corePrice_feature_div .a-offscreen',
        '.apexPriceToPay .a-offscreen',
        'meta[property="product:price:amount"]',
        'meta[itemprop="price"]',
    ]
    for sel in full_selectors:
        try:
            loc = page.locator(sel)
            n = min(loc.count(), 10)
            for i in range(n):
                raw = loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=400)
                v = bot.price(raw)
                if v is not None and 0 < v < 10000000:
                    values.append(v)
        except Exception:
            pass

    # 2) Eğer tam fiyat alanı yoksa Amazon'un whole + fraction parçalarını BİRLİKTE oku.
    # Örn. 3.739 + 91 -> 3739.91. Fraction tek başına asla fiyat kabul edilmez.
    if not values:
        try:
            whole_loc = page.locator('.a-price-whole')
            frac_loc = page.locator('.a-price-fraction')
            n = min(whole_loc.count(), 10)
            for i in range(n):
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


def _amazon_previous_prices(page):
    values = []
    selectors = [
        '.a-text-price .a-offscreen',
        '.priceBlockStrikePriceString',
        '.basisPrice .a-offscreen',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            n = min(loc.count(), 10)
            for i in range(n):
                v = bot.price(loc.nth(i).inner_text(timeout=400))
                if v is not None and 0 < v < 10000000:
                    values.append(v)
        except Exception:
            pass
    return list(dict.fromkeys(values))


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

        # Sayfa erişilemiyorsa mevcut arama fiyatı fallback olarak kullanılabilir.
        if not r or status >= 400:
            if search_ps:
                ps = sorted(set(v for v in search_ps if v is not None and v > 0))
                if ps:
                    cur = ps[0]
                    prev = next((v for v in ps if v > cur * 1.03), None)
                    print(f'{site} arama fiyatı kullanılıyor: {ps[:8]}')
                    return {
                        'name': _clean_name(title),
                        'price': cur,
                        'previous': prev,
                        'url': bot.canonical(url),
                        'site': site,
                    }
            return None

        page.wait_for_timeout(1500)
        html = page.content()
        current = _amazon_price_values(page, html)

        if not current:
            print(f'{site} güvenilir tam fiyat bulunamadı | {url}')
            return None

        # Birden fazla güvenilir alan varsa ilk alanlar aynı ürünün güncel fiyatını
        # temsil eder. En küçük değeri körlemesine seçmiyoruz.
        cur = current[0]

        previous = _amazon_previous_prices(page)
        previous = [v for v in previous if v > cur * 1.03]
        if not previous:
            # JSON-LD içindeki fiyatlar bazı Amazon sayfalarında güncel fiyatı tekrarlar.
            # Yalnızca güncel fiyattan belirgin yüksek olanı önceki fiyat adayı yap.
            try:
                _, jd_prices = bot.jsonld_all(html)
                previous = [v for v in jd_prices if v > cur * 1.03]
            except Exception:
                previous = []

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
        return {
            'name': _clean_name(name),
            'price': cur,
            'previous': prev,
            'url': bot.canonical(url),
            'site': site,
        }
    except Exception as e:
        print(f'{site} ürün hata: {type(e).__name__}: {e}')
        return None
    finally:
        page.close()


bot.product_page = product_page
bot.main()
