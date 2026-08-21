import os,re,hashlib
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
import run_serper_v2 as v2

# V3.1: improve recall without weakening price/reference safety.
# Stock policy is intentionally asymmetric: only explicit OOS blocks a deal.
v2.MAX_RESOLVE=int(os.environ.get('MAX_RESOLVE_PER_RUN','120'))
v2.MAX_MARKET=int(os.environ.get('MAX_MARKET_REF_PER_RUN','50'))
v2.MAX_RENDER=int(os.environ.get('MAX_RENDER_CHECKS','40'))
v2.NEG_CACHE=int(os.environ.get('NEGATIVE_CACHE_SECONDS','300'))

# New cache namespace: old negative resolutions must not poison the improved resolver.
def ckey_v31(site,title):
    norm=re.sub(r'\s+',' ',(title or '').lower()).strip()
    return hashlib.sha1(f'v31|{site}|{norm}'.encode()).hexdigest()
v2.ckey=ckey_v31

# HTTP product pages on these marketplaces are frequently JS-rendered. Requests may not
# expose a literal "Sepete ekle" even when the product is purchasable. If the page itself
# resolves as a product page and exposes a live price/title/image, treat availability as
# soft-positive unless an explicit OOS marker was already found by inspect_page().
_base_inspect=v2.inspect_page
def inspect_page_v31(url,expected=None):
    out=_base_inspect(url,expected)
    if out.get('available') is None and out.get('ok'):
        evidence=bool(out.get('live')) or bool((out.get('title') or '').strip()) or bool(out.get('image'))
        if evidence:
            out['available']=True
            out['availability_evidence']='soft-product-page'
    return out
v2.inspect_page=inspect_page_v31

def compact_title(title):
    t=re.sub(r'\([^)]{20,}\)',' ',title or '')
    t=re.sub(r'\s+',' ',t).strip()
    words=t.split()
    return ' '.join(words[:16])

def resolve_v31(it,site):
    raw=it.get('link') or '';title=re.sub(r'\s+',' ',it.get('title') or '').strip()
    if v2.valid(raw,site):v2.cset(site,title,raw);return raw
    cached,known=v2.cget(site,title)
    if known:return cached if cached and v2.valid(cached,site) else None
    short=compact_title(title)
    toks=list(v2.toks(title))
    model=' '.join(sorted(toks,key=lambda x:(not any(c.isdigit() for c in x),-len(x)))[:10])
    brand_model=' '.join(short.split()[:8])
    queries=[]
    for q in [
        f'site:{v2.SITE_DOMAIN[site]} "{title[:150]}"',
        f'site:{v2.SITE_DOMAIN[site]} "{short[:135]}"',
        f'site:{v2.SITE_DOMAIN[site]} {model}',
        f'site:{v2.SITE_DOMAIN[site]} {brand_model}',
    ]:
        q=re.sub(r'\s+',' ',q).strip()
        if q and q not in queries:queries.append(q)
    best=None;best_score=0
    for q in queries:
        for r in v2.search(q):
            link=r.get('link') or ''
            if not v2.valid(link,site):continue
            rt=r.get('title') or ''
            aa=v2.toks(title);bb=v2.toks(rt+' '+urlparse(link).path.replace('-',' '));score=len(aa&bb)/max(1,len(aa))
            if v2.match(title,rt,link) and score>best_score:best,best_score=link,score
        if best and best_score>=0.50:break
        if v2.COUNTERS['resolve']>=v2.MAX_RESOLVE:break
    if best:
        v2.cset(site,title,best);print(f'Direkt ürün linki doğrulandı: {site} | {best[:150]}');return best
    v2.cset(site,title,None);return None
v2.resolve=resolve_v31

def render_verify_v31(link,current,title):
    """Only explicit out-of-stock evidence blocks a candidate.

    Positive stock evidence: add-to-cart/buy button, or a rendered live price.
    Unknown/dynamic UI is allowed through; this avoids false negatives on marketplace JS.
    """
    if v2.COUNTERS['render']>=v2.MAX_RENDER:return True,None,''
    v2.COUNTERS['render']+=1
    try:
        with sync_playwright() as pw:
            b=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
            p=b.new_page(user_agent=v2.bot.HEADERS.get('User-Agent'),locale='tr-TR')
            p.goto(link,wait_until='domcontentloaded',timeout=22000);p.wait_for_timeout(1800)
            body=re.sub(r'\s+',' ',p.locator('body').inner_text(timeout=7000));low=body.lower()
            bad=['stokta yok','stokta bulunmuyor','stokta bulunmamaktadır','ürün tükendi','urun tukendi','tükendi','tukendi','out of stock','sold out','currently unavailable','satışa kapalı','satisa kapali','bu ürün şu anda mevcut değil','bu urun su anda mevcut degil']
            if any(x in low for x in bad):
                b.close();return False,None,''
            buy_words=['sepete ekle','hemen al','şimdi al','simdi al','add to cart','buy now','sepete at','satın al','satin al']
            has_buy=any(x in low for x in buy_words)
            vals=[]
            sels=['.a-price .a-offscreen','.apexPriceToPay .a-offscreen','[data-test-id="price-current-price"]','[class*="currentPrice"]','[class*="salePrice"]','.prc-dsc','.prc-slg','[itemprop="price"]','meta[property="product:price:amount"]']
            for sel in sels:
                try:
                    loc=p.locator(sel)
                    for i in range(min(loc.count(),8)):
                        e=loc.nth(i);val=e.get_attribute('content') or e.inner_text(timeout=500);x=v2.pprice(val)
                        if x and current*.55<=x<=current*1.55:vals.append(x)
                except:pass
            rv=min(vals,key=lambda x:abs(x-current)) if vals else None
            try:ttl=re.sub(r'\s+',' ',p.locator('h1').first.inner_text(timeout=1500)).strip()
            except:ttl=''
            if has_buy:print(f'RENDER STOK ONAYI: sepete/satın al butonu bulundu | {title[:65]}')
            elif rv is not None:print(f'RENDER STOK ONAYI: canlı fiyat mevcut, stok dışı işareti yok | {title[:65]}')
            else:print(f'RENDER STOK ESNEK: açık stok dışı işareti yok | {title[:65]}')
            b.close();return True,rv,ttl
    except Exception as e:
        print(f'RENDER HATA (stok engeli uygulanmadı): {type(e).__name__} | {title[:60]}');return True,None,''
v2.render_verify=render_verify_v31

if __name__=='__main__':
    print('=== Serper V3.1 aktif | link çözüm yüksek | stok: yalnızca kesin stok-yok engeller ===')
    v2.main()
