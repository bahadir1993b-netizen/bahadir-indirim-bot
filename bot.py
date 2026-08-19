import os, re, json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, urlunparse, quote, parse_qs, unquote
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID="-1004424116637"
SUPABASE_URL=os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY=os.environ["SUPABASE_SERVICE_KEY"]
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36","Accept-Language":"tr-TR,tr;q=0.9,en;q=0.8","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
SEEDS={"Amazon":"https://www.amazon.com.tr/gp/goldbox","Hepsiburada":"https://www.hepsiburada.com/ara?q=indirim","Trendyol":"https://www.trendyol.com/sr?q=indirim"}
MIN_DISCOUNT=10.0
REPOST_COOLDOWN_HOURS=12
MAX_PRODUCTS_PER_SITE=12
PRICE_HISTORY_DAYS=30
MIN_HISTORY_SAMPLES=3

def sb_headers(prefer=None):
    h={"apikey":SUPABASE_KEY,"Content-Type":"application/json","Accept":"application/json"}
    if SUPABASE_KEY.startswith("eyJ"): h["Authorization"]=f"Bearer {SUPABASE_KEY}"
    if prefer: h["Prefer"]=prefer
    return h

def sb_get(path,params=None):
    r=requests.get(f"{SUPABASE_URL}/rest/v1/{path}",headers=sb_headers(),params=params,timeout=20)
    if r.status_code>=400: raise requests.HTTPError(f"{r.status_code} {r.text[:500]}",response=r)
    return r.json()

def sb_upsert(p):
    # Bazı eski products tablolarında sonradan eklenen kampanya kolonları olmayabilir.
    # Önce tam kaydı, 400 olursa çekirdek kolonlarla tekrar deniyoruz.
    variants=[p]
    core={k:p[k] for k in ("name","price","previous_display_price","url","site") if k in p}
    if core!=p: variants.append(core)
    slim={k:p[k] for k in ("name","price","url","site") if k in p}
    if slim!=core: variants.append(slim)
    last_error=None
    for i,payload in enumerate(variants,1):
        r=requests.post(f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_url",headers=sb_headers("resolution=merge-duplicates,return=representation"),json=payload,timeout=20)
        if r.ok:
            d=r.json(); return d[0] if d else payload
        last_error=f"HTTP {r.status_code}: {r.text[:500]}"
        print(f"Supabase products deneme {i} başarısız: {last_error}")
    raise requests.HTTPError(last_error or "Supabase products upsert failed")

def record_price(url,site,value,at):
    payload={"product_url":url,"site":site,"price":value}
    # Repo SQL'indeki kolon observed_at; eski tabloda recorded_at kullanılmış olabilir.
    for time_key in ("observed_at","recorded_at"):
        q=dict(payload); q[time_key]=at
        r=requests.post(f"{SUPABASE_URL}/rest/v1/price_history",headers=sb_headers("return=minimal"),json=q,timeout=20)
        if r.ok:return
        print(f"Supabase price_history {time_key}: HTTP {r.status_code} | {r.text[:300]}")
    raise requests.HTTPError("price_history insert failed")

def history(url):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=PRICE_HISTORY_DAYS)).isoformat()
    for time_key in ("observed_at","recorded_at"):
        try:
            return sb_get("price_history",{"select":f"price,{time_key}","product_url":f"eq.{url}",time_key:f"gte.{cutoff}","order":f"{time_key}.desc"})
        except requests.HTTPError as e:
            print(f"Supabase history {time_key} kullanılamadı: {e}")
    return []

def price(v):
    if v is None:return None
    s=str(v).replace("TL","").replace("₺","").replace(" ",""); s=re.sub(r"[^0-9,.]","",s)
    if not s:return None
    if "," in s and "." in s:s=s.replace(".","").replace(",",".") if s.rfind(",")>s.rfind(".") else s.replace(",","")
    elif "," in s:
        a,b=s.rsplit(",",1); s=a.replace(".","")+"."+b if len(b)<=2 else s.replace(",","")
    elif "." in s:
        a,b=s.rsplit(".",1); s=s.replace(".","") if len(b)>2 else s
    try:return float(s)
    except:return None

