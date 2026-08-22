import os,re,time,json,html,statistics,requests,hashlib
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,urlunparse,urlencode
from playwright.sync_api import sync_playwright
import bot
from price_reference import market_snapshot
from deal_validation import inspect_page,choose_reference

if bot.SUPABASE_URL.endswith('/rest/v1'):bot.SUPABASE_URL=bot.SUPABASE_URL[:-8].rstrip('/')
bot.MIN_DISCOUNT=float(os.environ.get('MIN_DISCOUNT','15'))
SERPER_API_KEY=os.environ['SERPER_API_KEY']
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
QUERIES=[
 'elektronik indirim fırsat','telefon tablet akıllı saat indirim','laptop bilgisayar monitör kulaklık indirim',
 'televizyon oyun konsolu oyuncu indirim','küçük ev aletleri kahve makinesi süpürge indirim',
 'ev yaşam mutfak züccaciye indirim','kişisel bakım kozmetik şampuan güneş kremi indirim',
 'anne bebek oyuncak çocuk indirim','ofis kırtasiye fotokopi kağıdı indirim','market deterjan temizlik gıda indirim'
]
TARGETS={'amazon.com.tr':'Amazon','www.amazon.com.tr':'Amazon','hepsiburada.com':'Hepsiburada','www.hepsiburada.com':'Hepsiburada','trendyol.com':'Trendyol','www.trendyol.com':'Trendyol'}
SITE_DOMAIN={'Amazon':'amazon.com.tr','Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
MAX_RESOLVE=int(os.environ.get('MAX_RESOLVE_PER_RUN','30'));MAX_MARKET=int(os.environ.get('MAX_MARKET_REF_PER_RUN','30'));MAX_RENDER=int(os.environ.get('MAX_RENDER_CHECKS','10'))
NEG_CACHE=int(os.environ.get('NEGATIVE_CACHE_SECONDS','3600'));CACHE_FILE=Path('/app/data/link_cache_v2.json');CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
try:CACHE=json.loads(CACHE_FILE.read_text('utf-8')) if CACHE_FILE.exists() else {}
except:CACHE={}
COUNTERS={'resolve':0,'market':0,'render':0}
STATS={'seen':0,'amazon':0,'hepsiburada':0,'trendyol':0,'no_price':0,'no_link':0,'no_stock':0,'stock_unknown':0,'no_ref':0,'below':0,'cooldown':0,'sent':0,'errors':0,'market_blocked':0,'market_ref':0,'live_corrected':0,'campaign':0,'render_ok':0,'render_stock':0,'render_unknown':0,'render_price':0}

def pprice(v):
    from deal_validation import price
    return price(v)
def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def toks(t):return {x for x in re.findall(r'[a-z0-9çğıöşü]{2,}',(t or '').lower()) if x not in {'ve','ile','için','icin','bir','ürün','urun','yeni','adet','set','siyah','beyaz','the'}}
def match(a,b,link=''):
    aa=toks(a);bb=toks((b or '')+' '+urlparse(link).path.replace('-',' '));c=aa&bb
    return bool(aa) and (len(c)>=2 or len(c)/max(1,len(aa))>=.42)
def valid(link,site):
    try:
        p=urlparse(link);host=p.netloc.lower();path=p.path.rstrip('/');low=path.lower()
        if TARGETS.get(host)!=site or not path or 'yorumlari' in low or '/kategori/' in low:return False
        if site=='Amazon':return bool(re.search(r'/(?:dp|gp/product|gp/aw/d)/[A-Z0-9]{8,}(?:/|$)',path,re.I))
        if site=='Trendyol':return bool(re.search(r'-p-\d+(?:/|$)',path,re.I))
        return bool(re.search(r'-(?:pm-)?[A-Z0-9]{8,}(?:/|$)',path,re.I)) and not re.search(r'-c-\d+(?:/|$)',path,re.I)
    except:return False
def site_of(it):
    h=urlparse(it.get('link') or '').netloc.lower()
    if h in TARGETS:return TARGETS[h]
    s=(it.get('source') or '').lower()
    if 'amazon' in s:return 'Amazon'
    if 'hepsiburada' in s:return 'Hepsiburada'
    if 'trendyol' in s:return 'Trendyol'
def canonical(link):
    p=urlparse(link);return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))
