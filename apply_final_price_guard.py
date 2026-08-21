from pathlib import Path

P=Path('marketplace_scanner.py')
s=P.read_text(encoding='utf-8')
marker="if __name__ == '__main__':"

if 'def _bahadir_final_verify' not in s:
    guard=r'''

def _bahadir_authoritative_live_price(site,url,page):
    soup=BeautifulSoup(page.content(),'html.parser')
    if site=='Amazon':
        # Yalnızca görünür satın alma kutusundaki gerçek ödeme fiyatını kabul et.
        # Generic JSON-LD / eski priceblock alanları 70 TL gibi sahte/stale değerler üretebiliyor.
        selectors=[
            '#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen',
            '#corePriceDisplay_desktop_feature_div .priceToPay .a-price-whole',
            '#corePriceDisplay_desktop_feature_div .priceToPay .a-price',
        ]
        for sel in selectors:
            for el in soup.select(sel):
                raw=el.get('content') or el.get('value') or el.get_text(' ',strip=True)
                x=money(raw)
                if x and x>1:return x
        return None
    selectors={
        'Hepsiburada':['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]'],
        'Trendyol':['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]']
    }
    for sel in selectors.get(site,[]):
        for el in soup.select(sel):
            raw=el.get('content') or el.get('value') or el.get_text(' ',strip=True)
            x=money(raw)
            if x and x>1:return x
    for script in soup.select('script[type="application/ld+json"]'):
        text=script.string or script.get_text()
        for m in re.finditer(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)',text or '',re.I):
            x=money(m.group(1))
            if x and x>1:return x
    return None

_ORIGINAL_FINAL_VERIFY=verify
def _bahadir_final_verify(page,site,url,fallback_title,expected_current,candidate_previous):
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=9000)
        page.wait_for_timeout(700)
        live=_bahadir_authoritative_live_price(site,url,page)
        if live is None:
            print(f'FINAL FİYAT YOK | {site} | RED | {url}')
            return None
        if abs(live-expected_current)/max(expected_current,1)>.05:
            print(f'FINAL FİYAT RED | {site} | arama={expected_current:.2f} | canlı={live:.2f}')
            return None
        ref=candidate_previous
        try:
            old=history(site,url,live)
            if old and old>live:ref=old
        except Exception:pass
        if not ref or ref<=live:
            record_price(site,url,live)
            print(f'FINAL İNDİRİM YOK | {site} | canlı={live:.2f} | önceki={ref}')
            return None
        discount=(ref-live)/ref*100
        if discount<MIN_DISCOUNT or ref/max(live,1)>4:
            record_price(site,url,live)
            print(f'FINAL İNDİRİM EŞİĞİ RED | {site} | canlı={live:.2f} | önceki={ref:.2f} | %{discount:.1f}')
            return None
        soup=BeautifulSoup(page.content(),'html.parser')
        te=soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]')
        title=te.get('content','').strip() if te else fallback_title
        ie=soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]')
        image=ie.get('content','').strip() if ie else None
        record_price(site,url,live)
        print(f'FINAL DOĞRULANDI | {site} | canlı={live:.2f} | önceki={ref:.2f} | %{discount:.1f}')
        return clean_title(title),live,ref,discount,image
    except Exception as e:
        print(f'FINAL FİYAT HATA | {site} | {type(e).__name__}: {e}')
        return None

verify=_bahadir_final_verify

'''
    s=s.replace(marker,guard+marker,1)
    P.write_text(s,encoding='utf-8')
    compile(s,str(P),'exec')
    print('FINAL PRICE GUARD OK | Amazon only visible priceToPay accepted')
else:
    # Existing guard was too permissive; replace its authoritative price function.
    start=s.index('def _bahadir_authoritative_live_price')
    end=s.index('\n_ORIGINAL_FINAL_VERIFY=',start)
    new=r'''def _bahadir_authoritative_live_price(site,url,page):
    soup=BeautifulSoup(page.content(),'html.parser')
    if site=='Amazon':
        selectors=['#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen','#corePriceDisplay_desktop_feature_div .priceToPay .a-price-whole','#corePriceDisplay_desktop_feature_div .priceToPay .a-price']
        for sel in selectors:
            for el in soup.select(sel):
                raw=el.get('content') or el.get('value') or el.get_text(' ',strip=True)
                x=money(raw)
                if x and x>1:return x
        return None
    selectors={'Hepsiburada':['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]'],'Trendyol':['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]']}
    for sel in selectors.get(site,[]):
        for el in soup.select(sel):
            raw=el.get('content') or el.get('value') or el.get('data-price') or el.get_text(' ',strip=True)
            x=money(raw)
            if x and x>1:return x
    return None
'''
    s=s[:start]+new+s[end:]
    P.write_text(s,encoding='utf-8')
    compile(s,str(P),'exec')
    print('FINAL PRICE GUARD UPDATED | Amazon only visible priceToPay accepted')