PRICE_RE=re.compile(r"(?:₺\s*)?(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)",re.I)

def prices(text):
    out=[]
    for m in PRICE_RE.finditer(text or ""):
        # %8 gibi oranları fiyat sanma
        if "%" in (text[max(0,m.start()-3):m.start()]): continue
        p=price(m.group(1))
        if p and p>0:out.append(p)
    return out

def labeled(text,labels):
    for label in labels:
        m=re.search(re.escape(label)+r"[^0-9]{0,100}(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)",text or "",re.I)
        if m:
            p=price(m.group(1))
            if p:return p
    return None

def canonical(url):
    url=unquote(url or "").replace("\\/","/").strip().strip('"\'')
    p=urlparse(url)
    return urlunparse((p.scheme or "https",p.netloc.lower(),p.path.rstrip("/"),"","",""))

def product_url(site,url):
    u=unquote(url or "").replace("\\/","/")
    p=urlparse(u).path.lower() if "://" in u else u.lower()
    if site=="Amazon":return bool(re.search(r"/(?:dp|gp/product|gp/aw/d|product)/[a-z0-9]{8,}(?:$|[/?#])",p,re.I))
    if site=="Hepsiburada":return bool(re.search(r"-p-[a-z0-9]+(?:$|[/?#])",p,re.I))
    if site=="Trendyol":return bool(re.search(r"-p-\d+(?:$|[/?#])",p,re.I))
    return False

def unwrap(url):
    url=unquote(url or "").replace("\\/","/")
    q=parse_qs(urlparse(url).query)
    for k in ("q","url","uddg","u"):
        if q.get(k) and q[k][0].startswith("http"):return unquote(q[k][0])
    return url

def extract_candidate_urls(site,html,base):
    html=html or ""; html2=html.replace("\\/","/").replace("\\u002F","/").replace("\\u003A",":"); soup=BeautifulSoup(html,"html.parser"); out=[]; seen=set()
    def add(raw,title=""):
        raw=unwrap(raw).replace("\\/","/")
        if not raw:return
        u=canonical(urljoin(base,raw))
        if product_url(site,u) and u not in seen:
            seen.add(u); out.append((u,(title or "Ürün").strip()[:300]))
    for a in soup.find_all("a",href=True):
        add(a.get("href"),a.get("title") or a.get("aria-label") or a.get_text(" ",strip=True))
        if len(out)>=MAX_PRODUCTS_PER_SITE:return out
    domain={"Amazon":"amazon.com.tr","Hepsiburada":"hepsiburada.com","Trendyol":"trendyol.com"}[site]
    patterns=[
        rf"https?://(?:www\.)?{re.escape(domain)}[^\"'<>\\s]+",
        rf"(?:https?:)?//(?:www\.)?{re.escape(domain)}[^\"'<>\\s]+",
        rf"/(?:[^\"'<>\\s]+)-p-[A-Za-z0-9]+(?:[/?#][^\"'<>\\s]*)?",
        r"/(?:dp|gp/product|gp/aw/d)/[A-Za-z0-9]{8,}(?:[/?#][^\"'<>\\s]*)?"
    ]
    for pat in patterns:
        for m in re.finditer(pat,html2,re.I):
            add(m.group(0))
            if len(out)>=MAX_PRODUCTS_PER_SITE:return out
    return out

def parse_jsonld(html):
    res={}; soup=BeautifulSoup(html or "","html.parser")
    for s in soup.find_all("script",type="application/ld+json"):
        try:o=json.loads(s.string or s.get_text())
        except:continue
        for x in (o if isinstance(o,list) else [o]):
            if not isinstance(x,dict):continue
            typ=x.get("@type")
            if typ=="Product" or (isinstance(typ,list) and "Product" in typ):
                res["name"]=x.get("name") or res.get("name"); offers=x.get("offers") or {}
                if isinstance(offers,list):offers=offers[0] if offers else {}
                if isinstance(offers,dict):res["price"]=price(offers.get("price")) or res.get("price")
    return res

