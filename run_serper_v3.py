import os,re,hashlib
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
import run_serper_v2 as v2

# V3.2: price-link integrity + better recall.
v2.MAX_RESOLVE=int(os.environ.get('MAX_RESOLVE_PER_RUN','120'))
v2.MAX_MARKET=int(os.environ.get('MAX_MARKET_REF_PER_RUN','60'))
v2.MAX_RENDER=int(os.environ.get('MAX_RENDER_CHECKS','50'))
v2.NEG_CACHE=int(os.environ.get('NEGATIVE_CACHE_SECONDS','300'))
OFFER_LOOKUPS=int(os.environ.get('MAX_OFFER_LINK_LOOKUPS','30'))
_offer_lookups=0

# Fresh namespace so old bad link matches do not survive.
def ckey_v32(site,title):
    norm=re.sub(r'\s+',' ',(title or '').lower()).strip()
    return hashlib.sha1(f'v32|{site}|{norm}'.encode()).hexdigest()
v2.ckey=ckey_v32

_base_inspect=v2.inspect_page
def inspect_page_v32(url,expected=None):
    out=_base_inspect(url,expected)
    if out.get('available') is None and out.get('ok'):
        if out.get('live') or (out.get('title') or '').strip() or out.get('image'):
            out['available']=True;out['availability_evidence']='soft-product-page'
    return out
v2.inspect_page=inspect_page_v32

def compact_title(title):
    t=re.sub(r'\([^)]{20,}\)',' ',title or '')
    t=re.sub(r'\s+',' ',t).strip()
    return ' '.join(t.split()[:16])

def _price_close(a,b,tol=.035):
    return bool(a and b and abs(float(a)-float(b))/max(float(a),float(b),1)<=tol)

def _shopping_exact_offer(it,site,title):
    """Prefer a direct marketplace URL whose advertised price matches the discovered deal.
    This fixes the case where organic resolution lands on the right product but wrong seller/variant.
    """
    global _offer_lookups
    listed=v2.pprice(it.get('price'))
    if not listed or _offer_lookups>=OFFER_LOOKUPS:return None
    _offer_lookups+=1
    try:
        for r in v2.shopping(compact_title(title)):
            rp=v2.pprice(r.get('price'));link=r.get('link') or '';rt=r.get('title') or ''
            if not _price_close(rp,listed):continue
            if not v2.valid(link,site):continue
            if not v2.match(title,rt,link):continue
            print(f'FİYATA AİT LİNK BULUNDU: {site} | {listed:.2f} TL | {link[:145]}')
            return link
    except Exception as e:print(f'Fiyat-link arama hatası: {type(e).__name__}')
    return None

def resolve_v32(it,site):
    raw=it.get('link') or '';title=re.sub(r'\s+',' ',it.get('title') or '').strip()
    if v2.valid(raw,site):v2.cset(site,title,raw);return raw
    cached,known=v2.cget(site,title)
    if known:return cached if cached and v2.valid(cached,site) else None

    exact=_shopping_exact_offer(it,site,title)
    if exact:v2.cset(site,title,exact);return exact

    short=compact_title(title);toks=list(v2.toks(title))
    model=' '.join(sorted(toks,key=lambda x:(not any(c.isdigit() for c in x),-len(x)))[:10])
    queries=[]
    for q in [f'site:{v2.SITE_DOMAIN[site]} "{title[:150]}"',f'site:{v2.SITE_DOMAIN[site]} "{short[:135]}"',f'site:{v2.SITE_DOMAIN[site]} {model}',f'site:{v2.SITE_DOMAIN[site]} {" ".join(short.split()[:8])}']:
        q=re.sub(r'\s+',' ',q).strip()
        if q and q not in queries:queries.append(q)
    best=None;best_score=0
    for q in queries:
        for r in v2.search(q):
            link=r.get('link') or ''
            if not v2.valid(link,site):continue
            rt=r.get('title') or '';aa=v2.toks(title);bb=v2.toks(rt+' '+urlparse(link).path.replace('-',' '));score=len(aa&bb)/max(1,len(aa))
            if v2.match(title,rt,link) and score>best_score:best,best_score=link,score
        if best and best_score>=0.50:break
        if v2.COUNTERS['resolve']>=v2.MAX_RESOLVE:break
    if best:
        v2.cset(site,title,best);print(f'Direkt ürün linki doğrulandı: {site} | {best[:150]}');return best
    v2.cset(site,title,None);return None
