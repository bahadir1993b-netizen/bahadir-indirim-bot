import os,re,hashlib,statistics
from urllib.parse import urlparse,parse_qs,unquote
from playwright.sync_api import sync_playwright
import run_serper_v2 as v2
import price_reference as pr

# V3.5: deal-first architecture. Broad Shopping is discovery only; expensive
# link/market/render validation runs on a ranked, diverse shortlist.
v2.MAX_RESOLVE=max(90,int(os.environ.get('MAX_RESOLVE_PER_RUN','90')))
v2.MAX_MARKET=max(60,int(os.environ.get('MAX_MARKET_REF_PER_RUN','60')))
v2.MAX_RENDER=max(30,int(os.environ.get('MAX_RENDER_CHECKS','30')))
v2.NEG_CACHE=min(120,int(os.environ.get('NEGATIVE_CACHE_SECONDS','120')))
SHORTLIST_MAX=max(35,int(os.environ.get('SERPER_SHORTLIST_MAX','50')))
PER_QUERY=max(4,int(os.environ.get('SERPER_PER_QUERY','6')))
OFFER_LOOKUPS=max(60,int(os.environ.get('MAX_OFFER_LINK_LOOKUPS','70')))
_offer_lookups=0
_exact_cache={}

# Fresh namespace so old negative link resolutions do not suppress V3.5.
def ckey_v35(site,title):
    norm=re.sub(r'\s+',' ',(title or '').lower()).strip()
    return hashlib.sha1(f'v35|{site}|{norm}'.encode()).hexdigest()
v2.ckey=ckey_v35

_base_inspect=v2.inspect_page
def inspect_page_v35(url,expected=None):
    out=_base_inspect(url,expected)
    # Unknown stock must not kill a deal. Only explicit OOS blocks downstream.
    if out.get('available') is None and out.get('ok') and (out.get('live') or out.get('title') or out.get('image')):
        out['available']=True;out['availability_evidence']='soft-product-page'
    return out
v2.inspect_page=inspect_page_v35

def compact_title(title):
    t=re.sub(r'\([^)]{20,}\)',' ',title or '')
    t=re.sub(r'\[[^]]{20,}\]',' ',t)
    t=re.sub(r'\s+',' ',t).strip()
    return ' '.join(t.split()[:16])

def _price_close(a,b,tol=.04):
    return bool(a and b and abs(float(a)-float(b))/max(float(a),float(b),1)<=tol)

def _unwrap(link):
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

def _model_tokens(text):
    return set(re.findall(r'\b[a-z]*\d+[a-z0-9/-]*\b',(text or '').lower()))

def _candidate_score(title,result_title,link):
    aa=v2.toks(title);bb=v2.toks((result_title or '')+' '+urlparse(link).path.replace('-',' '))
    if not aa:return 0
    overlap=len(aa&bb)/max(1,len(aa))
    ma=_model_tokens(title);mb=_model_tokens((result_title or '')+' '+link)
    bonus=.22 if ma and ma&mb else 0
    return overlap+bonus

def _exact_results(title):
    key=re.sub(r'\s+',' ',compact_title(title).lower()).strip()
    if key in _exact_cache:return _exact_cache[key]
    try:rows=v2.shopping(compact_title(title))
    except Exception:rows=[]
    _exact_cache[key]=rows
    return rows

def _shopping_exact_offer(it,site,title):
    global _offer_lookups
    listed=v2.pprice(it.get('price'))
    if not listed or _offer_lookups>=OFFER_LOOKUPS:return None
    _offer_lookups+=1
    best=None;best_score=0
    for r in _exact_results(title):
        rp=v2.pprice(r.get('price'));link=_unwrap(r.get('link') or '');rt=r.get('title') or ''
        if not _price_close(rp,listed,.05) or not v2.valid(link,site):continue
        score=_candidate_score(title,rt,link)
        if score>best_score and (v2.match(title,rt,link) or score>=.48):best,best_score=link,score
    if best:print(f'FİYATA AİT LİNK BULUNDU: {site} | {listed:.2f} TL | skor={best_score:.2f} | {best[:135]}')
    return best

def resolve_v35(it,site):
    raw=_unwrap(it.get('link') or '');title=re.sub(r'\s+',' ',it.get('title') or '').strip()
    if v2.valid(raw,site):v2.cset(site,title,raw);return raw
    cached,known=v2.cget(site,title)
    if known:return cached if cached and v2.valid(cached,site) else None
    exact=_shopping_exact_offer(it,site,title)
    if exact:v2.cset(site,title,exact);return exact

    # Only after Shopping fails do we spend organic search calls.
    short=compact_title(title);models=' '.join(sorted(_model_tokens(title),key=len,reverse=True)[:5])
    queries=[f'site:{v2.SITE_DOMAIN[site]} "{short[:145]}"',f'site:{v2.SITE_DOMAIN[site]} {models or " ".join(short.split()[:8])}']
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
v2.resolve=resolve_v35