def make_product(site,name,url,text,current=None,previous=None):
    url=canonical(url)
    if not product_url(site,url):return None
    text=re.sub(r"\s+"," ",text or " ").strip(); ps=prices(text)
    if current is None:current=labeled(text,["Sepetteki Fiyat","Sepette","İndirimli Fiyat","Fırsatın Fiyatı","Teklif Fiyatı","Güncel Fiyat","Satış Fiyatı","Peşin Fiyat"])
    if current is None and ps:current=ps[0]
    if not current or current<=0:return None
    if previous is None:
        for label in ["Önce","Eski Fiyat","Liste Fiyatı","Piyasa Fiyatı"]:
            previous=labeled(text,[label])
            if previous and previous>current:break
    if not previous and ps:
        higher=[x for x in ps if x>current*1.05]
        if higher:previous=min(higher)
    campaign=labeled(text,["Sepetteki Fiyat","Sepette"]); coupon=None
    m=re.search(r"(?:kupon kodu|kupon|kod)\s*[:：]?\s*([A-Z0-9_-]{4,30})",text,re.I)
    if m:coupon=m.group(1).upper()
    return {"name":re.sub(r"\s+"," ",name or "Ürün").strip()[:300],"price":current,"previous_display_price":previous if previous and previous>current else None,"campaign_price":campaign if campaign and campaign<current else None,"coupon_code":coupon,"campaign_note":None,"url":url,"site":site}

def page_product(site,url,title,browser):
    ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"],viewport={"width":1440,"height":1000},extra_http_headers=HEADERS); page=ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"); r=page.goto(url,wait_until="domcontentloaded",timeout=30000)
        if not r or r.status>=400:return None
        page.wait_for_timeout(1800); html=page.content(); text=page.locator("body").inner_text(timeout=10000); jd=parse_jsonld(html)
        current=jd.get("price") or labeled(text,["Sepetteki Fiyat","Sepette","İndirimli Fiyat","Satış Fiyatı","Güncel Fiyat","Fiyat"]); ps=prices(text)
        if current is None and ps:current=ps[0]
        higher=[x for x in ps if current and x>current*1.05]; previous=min(higher) if higher else None
        return make_product(site,jd.get("name") or title,url,text,current,previous) or make_product(site,title,url,text,current,previous)
    except Exception as e:
        print(f"{site} ürün sayfası hata: {type(e).__name__}: {e}"); return None
    finally:ctx.close()

def direct_discover(site,seed,browser):
    ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"],viewport={"width":1440,"height":1000},extra_http_headers=HEADERS); page=ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"); r=page.goto(seed,wait_until="domcontentloaded",timeout=60000); status=r.status if r else 0; print(f"{site} web HTTP: {status}")
        if status!=200:return []
        page.wait_for_timeout(3500)
        for _ in range(6):page.mouse.wheel(0,3500); page.wait_for_timeout(600)
        html=page.content(); c=extract_candidate_urls(site,html,page.url); print(f"{site} web: {len(c)} ürün linki bulundu; HTML={len(html)}")
        return c
    except Exception as e:print(f"{site} web: {type(e).__name__}: {e}"); return []
    finally:ctx.close()

def search_engine_candidates(site):
    dom={"Amazon":"amazon.com.tr","Hepsiburada":"hepsiburada.com","Trendyol":"trendyol.com"}[site]; found=[]; seen=set()
    for q in [f"site:{dom} indirim TL",f"site:{dom} fırsat TL",f"site:{dom} kampanya TL"]:
        eq=quote(q,safe="")
        urls=[f"https://www.bing.com/search?format=rss&q={eq}",f"https://html.duckduckgo.com/html/?q={eq}",f"https://www.google.com/search?q={eq}&hl=tr&num=20"]
        for u in urls:
            try:
                r=requests.get(u,headers=HEADERS,timeout=15); print(f"{site} arama HTTP: {r.status_code}")
                if r.status_code>=400:continue
                for c in extract_candidate_urls(site,r.text,u):
                    if c[0] not in seen:seen.add(c[0]);found.append(c)
                if len(found)>=MAX_PRODUCTS_PER_SITE:return found[:MAX_PRODUCTS_PER_SITE]
            except Exception as e:print(f"{site} arama hata: {type(e).__name__}: {e}")
    return found[:MAX_PRODUCTS_PER_SITE]

