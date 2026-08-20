import os,re,time,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,parse_qs,unquote
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']
SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_KEY=os.environ['SUPABASE_SERVICE_KEY']
CHANNEL_ID='-1004424116637'
MIN_DISCOUNT=10.0
COOLDOWN=12
HISTORY_DAYS=90
MAX_PRODUCTS=6

HEADERS={'Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SITES={
 'Hepsiburada':'hepsiburada.com',
 'Trendyol':'trendyol.com'
}
TERMS=['indirim','kampanya','fırsat','elektronik','telefon','laptop','kulaklık','televizyon','oyuncu']

def sb(method,path,**kwargs):
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method.upper()=='POST': h['Prefer']='return=representation'
    r=requests.request(method,f'{SUPABASE_URL}/rest/v1/{path}',headers=h,timeout=12,**kwargs)
    r.raise_for_status()
    return r.json() if r.text else []

def price(s):
    if not s:return None
    s=str(s).replace('TL','').replace('₺','').replace(' ','')
    m=re.search(r'\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?',s)
    if not m:return None
    x=m.group(0).replace(' ','')
    if ',' in x:
        a,b=x.rsplit(',',1); x=a.replace('.','')+'.'+b
    elif x.count('.')>1 or ('.' in x and len(x.rsplit('.',1)[1])==3): x=x.replace('.','')
    try:
        v=float(x); return v if 1<v<10000000 else None
    except:return None

def prices(text):
    vals=[]
    for m in re.finditer(r'(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)',text or '',re.I):
        v=price(m.group(1))
        if v: vals.append(v)
    return sorted(set(vals))

def unwrap(href):
    href=unquote(href or '')
    for _ in range(3):
        q=parse_qs(urlparse(href).query)
        nxt=None
        for k in ('q','url','u','uddg','target'):
            if q.get(k) and q[k][0].startswith('http'): nxt=q[k][0];break
        if not nxt:break
        href=unquote(nxt)
    return href

def product_url(site,href):
    u=unwrap(href)
    if not u.startswith('http'):return None
    p=urlparse(u)
    if SITES[site] not in p.netloc.lower():return None
    if not re.search(r'-p-[a-z0-9]+(?:/|$)',p.path,re.I):return None
    return f'https://{p.netloc.lower()}{p.path.rstrip("/")}'

def link_context(link):
    # Google changes result DOM structure frequently. Walk up several ancestors
    # instead of relying on the old div.MjjYud selector.
    try:
        node=link
        for _ in range(7):
            text=re.sub(r'\s+',' ',node.inner_text(timeout=700)).strip()
            if text and re.search(r'(?:TL|₺)',text,re.I):
                ps=prices(text)
                if ps:return text,ps
            node=node.locator('xpath=..')
        return '',[]
    except Exception:
        return '',[]

def discover(site,page):
    domain=SITES[site]; found={}
    for term in TERMS:
        q=f'site:{domain} "TL" {term}'
        url='https://www.google.com/search?q='+requests.utils.quote(q)+'&num=20'
        try:
            print(f'{site} Google: {term}')
            page.goto(url,wait_until='domcontentloaded',timeout=20000)
            page.wait_for_timeout(1000)

            # Do not depend on Google's MjjYud class. Collect real product links
            # and derive the price from their nearest result container.
            links=page.locator('a[href]')
            n=min(links.count(),200)
            for i in range(n):
                link=links.nth(i)
                href=link.get_attribute('href')
                u=product_url(site,href)
                if not u or u in found:continue
                text,ps=link_context(link)
                if not ps:continue
                title=link.inner_text(timeout=500).strip() or text[:180]
                found[u]=(title[:250],ps)
                print(f'{site} aday: {title[:80]} | {ps[:5]} | {u}')
                if len(found)>=MAX_PRODUCTS:break
            if len(found)>=MAX_PRODUCTS:break
        except Exception as e: print(f'{site} arama hata: {type(e).__name__}: {e}')
    return list((u,t,p) for u,(t,p) in found.items())

def history(url):
    since=(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat()
    rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'100'})
    return [float(x['price']) for x in rows if x.get('price') is not None]

def send(site,url,title,current,base,row):
    disc=(base-current)/base*100
    if disc<MIN_DISCOUNT:return False
    last=row.get('last_posted_at') if row else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
        except:pass
    msg=f'🔥 %{disc:.0f} İNDİRİM\n\n{title}\n\n💰 {current:,.2f} TL\n🏷️ Önce: {base:,.2f} TL\n🛍️ {site}\n🔗 {url}'
    r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':msg},timeout=10)
    print(f'Telegram {site}: {r.status_code}')
    if not r.ok:return False
    if row and row.get('id'):
        sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
    return True

def process(site,url,title,search_prices):
    # Search snippets are discovery only. We use them to seed history; future runs compare against history.
    ps=sorted(set(search_prices or []))
    if not ps:return False
    current=ps[0]
    rows=sb('GET','products',params={'select':'*','product_url':f'eq.{url}','limit':'1'})
    row=rows[0] if rows else None
    old=history(url)
    # If snippet contains a second materially larger price, it is a strong explicit old-price signal.
    explicit=next((x for x in ps if x>current*1.10),None)
    base=explicit or (max(old) if old else None)
    now=datetime.now(timezone.utc).isoformat()
    payload={'product_name':title,'current_price':current,'previous_price':explicit,'product_url':url,'site':site,'updated_at':now}
    if row: sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
    else: row=(sb('POST','products',json=payload) or [payload])[0]
    sb('POST','price_history',json={'price':current,'product_url':url,'site':site,'recorded_at':now})
    print(f'Kontrol: {site} | {current:.2f} | baz={base or 0:.2f} | geçmiş={len(old)}')
    return send(site,url,title,current,base,row) if base and base>current else False

def main():
    print('=== HB/Trendyol arama motoru keşfi ==='); sent=0
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-blink-features=AutomationControlled'])
        page=browser.new_page(extra_http_headers=HEADERS,viewport={'width':1440,'height':1000})
        for site in SITES:
            items=discover(site,page); print(f'{site}: {len(items)} aday')
            for url,title,ps in items:
                try:
                    if process(site,url,title,ps):sent+=1
                except Exception as e: print(f'{site} işlem hata: {type(e).__name__}: {e}')
        browser.close()
    print(f'=== HB/Trendyol bitti. Gönderilen: {sent} ===')

if __name__=='__main__': main()