# Reuse the exact Shopping response already fetched for link resolution. This removes
# the old pattern of one Shopping call for the link + another Shopping call for market.
def market_snapshot_v35(title):
    vals=[]
    for r in _exact_results(title):
        rt=r.get('title') or '';p=v2.pprice(r.get('price'))
        if not p:continue
        score=_candidate_score(title,rt,_unwrap(r.get('link') or ''))
        # Model-bearing products require model agreement; generic products require
        # stronger token overlap.
        ma=_model_tokens(title);mb=_model_tokens(rt)
        if ma and not (ma&mb):continue
        if score<(.50 if ma else .62):continue
        vals.append(p)
    clean=pr._robust_prices(vals)
    if len(clean)<2:return None,None,len(clean),'exact-shopping-insufficient'
    floor=min(clean);med=float(statistics.median(clean))
    return floor,med,len(clean),'exact-shopping-shared-v35'
v2.market_snapshot=market_snapshot_v35

# ---- candidate ranking / shortlist -------------------------------------------------
def _norm_words(title):
    stop={'ve','ile','icin','için','yeni','urun','ürün','adet','set','siyah','beyaz','model','the','plus','pro'}
    return [x for x in re.findall(r'[a-z0-9çğıöşü]{2,}',(title or '').lower()) if x not in stop]

def _identity_key(it):
    pid=str(it.get('productId') or '').strip()
    if pid:return 'pid:'+pid
    t=it.get('title') or '';models=sorted(_model_tokens(t))
    words=_norm_words(t)
    core=' '.join((models[:3] if models else words[:8]))
    return 'txt:'+core

def _raw_score(it,query_idx):
    site=v2.site_of(it);title=it.get('title') or '';cur=v2.pprice(it.get('price'))
    if not site or not cur or len(title)<8:return -999
    low=title.lower().strip()
    if low in {'amazon.com.tr','hepsiburada','trendyol'}:return -999
    score=0.0
    raw=_unwrap(it.get('link') or '')
    if v2.valid(raw,site):score+=28
    if str(it.get('productId') or '').strip():score+=8
    if _model_tokens(title):score+=10
    pos=int(it.get('position') or 50);score+=max(0,18-min(pos,18))
    sref=v2.source_ref(it,cur)
    if sref and sref>cur:
        disc=(sref-cur)/sref*100
        score+=90+min(40,disc)
        it['_source_ref_hint']=sref
    # Existing direct-link history is cheap and highly valuable: prioritize known
    # products only when their own history really shows a drop.
    if v2.valid(raw,site):
        try:
            hist=v2.bot.history(v2.canonical(raw))
            higher=[x for x in hist if x and cur*1.08<x<=cur*1.8]
            if len(higher)>=2:
                href=float(statistics.median(higher));disc=(href-cur)/href*100
                if disc>=10:score+=100+min(50,disc);it['_history_hint']=href
        except Exception:pass
    # Small deterministic diversity bonus prevents one category monopolising list.
    score+=(query_idx%5)*0.3
    return score

