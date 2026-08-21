import os,re,requests,hashlib,json,time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, urlencode
import bot

if bot.SUPABASE_URL.endswith('/rest/v1'):
    bot.SUPABASE_URL = bot.SUPABASE_URL[:-8].rstrip('/')

SERPER_API_KEY=os.environ['SERPER_API_KEY']
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
QUERIES=['elektronik indirim','ev yaşam indirim','telefon laptop kulaklık indirim','oyuncu televizyon küçük ev aletleri indirim']
TARGETS={
    'amazon.com.tr':'Amazon','www.amazon.com.tr':'Amazon',
    'hepsiburada.com':'Hepsiburada','www.hepsiburada.com':'Hepsiburada',
    'trendyol.com':'Trendyol','www.trendyol.com':'Trendyol',
}
SITE_DOMAIN={'Amazon':'amazon.com.tr','Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
MAX_RESOLVE_PER_RUN=12
CACHE_FILE=Path('/app/data/link_cache.json')
CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
_resolve_count=0
try:
    _link_cache=json.loads(CACHE_FILE.read_text('utf-8')) if CACHE_FILE.exists() else {}
except Exception:
    _link_cache={}

def cache_key(site,title):
    t=re.sub(r'\s+',' ',title or '').strip().lower()
    return hashlib.sha1(f'{site}|{t}'.encode('utf-8')).hexdigest()

def cache_get(site,title):
    x=_link_cache.get(cache_key(site,title))
    if not isinstance(x,dict):return None,False
    age=time.time()-float(x.get('ts') or 0)
    link=x.get('link') or ''
    if link and age < 30*86400:return link,True
    if not link and age < 12*3600:return None,True
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
    """Serper sonucunda varsa çizili/liste/eski fiyatı güvenli biçimde bul."""
    vals=[]
    likely_keys={'oldprice','originalprice','listprice','beforeprice','wasprice','regularprice','baseprice'}
    def walk(x,key=''):
        if isinstance(x,dict):
            for k,v in x.items():walk(v,str(k).lower())
        elif isinstance(x,list):
            for v in x:walk(v,key)
        elif key in likely_keys or any(z in key for z in ('oldprice','originalprice','listprice','beforeprice','wasprice','regularprice')):
            p=parse_price(x)
            if p and p>current*1.03 and p<=current*5:vals.append(p)
    walk(item)
    return max(vals) if vals else None

def canonical_for_db(link):
    p=urlparse(link)
    return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))

def clean_link(link,site):
    if not link:return ''
    p=urlparse(link);host=p.netloc.lower();query=[]
    if site=='Amazon' and AMAZON_TAG:query=[('tag',AMAZON_TAG)]
    return urlunparse(('https',host,p.path.rstrip('/'),'','','')) + (('?'+urlencode(query)) if query else '')

def valid_product_url(link,site):
    try:
        p=urlparse(link);host=p.netloc.lower();path=p.path.rstrip('/')
        if TARGETS.get(host)!=site:return False
        low=path.lower()
        if not path or low.endswith('/yorumlari') or '/kategori/' in low:return False
        if site=='Amazon':return bool(re.search(r'/(?:dp|gp/product|gp/aw/d)/[A-Z0-9]{8,}(?:/|$)',path,re.I))
        if site=='Trendyol':return bool(re.search(r'-p-\d+(?:/|$)',path,re.I))
        if site=='Hepsiburada':return bool(re.search(r'-(?:pm-)?[A-Z0-9]{8,}(?:/|$)',path,re.I)) and not re.search(r'-c-\d+(?:/|$)',path,re.I)
    except Exception:return False

def meaningful_tokens(text):
    stop={'ve','ile','icin','için','bir','yeni','set','siyah','beyaz','urun','ürün','model','adet','the'}
    return {x for x in re.findall(r'[a-z0-9]+',(text or '').lower()) if len(x)>=4 and x not in stop}

def result_matches_title(item_title,result_title,link):
    a=meaningful_tokens(item_title);b=meaningful_tokens((result_title or '')+' '+urlparse(link).path.replace('-',' '))
    if not a:return False
    common=a & b
    return len(common)>=2 or (len(a)<=2 and len(common)>=1)

def serper_shopping(query):
    r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':query,'gl':'tr','hl':'tr','location':'Turkey','num':100},timeout=20)
    print(f'Serper [{query}] HTTP: {r.status_code}')
    if not r.ok:
        print(r.text[:300]);return []
    return r.json().get('shopping') or []

