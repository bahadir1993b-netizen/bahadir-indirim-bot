import os,re,requests,hashlib,json,time,html
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, urlencode
from bs4 import BeautifulSoup
import bot
try:
    from price_reference import market_reference
except Exception:
    market_reference=lambda site,title,current:(None,'unavailable')

if bot.SUPABASE_URL.endswith('/rest/v1'):
    bot.SUPABASE_URL = bot.SUPABASE_URL[:-8].rstrip('/')

bot.MIN_DISCOUNT=float(os.environ.get('MIN_DISCOUNT','15'))
SERPER_API_KEY=os.environ['SERPER_API_KEY']
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
QUERIES=['elektronik indirim','ev yaşam indirim','telefon laptop kulaklık indirim','oyuncu televizyon küçük ev aletleri indirim']
TARGETS={'amazon.com.tr':'Amazon','www.amazon.com.tr':'Amazon','hepsiburada.com':'Hepsiburada','www.hepsiburada.com':'Hepsiburada','trendyol.com':'Trendyol','www.trendyol.com':'Trendyol'}
SITE_DOMAIN={'Amazon':'amazon.com.tr','Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
MAX_RESOLVE_PER_RUN=int(os.environ.get('MAX_RESOLVE_PER_RUN','24'))
MAX_MARKET_REF_PER_RUN=int(os.environ.get('MAX_MARKET_REF_PER_RUN','10'))
NEGATIVE_CACHE_SECONDS=int(os.environ.get('NEGATIVE_CACHE_SECONDS','7200'))
CACHE_FILE=Path('/app/data/link_cache.json');CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
_resolve_count=0;_market_ref_count=0
STATS={'seen':0,'no_price':0,'no_link':0,'no_ref':0,'below':0,'cooldown':0,'sent':0,'errors':0,'amazon':0,'hepsiburada':0,'trendyol':0,'page_ref':0,'market_ref':0,'source_ref':0,'history_ref':0}
try:_link_cache=json.loads(CACHE_FILE.read_text('utf-8')) if CACHE_FILE.exists() else {}
except Exception:_link_cache={}

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

def parse_price(v):
    if not v:return None
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

def item_reference_price(item,current):
    vals=[];likely_keys={'oldprice','originalprice','listprice','beforeprice','wasprice','regularprice','baseprice'}
    def walk(x,key=''):
        if isinstance(x,dict):
            for k,v in x.items():walk(v,str(k).lower())
        elif isinstance(x,list):
            for v in x:walk(v,key)
        elif key in likely_keys or any(z in key for z in ('oldprice','originalprice','listprice','beforeprice','wasprice','regularprice')):
            p=parse_price(x)
            if p and p>current*1.03 and p<=current*3.0:vals.append(p)
    walk(item);return max(vals) if vals else None

def canonical_for_db(link):
    p=urlparse(link);return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))
def clean_link(link,site):
    if not link:return ''
    p=urlparse(link);query=[]
    if site=='Amazon' and AMAZON_TAG:query=[('tag',AMAZON_TAG)]
    return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))+(('?'+urlencode(query)) if query else '')
def valid_product_url(link,site):
    try:
        p=urlparse(link);host=p.netloc.lower();path=p.path.rstrip('/');low=path.lower()
        if TARGETS.get(host)!=site or not path or 'yorumlari' in low or '/kategori/' in low:return False
        if site=='Amazon':return bool(re.search(r'/(?:dp|gp/product|gp/aw/d)/[A-Z0-9]{8,}(?:/|$)',path,re.I))
        if site=='Trendyol':return bool(re.search(r'-p-\d+(?:/|$)',path,re.I))
        if site=='Hepsiburada':return bool(re.search(r'-(?:pm-)?[A-Z0-9]{8,}(?:/|$)',path,re.I)) and not re.search(r'-c-\d+(?:/|$)',path,re.I)
    except Exception:return False

def meaningful_tokens(text):
    stop={'ve','ile','icin','için','bir','yeni','set','siyah','beyaz','urun','ürün','model','adet','the','bluetooth','kablosuz'}
    return {x for x in re.findall(r'[a-z0-9çğıöşü]+',(text or '').lower()) if len(x)>=4 and x not in stop}
def result_matches_title(item_title,result_title,link):
    a=meaningful_tokens(item_title);b=meaningful_tokens((result_title or '')+' '+urlparse(link).path.replace('-',' '));common=a&b
    return bool(a) and (len(common)>=2 or (len(a)<=2 and len(common)>=1) or len(common)/max(1,len(a))>=0.45)