def main_v35():
    print(f'=== Serper V3.5 deal-first | kısa_liste={SHORTLIST_MAX} | link={v2.MAX_RESOLVE} piyasa={v2.MAX_MARKET} ===')
    raw=[]
    for qi,q in enumerate(v2.QUERIES):
        try:rows=v2.shopping(q)
        except Exception:rows=[]
        for it in rows:
            site=v2.site_of(it)
            if not site:continue
            it=dict(it);it['_qi']=qi;it['_score']=_raw_score(it,qi)
            if it['_score']>-900:raw.append(it)

    # Global product dedupe: Google Shopping productId is preferred. For duplicate
    # product cards, keep the cheapest offer; on equal price keep the stronger card.
    groups={}
    for it in raw:
        k=_identity_key(it);cur=v2.pprice(it.get('price')) or 10**12
        prev=groups.get(k)
        if not prev or cur<(v2.pprice(prev.get('price')) or 10**12)*.995 or (abs(cur-(v2.pprice(prev.get('price')) or cur))/max(cur,1)<.005 and it['_score']>prev['_score']):
            groups[k]=it
    unique=list(groups.values())

    # Preserve category diversity first, then fill remaining slots by deal evidence.
    chosen=[];used=set()
    for qi in range(len(v2.QUERIES)):
        bucket=sorted((x for x in unique if x.get('_qi')==qi),key=lambda x:x['_score'],reverse=True)
        for it in bucket[:PER_QUERY]:
            k=_identity_key(it)
            if k not in used:chosen.append(it);used.add(k)
    for it in sorted(unique,key=lambda x:x['_score'],reverse=True):
        if len(chosen)>=SHORTLIST_MAX:break
        k=_identity_key(it)
        if k not in used:chosen.append(it);used.add(k)
    chosen=sorted(chosen[:SHORTLIST_MAX],key=lambda x:x['_score'],reverse=True)
    print(f'ÖN ELEME: keşif={len(raw)} | tekil={len(unique)} | detaylı_kontrol={len(chosen)} | rastgele_tam_tarama=YOK')

    for it in chosen:v2.process(it)
    print(f'=== Bitti. Keşif={len(raw)} Tekil={len(unique)} Hedef={len(chosen)} | Amazon={v2.STATS["amazon"]} HB={v2.STATS["hepsiburada"]} Trendyol={v2.STATS["trendyol"]} | fiyat_yok={v2.STATS["no_price"]} link_yok={v2.STATS["no_link"]} stok_yok={v2.STATS["no_stock"]} stok_belirsiz={v2.STATS["stock_unknown"]} referans_yok={v2.STATS["no_ref"]} esik_alti={v2.STATS["below"]} cooldown={v2.STATS["cooldown"]} hata={v2.STATS["errors"]} | piyasa_engel={v2.STATS["market_blocked"]} piyasa_ref={v2.STATS["market_ref"]} kampanya={v2.STATS["campaign"]} render_ok={v2.STATS["render_ok"]} render_stok={v2.STATS["render_stock"]} render_belirsiz={v2.STATS["render_unknown"]} render_fiyat={v2.STATS["render_price"]} | link_sorgu={v2.COUNTERS["resolve"]} piyasa_sorgu={v2.COUNTERS["market"]} render_sorgu={v2.COUNTERS["render"]} | Gönderilen={v2.STATS["sent"]} ===')

# ---- final browser gate ------------------------------------------------------------
def _body_prices(body,current):
    vals=[]
    for pat in [r'(?<!\d)(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*(?:TL|₺)',r'(?<!\d)(\d{2,6}(?:[.,]\d{2})?)\s*(?:TL|₺)']:
        for m in re.finditer(pat,body,re.I):
            x=v2.pprice(m.group(1))
            if x and current*.55<=x<=current*1.65:vals.append(x)
    return vals

def render_verify_v35(link,current,title):
    if v2.COUNTERS['render']>=v2.MAX_RENDER:return True,None,''
    v2.COUNTERS['render']+=1
    try:
        with sync_playwright() as pw:
            b=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
            p=b.new_page(user_agent=v2.bot.HEADERS.get('User-Agent'),locale='tr-TR')
            p.goto(link,wait_until='domcontentloaded',timeout=22000);p.wait_for_timeout(1800)
            body=re.sub(r'\s+',' ',p.locator('body').inner_text(timeout=7000));low=body.lower()
            bad=['stokta yok','stokta bulunmuyor','stokta bulunmamaktadır','ürün tükendi','urun tukendi','tükendi','tukendi','out of stock','sold out','currently unavailable','satışa kapalı','satisa kapali','bu ürün şu anda mevcut değil','bu urun su anda mevcut degil']
            if any(x in low for x in bad):b.close();return False,None,''
            vals=[]
            for sel in ['.a-price .a-offscreen','.apexPriceToPay .a-offscreen','[data-test-id="price-current-price"]','[class*="currentPrice"]','[class*="salePrice"]','.prc-dsc','.prc-slg','[itemprop="price"]','meta[property="product:price:amount"]']:
                try:
                    loc=p.locator(sel)
                    for i in range(min(loc.count(),8)):
                        e=loc.nth(i);x=v2.pprice(e.get_attribute('content') or e.inner_text(timeout=400))
                        if x and current*.55<=x<=current*1.65:vals.append(x)
                except:pass
            vals+=_body_prices(body,current);rv=None
            if vals:
                near=[x for x in vals if _price_close(x,current,.035)]
                rv=min(near,key=lambda x:abs(x-current)) if near else min(vals,key=lambda x:abs(x-current))
            try:ttl=re.sub(r'\s+',' ',p.locator('h1').first.inner_text(timeout=1200)).strip()
            except:ttl=''
            has_buy=any(x in low for x in ['sepete ekle','hemen al','şimdi al','simdi al','add to cart','buy now','sepete at','satın al','satin al'])
            if rv and not _price_close(rv,current,.035):print(f'FİYAT-LİNK UYUŞMAZLIĞI: keşif={current:.2f} -> link={rv:.2f} | {title}')
            elif has_buy:print(f'RENDER STOK/FİYAT ONAYI | {title}')
            b.close();return True,rv,ttl
    except Exception as e:
        print(f'RENDER HATA (stok engeli uygulanmadı): {type(e).__name__} | {title}');return True,None,''
v2.render_verify=render_verify_v35

if __name__=='__main__':main_v35()