def outlink(link,site):
    p=urlparse(link);q=[('tag',AMAZON_TAG)] if site=='Amazon' and AMAZON_TAG else []
    return canonical(link)+(('?'+urlencode(q)) if q else '')
def ckey(site,title):return hashlib.sha1(f'{site}|{re.sub(r"\s+"," ",(title or "").lower()).strip()}'.encode()).hexdigest()
def cget(site,title):
    x=CACHE.get(ckey(site,title))
    if not isinstance(x,dict):return None,False
    age=time.time()-float(x.get('ts',0));link=x.get('link') or ''
    if link and age<30*86400:return link,True
    if not link and age<NEG_CACHE:return None,True
    return None,False
def cset(site,title,link):
    CACHE[ckey(site,title)]={'ts':time.time(),'link':link or ''}
    try:CACHE_FILE.write_text(json.dumps(CACHE,ensure_ascii=False),'utf-8')
    except:pass

def shopping(q):
    r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':q,'gl':'tr','hl':'tr','location':'Turkey','num':80},timeout=20)
    print(f'Serper [{q}] HTTP: {r.status_code}');return r.json().get('shopping') or [] if r.ok else []
def search(q):
    if COUNTERS['resolve']>=MAX_RESOLVE:return []
    COUNTERS['resolve']+=1
    r=requests.post('https://google.serper.dev/search',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':q,'gl':'tr','hl':'tr','location':'Turkey','num':10},timeout=18)
    print(f'Serper link çözüm #{COUNTERS["resolve"]} [{q[:65]}] HTTP: {r.status_code}');return r.json().get('organic') or [] if r.ok else []
def resolve(it,site):
    raw=it.get('link') or '';title=re.sub(r'\s+',' ',it.get('title') or '').strip()
    if valid(raw,site):cset(site,title,raw);return raw
    cached,known=cget(site,title)
    if known:return cached if cached and valid(cached,site) else None
    for q in [f'site:{SITE_DOMAIN[site]} "{title[:150]}"',f'site:{SITE_DOMAIN[site]} {" ".join(title.split()[:9])}']:
        for r in search(q):
            link=r.get('link') or ''
            if valid(link,site) and match(title,r.get('title') or '',link):cset(site,title,link);print(f'Direkt ürün linki doğrulandı: {site} | {link[:150]}');return link
        if COUNTERS['resolve']>=MAX_RESOLVE:break
    cset(site,title,None);return None

def source_ref(it,current):
    vals=[]
    def walk(x,k=''):
        if isinstance(x,dict):
            for a,b in x.items():walk(b,str(a).lower())
        elif isinstance(x,list):
            for z in x:walk(z,k)
        elif any(y in k for y in ('oldprice','originalprice','listprice','beforeprice','wasprice','regularprice')):
            p=pprice(x)
            if p and current*1.03<p<=current*1.8:vals.append(p)
    walk(it);return statistics.median(vals) if vals else None

def render_verify(link,current,title):
    if COUNTERS['render']>=MAX_RENDER:return None,None,''
    COUNTERS['render']+=1
    try:
        with sync_playwright() as pw:
            b=pw.chromium.launch(headless=True);p=b.new_page(user_agent=bot.HEADERS.get('User-Agent'));p.goto(link,wait_until='domcontentloaded',timeout=18000);p.wait_for_timeout(1400)
            text=re.sub(r'\s+',' ',p.locator('body').inner_text(timeout=5000)).lower()
            bad=['stokta yok','stokta bulunmuyor','tükendi','tukendi','out of stock','sold out','currently unavailable']
            if any(x in text for x in bad):b.close();return False,None,''
            avail=True if any(x in text for x in ['sepete ekle','hemen al','şimdi al','simdi al','add to cart','buy now']) else None
            vals=[]
            for sel in ['.a-price .a-offscreen','[data-test-id="price-current-price"]','[class*="currentPrice"]','[class*="salePrice"]','.prc-dsc','.prc-slg','[itemprop="price"]']:
                try:
                    loc=p.locator(sel)
                    for i in range(min(loc.count(),6)):
                        e=loc.nth(i);v=e.get_attribute('content') or e.inner_text(timeout=500);x=pprice(v)
                        if x and current*.55<=x<=current*1.55:vals.append(x)
                except:pass
            try:ttl=re.sub(r'\s+',' ',p.locator('h1').first.inner_text(timeout=1000)).strip()
            except:ttl=''
            b.close();return avail,(min(vals,key=lambda x:abs(x-current)) if vals else None),ttl
    except Exception as e:print(f'RENDER HATA: {type(e).__name__} | {title[:60]}');return None,None,''

def process(it):
    site=site_of(it)
    if not site:return False
    STATS['seen']+=1;STATS[site.lower()]+=1
    listed=pprice(it.get('price'));title=re.sub(r'\s+',' ',it.get('title') or 'Ürün').strip()[:300]
    if not listed:STATS['no_price']+=1;return False
    link=resolve(it,site)
    if not link:STATS['no_link']+=1;return False
    pg=inspect_page(link,listed)
    if pg.get('available') is False:STATS['no_stock']+=1;print(f'STOK ENGELİ: {site} | stokta yok | {title[:75]} | {link[:120]}');return False
    if pg.get('available') is None:STATS['stock_unknown']+=1;print(f'STOK BELİRSİZ: {site} | sayfadan kesin doğrulanamadı | {title[:70]}')
    live=pg.get('live');camp=pg.get('campaign');current=listed
    if camp and camp.get('effective') and abs(listed-camp['effective'])/max(camp['effective'],1)<=.08:
        current=float(camp['effective']);STATS['campaign']+=1
    elif live and abs(live-listed)/max(live,1)>.03:
        current=live;STATS['live_corrected']+=1;print(f'CANLI FİYAT DÜZELTİLDİ: {site} | Serper={listed:.2f} -> Sayfa={current:.2f} | {title[:55]}')
    if pg.get('title') and match(title,pg['title'],link):title=pg['title'][:300]
    db=canonical(link);now=datetime.now(timezone.utc).isoformat();image=it.get('imageUrl') or pg.get('image')
    try:
        rows=bot.sb('GET','products',params={'select':'*','product_url':f'eq.{db}','limit':'1'});hist=bot.history(db)
        sref=source_ref(it,current);pref=(live if camp and live and live>current else pg.get('old'))
        floor=med=None;n=0
        if COUNTERS['market']<MAX_MARKET:
            COUNTERS['market']+=1;floor,med,n,src=market_snapshot(title)
            if med:print(f'PİYASA KONTROL: {site} | ürün={current:.2f} | en_ucuz={floor:.2f} | medyan={med:.2f} | n={n} | {title[:55]}')
        ref,kind=choose_reference(current,hist,sref,pref,med,floor)
        if kind=='market-blocked':STATS['market_blocked']+=1;print(f'PİYASA ENGELİ: {site} | ürün piyasa tabanından pahalı | {title[:65]}')
        if kind.startswith('market'):STATS['market_ref']+=1
        payload={'product_name':title,'current_price':current,'previous_price':ref,'product_url':db,'site':site,'updated_at':now}
        if rows:row=rows[0];bot.sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
        else:row=(bot.sb('POST','products',json=payload) or [payload])[0]
        bot.sb('POST','price_history',json={'price':current,'product_url':db,'site':site,'recorded_at':now})
        print(f'Kontrol: {site} | {current:.2f} TL | referans={ref or 0:.2f} | geçmiş={len(hist)} | kaynak={kind} | {title[:65]}')
        if not ref or ref<=current:STATS['no_ref']+=1;return False
        disc=(ref-current)/ref*100
        if disc<bot.MIN_DISCOUNT:STATS['below']+=1;return False
        # Huge discounts require a market-validated reference; this blocks fake list prices.
        if disc>45 and not med:
            STATS['no_ref']+=1;print(f'YÜKSEK İNDİRİM ENGELİ: %{disc:.1f} piyasa doğrulaması yok | {title[:65]}');return False
        last=row.get('last_posted_at') if isinstance(row,dict) else None
        if last:
            try:
                if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=bot.COOLDOWN):STATS['cooldown']+=1;return False
            except:pass
        av,rv,rt=render_verify(link,current,title)
        if av is False:STATS['render_stock']+=1;print(f'RENDER STOK ENGELİ: {site} | {title[:70]}');return False
        if av is None:STATS['render_unknown']+=1;print(f'RENDER DOĞRULANAMADI: {site} | aday paylaşılmadı | {title[:70]}');return False
        STATS['render_ok']+=1
        if rv and abs(rv-current)/max(current,1)>.03:
            oldc=current;current=rv;STATS['render_price']+=1;disc=(ref-current)/ref*100;print(f'RENDER FİYAT DÜZELTİLDİ: {site} | {oldc:.2f}->{current:.2f} | {title[:60]}')
            if disc<bot.MIN_DISCOUNT:STATS['below']+=1;return False
        if rt and match(title,rt,link):title=rt[:300]
        ol=outlink(link,site);safe=html.escape(ol,quote=True);lines=[f'⭐⭐⭐ 🔥 %{disc:.0f} İNDİRİM','',html.escape(title),'',f'💰 {fmt(current)} TL',f'🏷️ Referans fiyat: {fmt(ref)} TL',f'🛍️ {site}']
        if camp:
            lines.append(f'🎯 Kampanya: {html.escape(camp.get("label") or "Kampanyalı alım")}')
            if camp.get('qty'):lines.append(f'📦 {camp["qty"]} adet alımda geçerli')
        lines += ['',f'👇 <a href="{safe}"><b>Fırsata git</b></a>'];msg='\n'.join(lines);kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':ol}]]}
        if image:
            rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendPhoto',data={'chat_id':bot.CHANNEL_ID,'photo':image,'caption':msg[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},timeout=16)
            if not rr.ok:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':kb},timeout=12)
        else:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':kb},timeout=12)
        print(f'Telegram HTTP: {rr.status_code} | foto={"var" if image else "yok"}')
        if rr.ok:
            STATS['sent']+=1
            if isinstance(row,dict) and row.get('id'):bot.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
        return rr.ok
    except Exception as e:STATS['errors']+=1;print(f'İşlem hata: {type(e).__name__}: {e}');return False