v2.resolve=resolve_v32

def _body_prices(body,current):
    """Fallback for JS marketplaces: read TL/₺ prices from rendered visible body."""
    vals=[]
    pats=[r'(?<!\d)(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*(?:TL|₺)',r'(?<!\d)(\d{2,6}(?:[.,]\d{2})?)\s*(?:TL|₺)']
    for pat in pats:
        for m in re.finditer(pat,body,re.I):
            x=v2.pprice(m.group(1))
            if x and current*.55<=x<=current*1.65:vals.append(x)
    return vals

def render_verify_v32(link,current,title):
    """Final gate: destination page must not silently disagree with the posted price.
    Explicit OOS blocks. If a visible live price is found, it is returned to the core so
    the post is recalculated or cancelled. Unknown stock alone does not block.
    """
    if v2.COUNTERS['render']>=v2.MAX_RENDER:return True,None,''
    v2.COUNTERS['render']+=1
    try:
        with sync_playwright() as pw:
            b=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
            p=b.new_page(user_agent=v2.bot.HEADERS.get('User-Agent'),locale='tr-TR')
            p.goto(link,wait_until='domcontentloaded',timeout=24000);p.wait_for_timeout(2200)
            body=re.sub(r'\s+',' ',p.locator('body').inner_text(timeout=8000));low=body.lower()
            bad=['stokta yok','stokta bulunmuyor','stokta bulunmamaktadır','ürün tükendi','urun tukendi','tükendi','tukendi','out of stock','sold out','currently unavailable','satışa kapalı','satisa kapali','bu ürün şu anda mevcut değil','bu urun su anda mevcut degil']
            if any(x in low for x in bad):b.close();return False,None,''
            vals=[]
            sels=['.a-price .a-offscreen','.apexPriceToPay .a-offscreen','[data-test-id="price-current-price"]','[class*="currentPrice"]','[class*="salePrice"]','.prc-dsc','.prc-slg','[itemprop="price"]','meta[property="product:price:amount"]']
            for sel in sels:
                try:
                    loc=p.locator(sel)
                    for i in range(min(loc.count(),10)):
                        e=loc.nth(i);x=v2.pprice(e.get_attribute('content') or e.inner_text(timeout=500))
                        if x and current*.55<=x<=current*1.65:vals.append(x)
                except:pass
            vals += _body_prices(body,current)
            # Prefer a price very near discovered value; otherwise use the most frequent/nearest
            # visible commercial price, not a hidden arbitrary list price.
            rv=None
            if vals:
                near=[x for x in vals if _price_close(x,current,.035)]
                if near:rv=min(near,key=lambda x:abs(x-current))
                else:
                    counts={round(x,2):sum(1 for y in vals if abs(y-x)/max(x,1)<.003) for x in vals}
                    rv=max(vals,key=lambda x:(counts[round(x,2)],-abs(x-current)))
            try:ttl=re.sub(r'\s+',' ',p.locator('h1').first.inner_text(timeout=1500)).strip()
            except:ttl=''
            has_buy=any(x in low for x in ['sepete ekle','hemen al','şimdi al','simdi al','add to cart','buy now','sepete at','satın al','satin al'])
            if rv and not _price_close(rv,current,.035):print(f'FİYAT-LİNK UYUŞMAZLIĞI: keşif={current:.2f} -> link={rv:.2f} | {title[:65]}')
            elif has_buy:print(f'RENDER STOK/FİYAT ONAYI | {title[:65]}')
            b.close();return True,rv,ttl
    except Exception as e:
        print(f'RENDER HATA (stok engeli uygulanmadı): {type(e).__name__} | {title[:60]}');return True,None,''
v2.render_verify=render_verify_v32

if __name__=='__main__':
    print('=== Serper V3.2 aktif | fiyat-link bütünlüğü + piyasa koruması + esnek stok ===')
    v2.main()