def serper_shopping(query):
    r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':query,'gl':'tr','hl':'tr','location':'Turkey','num':100},timeout=20)
    print(f'Serper [{query}] HTTP: {r.status_code}')
    if not r.ok:print(r.text[:300]);return []
    return r.json().get('shopping') or []
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
    source=(item.get('source') or '').lower()
    if 'amazon' in source:return 'Amazon'
    if 'hepsiburada' in source:return 'Hepsiburada'
    if 'trendyol' in source:return 'Trendyol'
    return None
def resolve_direct_link(item,site):
    raw=item.get('link') or '';title=re.sub(r'\s+',' ',item.get('title') or '').strip()
    if valid_product_url(raw,site):cache_set(site,title,raw);return raw
    cached,known=cache_get(site,title)
    if known:
        if cached and valid_product_url(cached,site):print(f'Link cache kullanıldı: {site} | {cached[:130]}');return cached
        print(f'Negatif link cache; tekrar sorgulanmadı: {site} | {title[:75]}');return None
    if not title:return None
    if _resolve_count>=MAX_RESOLVE_PER_RUN:print(f'Link çözüm limiti doldu; ürün sonraki tura bırakıldı: {site} | {title[:75]}');return None
    queries=[f'site:{SITE_DOMAIN[site]} "{title[:140]}"',f'site:{SITE_DOMAIN[site]} {" ".join(title.split()[:8])}']
    for q in queries:
        for result in serper_search(q):
            link=result.get('link') or ''
            if valid_product_url(link,site) and result_matches_title(title,result.get('title') or '',link):
                print(f'Direkt ürün linki doğrulandı: {site} | {link[:150]}');cache_set(site,title,link);return link
        if _resolve_count>=MAX_RESOLVE_PER_RUN:break
    print(f'Güvenilir direkt ürün linki bulunamadı; {NEGATIVE_CACHE_SECONDS//3600} saat cachelendi: {site} | {title[:90]}');cache_set(site,title,None);return None
def product_identity(item,site,direct_link):
    if direct_link:return canonical_for_db(direct_link)
    pid=str(item.get('productId') or '').strip()
    if pid:return f'https://serper.local/{site.lower()}/{pid}'
    title=re.sub(r'\s+',' ',item.get('title') or '').strip().lower();return f'https://serper.local/{site.lower()}/{hashlib.sha1(f"{site}|{title}".encode()).hexdigest()}'

