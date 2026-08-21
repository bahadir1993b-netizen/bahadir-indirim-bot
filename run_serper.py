import os,re,requests,hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, urlencode, quote
import bot

if bot.SUPABASE_URL.endswith('/rest/v1'):
    bot.SUPABASE_URL = bot.SUPABASE_URL[:-8].rstrip('/')

SERPER_API_KEY=os.environ['SERPER_API_KEY']
AMAZON_TAG=os.environ.get('AMAZON_TAG','').strip()
QUERIES=['elektronik indirim','ev yaşam indirim','telefon laptop kulaklık indirim','oyuncu televizyon küçük ev aletleri indirim']
TARGETS={
    'amazon.com.tr':'Amazon','www.amazon.com.tr':'Amazon',
    'hepsiburada.com':'Hepsiburada','www.hepsiburada.com':'Hepsiburada',
    'trendyol.com':'Trendyol','www.trendyol.com':'Trendyol',
}
SITE_DOMAIN={'Amazon':'amazon.com.tr','Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}

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

def canonical_for_db(link):
    p=urlparse(link)
    return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))

def clean_link(link,site):
    if not link:return ''
    p=urlparse(link);host=p.netloc.lower();query=[]
    if site=='Amazon' and AMAZON_TAG:query=[('tag',AMAZON_TAG)]
    return urlunparse(('https',host,p.path.rstrip('/'),'','','')) + (('?'+urlencode(query)) if query else '')

def is_direct_target(link,site):
    try:
        host=urlparse(link).netloc.lower()
        return TARGETS.get(host)==site
    except:return False

def serper_shopping(query):
    r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':query,'gl':'tr','hl':'tr','location':'Turkey','num':100},timeout=20)
    print(f'Serper [{query}] HTTP: {r.status_code}')
    if not r.ok:
        print(r.text[:300]);return []
    return r.json().get('shopping') or []

def serper_search(query):
    r=requests.post('https://google.serper.dev/search',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':query,'gl':'tr','hl':'tr','location':'Turkey','num':10},timeout=20)
    print(f'Serper link çözüm [{query[:65]}] HTTP: {r.status_code}')
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
    if is_direct_target(raw,site):return raw
    title=re.sub(r'\s+',' ',item.get('title') or '').strip()
    domain=SITE_DOMAIN[site]
    if not title:return None
    # Google Shopping bazen https://www.google.com/search döndürüyor. Exact ürün başlığıyla mağaza domaininde organik sonuç ara.
    queries=[f'site:{domain} "{title[:140]}"', f'site:{domain} {title[:100]}']
    for q in queries:
        for result in serper_search(q):
            link=result.get('link') or ''
            if is_direct_target(link,site):
                print(f'Direkt link çözüldü: {site} | {link[:150]}')
                return link
    print(f'Direkt ürün linki bulunamadı; ürün atlandı: {site} | {title[:90]}')
    return None

def product_identity(item,site,direct_link):
    # Gerçek mağaza URL'si varsa kimlik odur. Böylece farklı ürünler google.com/search altında birleşmez.
    if direct_link:return canonical_for_db(direct_link)
    pid=str(item.get('productId') or '').strip()
    if pid:return f'https://serper.local/{site.lower()}/{pid}'
    title=re.sub(r'\s+',' ',item.get('title') or '').strip().lower()
    h=hashlib.sha1(f'{site}|{title}'.encode('utf-8')).hexdigest()
    return f'https://serper.local/{site.lower()}/{h}'

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
        old=bot.history(db_url);prev=old[0] if old else None
        payload={'product_name':title,'current_price':current,'previous_price':prev,'product_url':db_url,'site':site,'updated_at':now}
        if rows:
            row=rows[0];bot.sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
        else:
            row=(bot.sb('POST','products',json=payload) or [payload])[0]
        bot.sb('POST','price_history',json={'price':current,'product_url':db_url,'site':site,'recorded_at':now})
        print(f'Kontrol: {site} | {current:.2f} TL | önceki={prev or 0:.2f} | {title[:70]}')
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
        msg=(f'🔥 %{disc:.0f} İNDİRİM\n\n{title}\n\n💰 {current:,.2f} TL\n🏷️ Önce: {prev:,.2f} TL\n🛍️ {site}\n🔗 {link}')
        if image:
            rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendPhoto',data={'chat_id':bot.CHANNEL_ID,'photo':image,'caption':msg[:1024]},timeout=12)
            if not rr.ok:rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg},timeout=10)
        else:
            rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':bot.CHANNEL_ID,'text':msg},timeout=10)
        print(f'Telegram HTTP: {rr.status_code} | link={link[:120]}')
        if rr.ok and isinstance(row,dict) and row.get('id'):
            bot.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now,'last_posted_price':current})
        return rr.ok
    except Exception as e:
        print(f'İşlem hata: {type(e).__name__}: {e}');return False

def main():
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
    print(f'=== Bitti. Hedef ürün: {matched} | Gönderilen: {sent} ===')

if __name__=='__main__':main()
