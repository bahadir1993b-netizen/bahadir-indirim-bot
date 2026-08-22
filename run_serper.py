import os,re,requests,hashlib,json,time,html,statistics
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,urlunparse,urlencode
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import bot
from price_reference import market_snapshot

if bot.SUPABASE_URL.endswith('/rest/v1'):
    bot.SUPABASE_URL=bot.SUPABASE_URL[:-8].rstrip('/')

bot.MIN_DISCOUNT=float(os.environ.get('MIN_DISCOUNT','15'))
SERPER_API_KEY=os.environ['SERPER_API_KEY']
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
QUERIES=['elektronik indirim','ev yaşam indirim','telefon laptop kulaklık indirim','oyuncu televizyon küçük ev aletleri indirim']
TARGETS={'amazon.com.tr':'Amazon','www.amazon.com.tr':'Amazon','hepsiburada.com':'Hepsiburada','www.hepsiburada.com':'Hepsiburada','trendyol.com':'Trendyol','www.trendyol.com':'Trendyol'}
SITE_DOMAIN={'Amazon':'amazon.com.tr','Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
MAX_RESOLVE_PER_RUN=int(os.environ.get('MAX_RESOLVE_PER_RUN','24'))
MAX_MARKET_CHECKS=int(os.environ.get('MAX_MARKET_REF_PER_RUN','14'))
MAX_RENDER_CHECKS=int(os.environ.get('MAX_RENDER_CHECKS','8'))
NEGATIVE_CACHE_SECONDS=int(os.environ.get('NEGATIVE_CACHE_SECONDS','7200'))
CACHE_FILE=Path('/app/data/link_cache.json');CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
_resolve_count=0;_market_checks=0;_render_checks=0
STATS={'seen':0,'no_price':0,'no_link':0,'no_ref':0,'below':0,'cooldown':0,'sent':0,'errors':0,'amazon':0,'hepsiburada':0,'trendyol':0,'live_corrected':0,'market_blocked':0,'market_ref':0,'page_ref':0,'history_ref':0,'source_ref':0,'title_enriched':0,'no_stock':0,'stock_unknown':0,'render_verified':0,'render_stock_blocked':0,'render_unknown_blocked':0,'render_price_corrected':0}
try:_link_cache=json.loads(CACHE_FILE.read_text('utf-8')) if CACHE_FILE.exists() else {}
except Exception:_link_cache={}

def parse_price(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v)).strip()
    if not s:return None
    if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:
        x=float(s);return x if 1<x<10000000 else None
    except:return None

def cache_key(site,title):return hashlib.sha1(f'{site}|{re.sub(r"\s+"," ",title or "").strip().lower()}'.encode()).hexdigest()
def cache_get(site,title):
    x=_link_cache.get(cache_key(site,title))
    if not isinstance(x,dict):return None,False
    age=time.time()-float(x.get('ts') or 0);link=x.get('link') or ''
    if link and age<30*86400:return link,True
    if not link and age<NEGATIVE_CACHE_SECONDS:return None,True
    return None,False
def cache_set(site,title,link):
    _link_cache[cache_key(site,title)]={'link':link or '','ts':time.time()}
    try:CACHE_FILE.write_text(json.dumps(_link_cache,ensure_ascii=False),'utf-8')
    except Exception:pass

def canonical_for_db(link):
    p=urlparse(link);return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))
def clean_link(link,site):
    p=urlparse(link);q=[('tag',AMAZON_TAG)] if site=='Amazon' and AMAZON_TAG else []
    return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))+(('?'+urlencode(q)) if q else '')
def valid_product_url(link,site):
    try:
        p=urlparse(link);host=p.netloc.lower();path=p.path.rstrip('/');low=path.lower()
        if TARGETS.get(host)!=site or not path or 'yorumlari' in low or '/kategori/' in low:return False
        if site=='Amazon':return bool(re.search(r'/(?:dp|gp/product|gp/aw/d)/[A-Z0-9]{8,}(?:/|$)',path,re.I))
        if site=='Trendyol':return bool(re.search(r'-p-\d+(?:/|$)',path,re.I))
        if site=='Hepsiburada':return bool(re.search(r'-(?:pm-)?[A-Z0-9]{8,}(?:/|$)',path,re.I)) and not re.search(r'-c-\d+(?:/|$)',path,re.I)
    except:return False

def tokens(text):
    stop={'ve','ile','icin','için','bir','yeni','set','siyah','beyaz','urun','ürün','model','adet','the','bluetooth','kablosuz'}
    return {x for x in re.findall(r'[a-z0-9çğıöşü]{2,}',(text or '').lower()) if x not in stop}