def serper_search(query):
    global _resolve_count
    if _resolve_count>=MAX_RESOLVE_PER_RUN:return []
    _resolve_count+=1
    r=requests.post('https://google.serper.dev/search',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':query,'gl':'tr','hl':'tr','location':'Turkey','num':10},timeout=20)
    print(f'Serper link çözüm #{_resolve_count} [{query[:65]}] HTTP: {r.status_code}')
    if not r.ok:return []
    return r.json().get('organic') or []

def site_from_item(item):
    link=item.get('link') or '';host=urlparse(link).netloc.lower()
    if host in TARGETS:return TARGETS[host]
    source=(item.get('source') or '').lower()
    if 'amazon' in source:return 'Amazon'
    if 'hepsiburada' in source:return 'Hepsiburada'
    if 'trendyol' in source:return 'Trendyol'
    return None

def resolve_direct_link(item,site):
    raw=item.get('link') or ''
    title=re.sub(r'\s+',' ',item.get('title') or '').strip()
    if valid_product_url(raw,site):
        cache_set(site,title,raw);return raw
    cached,known=cache_get(site,title)
    if known:
        if cached and valid_product_url(cached,site):
            print(f'Link cache kullanıldı: {site} | {cached[:130]}')
            return cached
        print(f'Negatif link cache; tekrar sorgulanmadı: {site} | {title[:75]}')
        return None
    if not title:return None
    if _resolve_count>=MAX_RESOLVE_PER_RUN:
        print(f'Link çözüm limiti doldu; ürün sonraki tura bırakıldı: {site} | {title[:75]}')
        return None
    q=f'site:{SITE_DOMAIN[site]} "{title[:140]}"'
    for result in serper_search(q):
        link=result.get('link') or ''
        if valid_product_url(link,site) and result_matches_title(title,result.get('title') or '',link):
            print(f'Direkt ürün linki doğrulandı: {site} | {link[:150]}')
            cache_set(site,title,link)
            return link
    print(f'Güvenilir direkt ürün linki bulunamadı; 12 saat cachelendi: {site} | {title[:90]}')
    cache_set(site,title,None)
    return None

def product_identity(item,site,direct_link):
    if direct_link:return canonical_for_db(direct_link)
    pid=str(item.get('productId') or '').strip()
    if pid:return f'https://serper.local/{site.lower()}/{pid}'
    title=re.sub(r'\s+',' ',item.get('title') or '').strip().lower()
    return f'https://serper.local/{site.lower()}/{hashlib.sha1(f"{site}|{title}".encode()).hexdigest()}'

def process_item(item):
    site=site_from_item(item)
    if not site:return False
    current=parse_price(item.get('price'))
    if current is None:return False
    title=re.sub(r'\s+',' ',item.get('title') or 'Ürün').strip()[:300]
    direct_link=resolve_direct_link(item,site)
    if not direct_link:return False
    db_url=product_identity(item,site,direct_link)
    image=item.get('imageUrl') or ''
    now=datetime.now(timezone.utc).isoformat()
    try:
        rows=bot.sb('GET','products',params={'select':'*','product_url':f'eq.{db_url}','limit':'1'})
        old=bot.history(db_url)
        hist_ref=max((x for x in old if x>current*1.03),default=None)
        source_ref=item_reference_price(item,current)
        prev=max([x for x in (hist_ref,source_ref) if x],default=None)
        payload={'product_name':title,'current_price':current,'previous_price':prev,'product_url':db_url,'site':site,'updated_at':now}
        if rows:
            row=rows[0];bot.sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
        else:
            row=(bot.sb('POST','products',json=payload) or [payload])[0]
        bot.sb('POST','price_history',json={'price':current,'product_url':db_url,'site':site,'recorded_at':now})
        print(f'Kontrol: {site} | {current:.2f} TL | referans={prev or 0:.2f} | geçmiş={len(old)} | {title[:65]}')
        if prev is None or prev<=current:return False
        disc=(prev-current)/prev*100
        if disc<bot.MIN_DISCOUNT:return False
        last=row.get('last_posted_at') if isinstance(row,dict) else None
        if last:
            try:
                dt=datetime.fromisoformat(last.replace('Z','+00:00'))
                if datetime.now(timezone.utc)-dt < bot.timedelta(hours=bot.COOLDOWN):return False
            except:pass
        link=clean_link(direct_link,site)
        msg=f'🔥 %{disc:.0f} İNDİRİM\n\n{title}\n\n💰 {current:,.2f} TL\n🏷️ Referans: {prev:,.2f} TL\n🛍️ {site}\n🔗 {link}'
        if image:
            rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendPhoto',data={'chat_id':bot.CHANNEL_ID,'photo':image,'caption':msg[:1024]},timeout=12)
            if not rr.ok:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg},timeout=10)
        else:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg},timeout=10)
        print(f'Telegram HTTP: {rr.status_code} | link={link[:120]}')
        if rr.ok and isinstance(row,dict) and row.get('id'):
            bot.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now,'last_posted_price':current})
        return rr.ok
    except Exception as e:
        print(f'İşlem hata: {type(e).__name__}: {e}');return False

def main():
    global _resolve_count
    _resolve_count=0
    print('=== Serper alışveriş botu başladı ===')
    seen=set();matched=0;sent=0
    for q in QUERIES:
        for item in serper_shopping(q):
            site=site_from_item(item)
            pid=str(item.get('productId') or '').strip()
            title=re.sub(r'\s+',' ',item.get('title') or '').strip().lower()
            key=(site,pid or title)
            if not site or key in seen:continue
            seen.add(key);matched+=1
            if process_item(item):sent+=1
    print(f'=== Bitti. Hedef ürün: {matched} | link çözüm sorgusu: {_resolve_count} | Gönderilen: {sent} ===')

if __name__=='__main__':main()