def main():
    print(f'=== Serper V2 başladı | eşik=%{bot.MIN_DISCOUNT:g} | 10 kategori + piyasa/geçmiş + kampanya + stok ===');seen=set();matched=0
    for q in QUERIES:
        for it in shopping(q):
            site=site_of(it);pid=str(it.get('productId') or '').strip();title=re.sub(r'\s+',' ',it.get('title') or '').lower();k=(site,pid or title)
            if not site or k in seen:continue
            seen.add(k);matched+=1;process(it)
    print(f'=== Bitti. Hedef={matched} | Amazon={STATS["amazon"]} HB={STATS["hepsiburada"]} Trendyol={STATS["trendyol"]} | fiyat_yok={STATS["no_price"]} link_yok={STATS["no_link"]} stok_yok={STATS["no_stock"]} stok_belirsiz={STATS["stock_unknown"]} referans_yok={STATS["no_ref"]} esik_alti={STATS["below"]} cooldown={STATS["cooldown"]} hata={STATS["errors"]} | piyasa_engel={STATS["market_blocked"]} piyasa_ref={STATS["market_ref"]} kampanya={STATS["campaign"]} render_ok={STATS["render_ok"]} render_stok={STATS["render_stock"]} render_belirsiz={STATS["render_unknown"]} render_fiyat={STATS["render_price"]} | link_sorgu={COUNTERS["resolve"]} piyasa_sorgu={COUNTERS["market"]} render_sorgu={COUNTERS["render"]} | Gönderilen={STATS["sent"]} ===')
if __name__=='__main__':main()
