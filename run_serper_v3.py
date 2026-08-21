import os,re,hashlib
from urllib.parse import urlparse,parse_qs,unquote
from playwright.sync_api import sync_playwright
import run_serper_v2 as v2

# V3.3: aggressive link recovery without weakening price/reference safety.
# docker-compose currently injects 60 resolve lookups; force a sane floor here so
# large 170+ candidate runs do not stop resolving links halfway through the list.
v2.MAX_RESOLVE=max(180,int(os.environ.get('MAX_RESOLVE_PER_RUN','180')))
v2.MAX_MARKET=max(40,int(os.environ.get('MAX_MARKET_REF_PER_RUN','40')))
v2.MAX_RENDER=max(20,int(os.environ.get('MAX_RENDER_CHECKS','20')))
v2.NEG_CACHE=min(180,int(os.environ.get('NEGATIVE_CACHE_SECONDS','180')))
OFFER_LOOKUPS=max(80,int(os.environ.get('MAX_OFFER_LINK_LOOKUPS','80')))
_offer_lookups=0

# Fresh cache namespace: old negative link results must not poison V3.3.
def ckey_v33(site,title):
    norm=re.sub(r'\s+',' ',(title or '').lower()).strip()
    return hashlib.sha1(f'v33|{site}|{norm}'.encode()).hexdigest()
v2.ckey=ckey_v33

_base_inspect=v2.inspect_page
def inspect_page_v33(url,expected=None):
    out=_base_inspect(url,expected)
    if out.get('available') is None and out.get('ok'):
        if out.get('live') or (out.get('title') or '').strip() or out.get('image'):
            out['available']=True;out['availability_evidence']='soft-product-page'
    return out
v2.inspect_page=inspect_page_v33

def compact_title(title):
    t=re.sub(r'\([^)]{20,}\)',' ',title or '')
    t=re.sub(r'\[[^]]{20,}\]',' ',t)
    t=re.sub(r'\s+',' ',t).strip()
    return ' '.join(t.split()[:16])

def _price_close(a,b,tol=.04):
    return bool(a and b and abs(float(a)-float(b))/max(float(a),float(b),1)<=tol)

def _unwrap(link):
    """Recover merchant URL from Google redirect/tracking links when present."""
    if not link:return ''
    try:
        p=urlparse(link)
        if 'google.' in p.netloc.lower():
            qs=parse_qs(p.query)
            for k in ('q','url','adurl'):
                vals=qs.get(k) or []
                if vals and vals[0].startswith('http'):return unquote(vals[0])
    except Exception:pass
    return link

def _candidate_score(title,result_title,link):
    aa=v2.toks(title);bb=v2.toks((result_title or '')+' '+urlparse(link).path.replace('-',' '))
    if not aa:return 0
    overlap=len(aa&bb)/max(1,len(aa))
    # Model/number tokens are especially useful for tablets, watches, TVs, etc.
    nums=set(re.findall(r'\b[a-z]*\d+[a-z0-9-]*\b',(title or '').lower()))
    rnums=set(re.findall(r'\b[a-z]*\d+[a-z0-9-]*\b',((result_title or '')+' '+link).lower()))
    bonus=.18 if nums and nums&rnums else 0
    return overlap+bonus

def _shopping_exact_offer(it,site,title):
    global _offer_lookups
    listed=v2.pprice(it.get('price'))
    if not listed or _offer_lookups>=OFFER_LOOKUPS:return None
    _offer_lookups+=1
    try:
        best=None;best_score=0
        # One exact-title shopping call usually returns the direct merchant URL and
        # is more useful than several organic searches.
        for r in v2.shopping(compact_title(title)):
            rp=v2.pprice(r.get('price'));link=_unwrap(r.get('link') or '');rt=r.get('title') or ''
            if not _price_close(rp,listed,.05):continue
            if not v2.valid(link,site):continue
            score=_candidate_score(title,rt,link)
            if score>best_score and (v2.match(title,rt,link) or score>=.48):best,best_score=link,score
        if best:
            print(f'FİYATA AİT LİNK BULUNDU: {site} | {listed:.2f} TL | skor={best_score:.2f} | {best[:135]}')
            return best
    except Exception as e:print(f'Fiyat-link arama hatası: {type(e).__name__}')
    return None

def resolve_v33(it,site):
    raw=_unwrap(it.get('link') or '');title=re.sub(r'\s+',' ',it.get('title') or '').strip()
    if v2.valid(raw,site):v2.cset(site,title,raw);return raw
    cached,known=v2.cget(site,title)
    if known:return cached if cached and v2.valid(cached,site) else None

    exact=_shopping_exact_offer(it,site,title)
    if exact:v2.cset(site,title,exact);return exact

    short=compact_title(title)
    toks=list(v2.toks(title))
    model=' '.join(sorted(toks,key=lambda x:(not any(c.isdigit() for c in x),-len(x)))[:10])
    # Spend at most two organic searches per candidate. V3.2 could spend four,
    # exhausting the 60-query budget on the first part of the candidate list.
    queries=[
        f'site:{v2.SITE_DOMAIN[site]} "{short[:145]}"',
        f'site:{v2.SITE_DOMAIN[site]} {model or " ".join(short.split()[:8])}',
    ]
    best=None;best_score=0
    for q in queries:
        if v2.COUNTERS['resolve']>=v2.MAX_RESOLVE:break
        for r in v2.search(q):
            link=_unwrap(r.get('link') or '')
            if not v2.valid(link,site):continue
            rt=r.get('title') or '';score=_candidate_score(title,rt,link)
            if score>best_score and (v2.match(title,rt,link) or score>=.44):best,best_score=link,score
        if best and best_score>=.52:break
    if best:
        v2.cset(site,title,best);print(f'Direkt ürün linki doğrulandı: {site} | skor={best_score:.2f} | {best[:140]}');return best
    v2.cset(site,title,None);return None
v2.resolve=resolve_v33

def _body_prices(body,current):
    vals=[]
    pats=[r'(?<!\d)(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*(?:TL|₺)',r'(?<!\d)(\d{2,6}(?:[.,]\d{2})?)\s*(?:TL|₺)']
    for pat in pats:
        for m in re.finditer(pat,body,re.I):
            x=v2.pprice(m.group(1))
            if x and current*.55<=x<=current*1.65:vals.append(x)
    return vals

def render_verify_v33(link,current,title):
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
v2.render_verify=render_verify_v33

if __name__=='__main__':
    print(f'=== Serper V3.3 aktif | link çözüm={v2.MAX_RESOLVE} | fiyat-link bütünlüğü + esnek stok ===')
    v2.main()