def product_page_meta(link,current):
    image='';refs=[]
    try:
        r=requests.get(link,headers=bot.HEADERS,timeout=9,allow_redirects=True)
        if not r.ok:return image,None
        soup=BeautifulSoup(r.text,'html.parser')
        for sel,attr in [('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('img[itemprop="image"]','src')]:
            e=soup.select_one(sel)
            if e and (e.get(attr) or '').startswith('http'):image=e.get(attr);break
        selectors=['del','s','.old-price','.list-price','.price-old','[class*="oldPrice"]','[class*="old-price"]','[class*="listPrice"]','[data-test-id*="old"]','[data-test-id*="list"]']
        for sel in selectors:
            for e in soup.select(sel):
                p=parse_price(e.get('content') or e.get('data-price') or e.get_text(' ',strip=True))
                if p and current*1.03<p<=current*3.0:refs.append(p)
        txt=soup.get_text(' ',strip=True)
        for pat in [r'(?:eski fiyat|liste fiyatı|liste fiyati|normal fiyat|önceki fiyat|onceki fiyat)\s*[:\-]?\s*([\d.,]+)\s*(?:TL|₺)',r'([\d.,]+)\s*(?:TL|₺)\s*yerine']:
            for m in re.finditer(pat,txt,re.I):
                p=parse_price(m.group(1))
                if p and current*1.03<p<=current*3.0:refs.append(p)
    except Exception:pass
    return image,(max(refs) if refs else None)

def product_photo(link,existing=''):
    if existing:return existing
    return product_page_meta(link,1)[0]

def process_item(item):
    global _market_ref_count
    site=site_from_item(item)
    if not site:return False
    STATS['seen']+=1;STATS[site.lower()]+=1
    current=parse_price(item.get('price'))
    if current is None:STATS['no_price']+=1;return False
    title=re.sub(r'\s+',' ',item.get('title') or 'Ürün').strip()[:300]
    direct_link=resolve_direct_link(item,site)
    if not direct_link:STATS['no_link']+=1;return False
    db_url=product_identity(item,site,direct_link);page_image,page_ref=product_page_meta(direct_link,current);image=item.get('imageUrl') or page_image;now=datetime.now(timezone.utc).isoformat()
    try:
        rows=bot.sb('GET','products',params={'select':'*','product_url':f'eq.{db_url}','limit':'1'});old=bot.history(db_url)
        hist_ref=max((x for x in old if x>current*1.03 and x<=current*3.0),default=None)
        source_ref=item_reference_price(item,current)
        if hist_ref:STATS['history_ref']+=1
        if source_ref:STATS['source_ref']+=1
        if page_ref:STATS['page_ref']+=1
        prev=max([x for x in (hist_ref,source_ref,page_ref) if x],default=None);ref_kind='local'
        if prev is None and _market_ref_count<MAX_MARKET_REF_PER_RUN:
            _market_ref_count+=1
            mr,kind=market_reference(site,title,current)
            if mr and current*1.08<mr<=current*3.0:
                prev=mr;ref_kind=kind;STATS['market_ref']+=1;print(f'Piyasa referansı bulundu: {site} | {current:.2f}->{mr:.2f} | {kind} | {title[:60]}')
        payload={'product_name':title,'current_price':current,'previous_price':prev,'product_url':db_url,'site':site,'updated_at':now}
        if rows:row=rows[0];bot.sb('PATCH',f'products?id=eq.{row[0]["id"] if isinstance(row,list) else row["id"]}',json=payload) if False else None
        if rows:
            row=rows[0];bot.sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
        else:row=(bot.sb('POST','products',json=payload) or [payload])[0]
        bot.sb('POST','price_history',json={'price':current,'product_url':db_url,'site':site,'recorded_at':now})
        print(f'Kontrol: {site} | {current:.2f} TL | referans={prev or 0:.2f} | geçmiş={len(old)} | kaynak={ref_kind} | {title[:60]}')
        if prev is None or prev<=current:STATS['no_ref']+=1;return False
        disc=(prev-current)/prev*100
        if disc<bot.MIN_DISCOUNT:STATS['below']+=1;return False
        last=row.get('last_posted_at') if isinstance(row,dict) else None
        if last:
            try:
                if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<bot.timedelta(hours=bot.COOLDOWN):STATS['cooldown']+=1;return False
            except:pass
        link=clean_link(direct_link,site);safe_link=html.escape(link,quote=True);safe_title=html.escape(title)
        msg=f'⭐⭐⭐ 🔥 %{disc:.0f} İNDİRİM\n\n{safe_title}\n\n💰 {current:,.2f} TL\n🏷️ Referans: {prev:,.2f} TL\n🛍️ {site}\n\n👇 <a href="{safe_link}"><b>Fırsata git</b></a>'
        keyboard={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':link}]]}
        if image:
            rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendPhoto',data={'chat_id':bot.CHANNEL_ID,'photo':image,'caption':msg[:1024],'parse_mode':'HTML','reply_markup':json.dumps(keyboard,ensure_ascii=False)},timeout=15)
            if not rr.ok:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':keyboard},timeout=12)
        else:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':keyboard},timeout=12)
        print(f'Telegram HTTP: {rr.status_code} | link={link[:120]} | foto={"var" if image else "yok"}')
        if rr.ok:
            STATS['sent']+=1
            if isinstance(row,dict) and row.get('id'):bot.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now,'last_posted_price':current})
        return rr.ok
    except Exception as e:STATS['errors']+=1;print(f'İşlem hata: {type(e).__name__}: {e}');return False

def main():
    global _resolve_count,_market_ref_count;_resolve_count=0;_market_ref_count=0
    print(f'=== Serper alışveriş botu başladı | eşik=%{bot.MIN_DISCOUNT:g} | link_limit={MAX_RESOLVE_PER_RUN} | piyasa_ref_limit={MAX_MARKET_REF_PER_RUN} ===')
    seen=set();matched=0
    for q in QUERIES:
        for item in serper_shopping(q):
            site=site_from_item(item);pid=str(item.get('productId') or '').strip();title=re.sub(r'\s+',' ',item.get('title') or '').strip().lower();key=(site,pid or title)
            if not site or key in seen:continue
            seen.add(key);matched+=1;process_item(item)
    print(f'=== Bitti. Hedef={matched} | Amazon={STATS["amazon"]} HB={STATS["hepsiburada"]} Trendyol={STATS["trendyol"]} | fiyat_yok={STATS["no_price"]} link_yok={STATS["no_link"]} referans_yok={STATS["no_ref"]} esik_alti={STATS["below"]} cooldown={STATS["cooldown"]} hata={STATS["errors"]} | ref: geçmiş={STATS["history_ref"]} serper={STATS["source_ref"]} sayfa={STATS["page_ref"]} piyasa={STATS["market_ref"]} | link_sorgu={_resolve_count} piyasa_sorgu={_market_ref_count} | Gönderilen={STATS["sent"]} ===')
if __name__=='__main__':main()