def discover(site,seed,browser):
    c=direct_discover(site,seed,browser)
    if not c:
        c=search_engine_candidates(site); print(f"{site} arama fallback: {len(c)} ürün linki bulundu")
    products=[]
    for u,t in c:
        p=page_product(site,u,t,browser)
        if p:products.append(p);print(f"{site} ÜRÜN: {p['price']:.2f} TL | {p['name'][:90]}")
        if len(products)>=MAX_PRODUCTS_PER_SITE:break
    print(f"{site}: {len(products)} fiyatlı ürün bulundu"); return products

def telegram(text):
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":CHANNEL_ID,"text":text,"disable_web_page_preview":False},timeout=20); print(f"Telegram HTTP: {r.status_code} | {r.text[:300]}"); r.raise_for_status()

def median(v):
    v=sorted(v); n=len(v)
    return None if not v else v[n//2] if n%2 else (v[n//2-1]+v[n//2])/2

def process(p):
    now=datetime.now(timezone.utc); url=p["url"]; current=float(p["price"]); hist=history(url); hp=[float(x["price"]) for x in hist if x.get("price") is not None]
    baseline=float(p["previous_display_price"]) if p.get("previous_display_price") and float(p["previous_display_price"])>current else None
    if len(hp)>=MIN_HISTORY_SAMPLES:
        hm=median(hp)
        if hm and hm>current:baseline=max(baseline or 0,hm)
    discount=((baseline-current)/baseline*100) if baseline and baseline>current else 0
    print(f"DEĞERLENDİR: {p['site']} | {current:.2f} TL | baz={baseline} | indirim=%{discount:.1f}")
    row=dict(p); row.update({"price":current,"discount_percent":round(discount,2),"last_seen_at":now.isoformat()})
    try:
        saved=sb_upsert(row)
    except Exception as e:
        # Veritabanı hatası Telegram bildirimini engellemesin; hata logda görünür.
        print(f"Supabase products son hata: {type(e).__name__}: {e}")
        saved=p
    try:record_price(url,p["site"],current,now.isoformat())
    except Exception as e:print(f"Supabase price_history son hata: {type(e).__name__}: {e}")
    if discount<MIN_DISCOUNT:return False
    last=saved.get("last_posted_at")
    if last:
        try:
            if now-datetime.fromisoformat(last.replace("Z","+00:00"))<timedelta(hours=REPOST_COOLDOWN_HOURS):return False
        except:pass
    msg=f"🔥 %{discount:.0f} İNDİRİM\n\n{p['name']}\n\n💰 {current:,.2f} TL\n🏷️ Önce: {baseline:,.2f} TL\n🛍️ {p['site']}\n🔗 {url}"
    if p.get("campaign_price"):msg+=f"\n🛒 Sepette: {p['campaign_price']:,.2f} TL"
    if p.get("coupon_code"):msg+=f"\n🎟️ Kupon: {p['coupon_code']}"
    telegram(msg)
    return True

def telegram_check():
    r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe",timeout=20); print(f"Telegram getMe: {r.status_code} | {r.text[:250]}"); r.raise_for_status()

def main():
    print("=== İndirim botu başladı ===")
    telegram_check()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        total=0
        for site,seed in SEEDS.items():
            try:
                products=discover(site,seed,browser)
                for p in products:
                    try:
                        if process(p):total+=1
                    except Exception as e:print(f"{site} işleme hata: {type(e).__name__}: {e}")
            except Exception as e:print(f"{site} genel hata: {type(e).__name__}: {e}")
        browser.close()
    print(f"=== Bitti. Gönderilen: {total} ===")

if __name__=="__main__":main()