def matches(a,b,link=''):
    aa=tokens(a);bb=tokens((b or '')+' '+urlparse(link).path.replace('-',' '));common=aa&bb
    return bool(aa) and (len(common)>=2 or len(common)/max(1,len(aa))>=0.42)

def serper_shopping(query):
    r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':query,'gl':'tr','hl':'tr','location':'Turkey','num':100},timeout=20)
    print(f'Serper [{query}] HTTP: {r.status_code}')
    return r.json().get('shopping') or [] if r.ok else []
def serper_search(query):
    global _resolve_count
    if _resolve_count>=MAX_RESOLVE_PER_RUN:return []
    _resolve_count+=1
    r=requests.post('https://google.serper.dev/search',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':query,'gl':'tr','hl':'tr','location':'Turkey','num':10},timeout=20)
    print(f'Serper link çözüm #{_resolve_count} [{query[:65]}] HTTP: {r.status_code}')
    return r.json().get('organic') or [] if r.ok else []
def site_from_item(item):
    host=urlparse(item.get('link') or '').netloc.lower()
    if host in TARGETS:return TARGETS[host]
    s=(item.get('source') or '').lower()
    if 'amazon' in s:return 'Amazon'
    if 'hepsiburada' in s:return 'Hepsiburada'
    if 'trendyol' in s:return 'Trendyol'
    return None

def resolve_direct_link(item,site):
    raw=item.get('link') or '';title=re.sub(r'\s+',' ',item.get('title') or '').strip()
    if valid_product_url(raw,site):cache_set(site,title,raw);return raw
    cached,known=cache_get(site,title)
    if known:
        if cached and valid_product_url(cached,site):print(f'Link cache kullanıldı: {site} | {cached[:130]}');return cached
        print(f'Negatif link cache; tekrar sorgulanmadı: {site} | {title[:75]}');return None
    if not title:return None
    for q in [f'site:{SITE_DOMAIN[site]} "{title[:140]}"',f'site:{SITE_DOMAIN[site]} {" ".join(title.split()[:9])}']:
        if _resolve_count>=MAX_RESOLVE_PER_RUN:break
        for result in serper_search(q):
            link=result.get('link') or ''
            if valid_product_url(link,site) and matches(title,result.get('title') or '',link):
                print(f'Direkt ürün linki doğrulandı: {site} | {link[:150]}');cache_set(site,title,link);return link
    if _resolve_count>=MAX_RESOLVE_PER_RUN:print(f'Link çözüm limiti doldu; ürün sonraki tura bırakıldı: {site} | {title[:75]}')
    else:print(f'Güvenilir direkt ürün linki bulunamadı; {NEGATIVE_CACHE_SECONDS//3600} saat cachelendi: {site} | {title[:90]}')
    cache_set(site,title,None);return None

def item_reference_price(item,current):
    vals=[]
    def walk(x,key=''):
        if isinstance(x,dict):
            for k,v in x.items():walk(v,str(k).lower())
        elif isinstance(x,list):
            for v in x:walk(v,key)
        elif any(z in key for z in ('oldprice','originalprice','listprice','beforeprice','wasprice','regularprice')):
            p=parse_price(x)
            if p and current*1.03<p<=current*2.2:vals.append(p)
    walk(item);return max(vals) if vals else None

def _availability_from_json(obj):
    vals=[]
    def walk(x,key=''):
        if isinstance(x,dict):
            for k,v in x.items():walk(v,str(k).lower())
        elif isinstance(x,list):
            for v in x:walk(v,key)
        elif 'availability' in key or key in ('stock','stockstatus','availabilitystatus','instock','soldout'):
            vals.append(str(x).lower())
    walk(obj)
    if any(any(z in v for z in ('outofstock','out_of_stock','soldout','sold_out','false','tükendi','tukendi','stokta yok')) for v in vals):return False
    if any(any(z in v for z in ('instock','in_stock','limitedavailability','preorder','true','stokta')) for v in vals):return True
    return None

def page_info(link,serper_current,serper_title):
    out={'image':'','title':'','live':None,'old':None,'available':None}
    try:
        r=requests.get(link,headers=bot.HEADERS,timeout=10,allow_redirects=True)
        if not r.ok:return out
        raw=r.text.lower();soup=BeautifulSoup(r.text,'html.parser');full_text=re.sub(r'\s+',' ',soup.get_text(' ',strip=True)).lower()
        out_patterns=['stokta yok','stokta bulunmamaktadır','stokta bulunmuyor','ürün tükendi','urun tukendi','tükendi','tukendi','satışa kapalı','satisa kapali','currently unavailable','temporarily out of stock','out of stock','sold out']
        raw_patterns=['"instock":false','"inStock":false'.lower(),'"soldout":true','"soldOut":true'.lower(),'"stock":0','"availability":"out_of_stock"','"availability":"outofstock"']
        if any(x in full_text for x in out_patterns) or any(x in raw for x in raw_patterns):out['available']=False
        for sc in soup.select('script[type="application/ld+json"]'):
            try:
                obj=json.loads(sc.string or sc.get_text() or '{}');av=_availability_from_json(obj)
                if av is False:out['available']=False
                elif av is True and out['available'] is None:out['available']=True
            except Exception:pass
        for e in soup.select('button,[role="button"]'):
            txt=re.sub(r'\s+',' ',e.get_text(' ',strip=True)).lower()
            if any(z in txt for z in ('stokta yok','tükendi','tukendi','sold out','out of stock')):out['available']=False
        for sel,attr in [('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('img[itemprop="image"]','src')]:
            e=soup.select_one(sel)
            if e and (e.get(attr) or '').startswith('http'):out['image']=e.get(attr);break
        for sel,attr in [('meta[property="og:title"]','content'),('h1',None),('title',None)]:
            e=soup.select_one(sel)
            if e:
                t=(e.get(attr) if attr else e.get_text(' ',strip=True)) or '';t=re.sub(r'\s+',' ',t).strip()
                if len(t)>=8 and matches(serper_title,t,link):out['title']=t[:300];break
        live=[];old=[]
        for sel,attrs in [('meta[itemprop="price"]',['content']),('meta[property="product:price:amount"]',['content']),('[itemprop="price"]',['content','data-price']),('.a-price .a-offscreen',[None]),('[data-test-id="price-current-price"]',[None]),('[class*="currentPrice"]',[None]),('[class*="salePrice"]',[None]),('.prc-dsc',[None]),('.prc-slg',[None])]:
            for e in soup.select(sel):
                for a in attrs:
                    p=parse_price(e.get(a) if a else e.get_text(' ',strip=True))
                    if p and serper_current*0.45<=p<=serper_current*1.55:live.append(p)
        for sel in ['del','s','.old-price','.list-price','[class*="oldPrice"]','[class*="listPrice"]']:
            for e in soup.select(sel):
                p=parse_price(e.get('content') or e.get('data-price') or e.get_text(' ',strip=True))
                if p and serper_current*1.03<p<=serper_current*2.2:old.append(p)
        if live:out['live']=min(live)
        if old:out['old']=max(old)
        if out['available'] is None and out['live'] is not None:out['available']=True
    except Exception as e:print(f'Sayfa okuma hata: {type(e).__name__}')
    return out

def rendered_verify(link,site,current,title):
    global _render_checks
    if _render_checks>=MAX_RENDER_CHECKS:return None,None,''
    _render_checks+=1
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(user_agent=bot.HEADERS.get('User-Agent'))
            page.goto(link,wait_until='domcontentloaded',timeout=18000)
            page.wait_for_timeout(1800)
            text=re.sub(r'\s+',' ',page.locator('body').inner_text(timeout=5000)).lower()
            rendered_title=''
            try:rendered_title=re.sub(r'\s+',' ',page.locator('h1').first.inner_text(timeout=1500)).strip()
            except Exception:pass
            bad=['stokta yok','stokta bulunmamaktadır','stokta bulunmuyor','ürün tükendi','urun tukendi','tükendi','tukendi','currently unavailable','out of stock','sold out','satışa kapalı','satisa kapali']
            if any(x in text for x in bad):
                browser.close();return False,None,rendered_title
            # Explicit purchasable controls are the strongest positive signal.
            good=['sepete ekle','hemen al','şimdi al','simdi al','add to cart','buy now']
            available=True if any(x in text for x in good) else None
            vals=[]
            selectors=['.a-price .a-offscreen','[data-test-id="price-current-price"]','[class*="currentPrice"]','[class*="salePrice"]','.prc-dsc','.prc-slg','[itemprop="price"]']
            for sel in selectors:
                try:
                    for i in range(min(page.locator(sel).count(),6)):
                        e=page.locator(sel).nth(i);v=e.get_attribute('content') or e.inner_text(timeout=800)
                        p=parse_price(v)
                        if p and current*0.45<=p<=current*1.55:vals.append(p)
                except Exception:pass
            browser.close();return available,(min(vals) if vals else None),rendered_title
    except Exception as e:
        print(f'RENDER HATA: {site} | {type(e).__name__} | {title[:60]}');return None,None,''

def product_identity(link):return canonical_for_db(link)
def enrich_title(base,page_title):
    if not page_title:return base
    better=page_title if len(tokens(page_title))>=len(tokens(base)) else base
    better=re.sub(r'\s*[-|]\s*(Amazon.*|Hepsiburada.*|Trendyol.*)$','',better,flags=re.I).strip()
    return better[:300]

def process_item(item):
    global _market_checks
    site=site_from_item(item)
    if not site:return False
    STATS['seen']+=1;STATS[site.lower()]+=1
    serper_current=parse_price(item.get('price'))
    if not serper_current:STATS['no_price']+=1;return False
    base_title=re.sub(r'\s+',' ',item.get('title') or 'Ürün').strip()[:300]
    link=resolve_direct_link(item,site)
    if not link:STATS['no_link']+=1;return False
    pg=page_info(link,serper_current,base_title)
    if pg['available'] is False:
        STATS['no_stock']+=1;print(f'STOK ENGELİ: {site} | stokta yok | {base_title[:75]} | {link[:120]}');return False
    if pg['available'] is None:
        STATS['stock_unknown']+=1;print(f'STOK BELİRSİZ: {site} | sayfadan kesin doğrulanamadı | {base_title[:70]}')
    current=serper_current
    if pg['live'] and abs(pg['live']-serper_current)/serper_current>=0.025:
        current=pg['live'];STATS['live_corrected']+=1;print(f'CANLI FİYAT DÜZELTİLDİ: {site} | Serper={serper_current:.2f} -> Sayfa={current:.2f} | {base_title[:55]}')
    title=enrich_title(base_title,pg['title'])
    if title!=base_title:STATS['title_enriched']+=1;print(f'BAŞLIK ZENGİNLEŞTİ: {title[:100]}')
    db_url=product_identity(link);now=datetime.now(timezone.utc).isoformat();image=item.get('imageUrl') or pg['image']
    try:
        rows=bot.sb('GET','products',params={'select':'*','product_url':f'eq.{db_url}','limit':'1'});old=bot.history(db_url)
        higher=[x for x in old if current*1.03<x<=current*2.2];hist_ref=float(statistics.median(higher)) if higher else None
        source_ref=item_reference_price(item,current);page_ref=pg['old'] if pg['old'] and pg['old']>current else None
        if hist_ref:STATS['history_ref']+=1
        if source_ref:STATS['source_ref']+=1
        if page_ref:STATS['page_ref']+=1
        raw_ref=max([x for x in (hist_ref,source_ref,page_ref) if x],default=None)
        market_floor=market_med=None;market_n=0;candidate=bool(raw_ref and (raw_ref-current)/raw_ref*100>=bot.MIN_DISCOUNT)
        if _market_checks<MAX_MARKET_CHECKS and (candidate or raw_ref is None):
            _market_checks+=1;market_floor,market_med,market_n,msrc=market_snapshot(title)
            if market_med:print(f'PİYASA KONTROL: {site} | ürün={current:.2f} | en_ucuz={market_floor:.2f} | medyan={market_med:.2f} | n={market_n} | {title[:55]}')
        prev=raw_ref;ref_kind='yerel'
        if market_med:
            if market_floor and current>market_floor*1.05:
                STATS['market_blocked']+=1;print(f'PİYASA ENGELİ: {site} | {current:.2f} > piyasa en ucuz {market_floor:.2f} | {title[:65]}');prev=None
            elif current<market_med*(1-bot.MIN_DISCOUNT/100):
                prev=min(raw_ref,market_med) if raw_ref else market_med;ref_kind='piyasa';STATS['market_ref']+=1
            elif candidate:
                STATS['market_blocked']+=1;print(f'PİYASA ENGELİ: {site} | medyana göre indirim yetersiz | {title[:65]}');prev=None
        payload={'product_name':title,'current_price':current,'previous_price':prev,'product_url':db_url,'site':site,'updated_at':now}
        if rows:row=rows[0];bot.sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
        else:row=(bot.sb('POST','products',json=payload) or [payload])[0]
        bot.sb('POST','price_history',json={'price':current,'product_url':db_url,'site':site,'recorded_at':now})
        print(f'Kontrol: {site} | {current:.2f} TL | referans={prev or 0:.2f} | geçmiş={len(old)} | kaynak={ref_kind} | {title[:65]}')
        if not prev or prev<=current:STATS['no_ref']+=1;return False
        disc=(prev-current)/prev*100
        if disc<bot.MIN_DISCOUNT:STATS['below']+=1;return False
        last=row.get('last_posted_at') if isinstance(row,dict) else None
        if last:
            try:
                if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=bot.COOLDOWN):STATS['cooldown']+=1;return False
            except:pass
        # Final browser-rendered verification: fail closed for candidate deals.
        rav,rlive,rtitle=rendered_verify(link,site,current,title)
        if rav is False:
            STATS['render_stock_blocked']+=1;print(f'RENDER STOK ENGELİ: {site} | {title[:70]}');return False
        if rav is None:
            STATS['render_unknown_blocked']+=1;print(f'RENDER DOĞRULANAMADI: {site} | aday paylaşılmadı | {title[:70]}');return False
        STATS['render_verified']+=1
        if rlive and abs(rlive-current)/current>=0.025:
            old_current=current;current=rlive;STATS['render_price_corrected']+=1
            print(f'RENDER FİYAT DÜZELTİLDİ: {site} | {old_current:.2f}->{current:.2f} | {title[:60]}')
            disc=(prev-current)/prev*100
            if disc<bot.MIN_DISCOUNT:
                STATS['below']+=1;print(f'RENDER SONRASI EŞİK ALTI: %{disc:.1f} | {title[:65]}');return False
        if rtitle:
            nt=enrich_title(title,rtitle)
            if nt!=title:title=nt;STATS['title_enriched']+=1;print(f'RENDER BAŞLIK ZENGİNLEŞTİ: {title[:100]}')
        if isinstance(row,dict) and row.get('id'):
            bot.sb('PATCH',f'products?id=eq.{row["id"]}',json={'product_name':title,'current_price':current,'previous_price':prev,'updated_at':datetime.now(timezone.utc).isoformat()})
        out_link=clean_link(link,site);safe=html.escape(out_link,quote=True);safe_title=html.escape(title)
        msg=f'⭐⭐⭐ 🔥 %{disc:.0f} İNDİRİM\n\n{safe_title}\n\n💰 {current:,.2f} TL\n🏷️ Referans: {prev:,.2f} TL\n🛍️ {site}\n\n👇 <a href="{safe}"><b>Fırsata git</b></a>'
        keyboard={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':out_link}]]}
        if image:
            rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendPhoto',data={'chat_id':bot.CHANNEL_ID,'photo':image,'caption':msg[:1024],'parse_mode':'HTML','reply_markup':json.dumps(keyboard,ensure_ascii=False)},timeout=15)
            if not rr.ok:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':keyboard},timeout=12)
        else:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':keyboard},timeout=12)
        print(f'Telegram HTTP: {rr.status_code} | foto={"var" if image else "yok"}')
        if rr.ok:
            STATS['sent']+=1
            if isinstance(row,dict) and row.get('id'):bot.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
        return rr.ok
    except Exception as e:
        STATS['errors']+=1;print(f'İşlem hata: {type(e).__name__}: {e}');return False

def main():
    global _resolve_count,_market_checks,_render_checks
    _resolve_count=0;_market_checks=0;_render_checks=0
    print(f'=== Serper alışveriş botu başladı | eşik=%{bot.MIN_DISCOUNT:g} | piyasa + render stok/fiyat koruması ===')
    seen=set();matched=0
    for q in QUERIES:
        for item in serper_shopping(q):
            site=site_from_item(item);pid=str(item.get('productId') or '').strip();title=re.sub(r'\s+',' ',item.get('title') or '').strip().lower();key=(site,pid or title)
            if not site or key in seen:continue
            seen.add(key);matched+=1;process_item(item)
    print(f'=== Bitti. Hedef={matched} | Amazon={STATS["amazon"]} HB={STATS["hepsiburada"]} Trendyol={STATS["trendyol"]} | fiyat_yok={STATS["no_price"]} link_yok={STATS["no_link"]} stok_yok={STATS["no_stock"]} stok_belirsiz={STATS["stock_unknown"]} referans_yok={STATS["no_ref"]} esik_alti={STATS["below"]} cooldown={STATS["cooldown"]} hata={STATS["errors"]} | piyasa_engel={STATS["market_blocked"]} piyasa_ref={STATS["market_ref"]} render_ok={STATS["render_verified"]} render_stok={STATS["render_stock_blocked"]} render_belirsiz={STATS["render_unknown_blocked"]} render_fiyat={STATS["render_price_corrected"]} | link_sorgu={_resolve_count} piyasa_sorgu={_market_checks} render_sorgu={_render_checks} | Gönderilen={STATS["sent"]} ===')
if __name__=='__main__':main()
