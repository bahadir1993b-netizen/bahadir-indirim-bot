import os
import re
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, urlunparse, quote, parse_qs
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = "-1004424116637"
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36", "Accept-Language":"tr-TR,tr;q=0.9,en;q=0.8", "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
SEEDS = {"Amazon":"https://www.amazon.com.tr/gp/goldbox", "Hepsiburada":"https://www.hepsiburada.com/ara?q=indirim", "Trendyol":"https://www.trendyol.com/sr?q=indirim", "Pazarama":"https://www.pazarama.com/son-30-gunun-en-dusuk-fiyatli-urunleri-k-VRTCTGRY"}
MIN_DISCOUNT = 10.0
REPOST_COOLDOWN_HOURS = 12
MAX_PRODUCTS_PER_SITE = 12
PRICE_HISTORY_DAYS = 30
MIN_HISTORY_SAMPLES = 3


def sb_headers(prefer=None):
    h={"apikey":SUPABASE_KEY,"Content-Type":"application/json","Accept":"application/json"}
    if SUPABASE_KEY.startswith("eyJ"): h["Authorization"]=f"Bearer {SUPABASE_KEY}"
    if prefer: h["Prefer"]=prefer
    return h


def sb_get(path, params=None):
    r=requests.get(f"{SUPABASE_URL}/rest/v1/{path}",headers=sb_headers(),params=params,timeout=20); r.raise_for_status(); return r.json()


def sb_upsert(p):
    r=requests.post(f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_url",headers=sb_headers("resolution=merge-duplicates,return=representation"),json=p,timeout=20); r.raise_for_status(); d=r.json(); return d[0] if d else p


def record_price(url,site,price,at):
    r=requests.post(f"{SUPABASE_URL}/rest/v1/price_history",headers=sb_headers("return=minimal"),json={"product_url":url,"site":site,"price":price,"recorded_at":at},timeout=20); r.raise_for_status()


def history(url):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=PRICE_HISTORY_DAYS)).isoformat()
    return sb_get("price_history",{"select":"price,recorded_at","product_url":f"eq.{url}","recorded_at":f"gte.{cutoff}","order":"recorded_at.desc"})


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

def prices(text): return [p for m in PRICE_RE.finditer(text or "") if (p:=price(m.group(1))) and p>0]

def labeled(text,labels):
    for label in labels:
        m=re.search(re.escape(label)+r"[^0-9]{0,80}(?:₺\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)",text or "",re.I)
        if m and (p:=price(m.group(1))): return p
    return None


def hb_prices(text):
    cur=labeled(text,["Sepetteki Fiyat","Sepette indirimli fiyat","Sepette","Peşin Fiyat","Güncel Fiyat","Satış Fiyatı","Kampanyalı Fiyat"])
    if cur is None:
        cand=[]; bad=["taksit","puan","bonus","parapuan","kupon kazan","indirim tutarı","kazanç"]
        for m in PRICE_RE.finditer(text or ""):
            p=price(m.group(1)); ctx=text[max(0,m.start()-160):m.end()+100].lower()
            if not p:continue
            score=(15 if any(x in ctx for x in ["fiyat","satış","sepette","hemen al"]) else 0)-(40 if any(x in ctx for x in bad) else 0)-(10 if p<50 else 0)
            cand.append((score,p))
        if cand: cur=max(cand,key=lambda x:(x[0],-x[1]))[1]
    prev=None
    for lab in ["Eski Fiyat","Önceki Fiyat","Liste Fiyatı"]:
        p=labeled(text,[lab])
        if p and cur and p>cur:prev=p;break
    return cur,prev


def canonical(u):
    p=urlparse(u); return urlunparse((p.scheme or "https",p.netloc,p.path.rstrip("/"),"","",""))


def product_url(site,path):
    p=(path or "").lower()
    if site=="Amazon":return "/dp/" in p or "/gp/product/" in p or "/gp/aw/d/" in p
    if site in {"Hepsiburada","Trendyol"}:return "-p-" in p
    if site=="Pazarama":return bool(re.search(r"-p-\d+",p))
    return False


def product(site,name,url,text,cur=None,prev=None):
    url=canonical(url)
    if not product_url(site,urlparse(url).path):return None
    text=text or ""
    if cur is None:
        cur,prev=hb_prices(text) if site=="Hepsiburada" else (labeled(text,["Fırsatın Fiyatı","Teklif Fiyatı","Sepette","İndirimli Fiyat","Güncel Fiyat"]) or (prices(text)[0] if prices(text) else None),prev
    if not cur or cur<=0:return None
    camp=None; code=None
    m=re.search(r"(?:sepette|ödemede|ödeme adımında)\s*(?:₺\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)",text,re.I)
    if m and (p:=price(m.group(1))) and p<cur:camp=p
    m=re.search(r"(?:kod|kupon kodu|kodu)\s*[:：]?\s*([A-Z0-9_-]{4,30})",text,re.I)
    if m:code=m.group(1).upper()
    return {"name":re.sub(r"\s+"," ",name or "Ürün").strip()[:300],"price":cur,"previous_display_price":prev if prev and prev>cur else None,"campaign_price":camp,"coupon_code":code,"campaign_note":None,"url":url,"site":site}


def amazon_card(a,base):
    u=canonical(urljoin(base,a.get("href","")))
    if not product_url("Amazon",urlparse(u).path):return None
    node=a; best=""; name=a.get("title") or a.get_text(" ",strip=True)
    for _ in range(5):
        if not node:break
        t=node.get_text(" ",strip=True)
        if len(t)>len(best) and len(t)<1800:best=t
        for img in node.find_all("img"):
            if img.get("alt") and len(img.get("alt"))>len(name or ""):name=img.get("alt")
        node=node.parent
    ps=prices(best); cur=labeled(best,["Fırsatın Fiyatı","Teklif Fiyatı","Deal Price","İndirimli Fiyat","Şimdi Al"]) or (ps[0] if ps else None)
    prev=max(ps[1:]) if len(ps)>1 else None
    return product("Amazon",name or best[:300],u,best,cur,prev)


def card(site,a,base):
    if site=="Amazon":return amazon_card(a,base)
    u=canonical(urljoin(base,a.get("href","")))
    if not product_url(site,urlparse(u).path):return None
    text=a.get_text(" ",strip=True); node=a; best=text
    for _ in range(3):
        node=node.parent
        if not node:break
        t=node.get_text(" ",strip=True)
        if len(t)>len(best) and len(t)<1200:best=t
    if site=="Pazarama":
        cur=labeled(best,["Sepette","Plus ile"]) or (prices(best)[-1] if prices(best) else None)
    elif site=="Hepsiburada":
        cur,prev=hb_prices(best); return product(site,text or best[:300],u,best,cur,prev)
    else:cur=labeled(best,["Sepette","İndirimli Fiyat"]) or (prices(best)[0] if prices(best) else None)
    return product(site,text or best[:300],u,best,cur)


def google_fallback(site,browser):
    domains={"Amazon":"amazon.com.tr","Hepsiburada":"hepsiburada.com","Trendyol":"trendyol.com"}; dom=domains[site]; out=[];seen=set()
    ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"]); page=ctx.new_page()
    try:
        for q in [f"site:{dom} indirim TL",f"site:{dom} fırsat TL"]:
            try:
                page.goto("https://www.google.com/search?"+quote("q="+q+"&hl=tr&gl=tr&num=20",safe="=&"),wait_until="domcontentloaded",timeout=30000); page.wait_for_timeout(1000)
                soup=BeautifulSoup(page.content(),"html.parser")
                for a in soup.find_all("a",href=True):
                    href=a["href"]
                    if href.startswith("/url?"):
                        qs=parse_qs(urlparse(href).query); href=(qs.get("q") or qs.get("url") or [""])[0]
                    if not href.startswith("http") or dom not in urlparse(href).netloc or not product_url(site,urlparse(href).path) or href in seen:continue
                    node=a; best=a.get_text(" ",strip=True)
                    for _ in range(3):
                        node=node.parent
                        if not node:break
                        t=node.get_text(" ",strip=True)
                        if len(t)>len(best) and len(t)<1500:best=t
                    ps=prices(best)
                    if not ps:continue
                    cur,prev=(hb_prices(best) if site=="Hepsiburada" else (ps[-1],max(ps[:-1]) if len(ps)>1 else None))
                    p=product(site,a.get_text(" ",strip=True) or best[:300],href,best,cur,prev)
                    if p:out.append(p);seen.add(p["url"])
                    if len(out)>=MAX_PRODUCTS_PER_SITE:break
                if len(out)>=MAX_PRODUCTS_PER_SITE:break
            except Exception as e:print(f"{site} Google fallback: {type(e).__name__}: {e}")
    finally:ctx.close()
    print(f"{site} Google fallback: {len(out)} ürün bulundu"); return out


def trendyol_api():
    ep="https://public.trendyol.com/discovery-web-searchgw-service/v2/api/infinite-scroll/sr"; params={"q":"indirim","os":"1","sk":"1","pi":"1","culture":"tr-TR","userGenderId":"1","pId":"0"}
    try:r=session.get(ep,params=params,timeout=25);r.raise_for_status();items=(r.json().get("result") or {}).get("products") or []
    except Exception as e:print(f"Trendyol API: {type(e).__name__}: {e}");return []
    out=[]
    for x in items:
        u=x.get("url") or x.get("productUrl"); cid=x.get("id") or x.get("contentId"); u=u or (f"https://www.trendyol.com/urun-p-{cid}" if cid else None)
        cur=price(x.get("price") or x.get("discountedPrice") or x.get("sellingPrice")); prev=price(x.get("originalPrice") or x.get("listPrice"))
        if u and cur:
            p=product("Trendyol",x.get("name") or x.get("title") or "Trendyol ürünü",u,json.dumps(x,ensure_ascii=False),cur,prev)
            if p:out.append(p)
        if len(out)>=MAX_PRODUCTS_PER_SITE:break
    print(f"Trendyol API: {len(out)} ürün bulundu");return out


def discover(site,seed,browser):
    ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"],viewport={"width":1440,"height":1000},extra_http_headers=HEADERS); page=ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        r=page.goto(seed,wait_until="domcontentloaded",timeout=60000); status=r.status if r else 0
        print(f"{site} web HTTP: {status}")
        if status==200:
            page.wait_for_timeout(2000)
            for _ in range(3):page.mouse.wheel(0,2500);page.wait_for_timeout(500)
            soup=BeautifulSoup(page.content(),"html.parser");out=[];seen=set()
            for a in soup.find_all("a",href=True):
                p=card(site,a,page.url)
                if p and p["url"] not in seen:out.append(p);seen.add(p["url"])
                if len(out)>=MAX_PRODUCTS_PER_SITE:break
            if out:
                print(f"{site} web: {len(out)} ürün bulundu");return out
    except Exception as e:print(f"{site} web: {type(e).__name__}: {e}")
    finally:ctx.close()
    if site=="Trendyol":
        p=trendyol_api()
        return p or google_fallback(site,browser)
    return google_fallback(site,browser) if site in {"Amazon","Hepsiburada"} else []


def telegram(text):
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":CHANNEL_ID,"text":text,"disable_web_page_preview":False},timeout=20)
    print(f"Telegram HTTP: {r.status_code} | {r.text[:300]}");r.raise_for_status()


def median(v):
    v=sorted(v);n=len(v)
    return None if not v else v[n//2] if n%2 else (v[n//2-1]+v[n//2])/2


def process(p):
    now=datetime.now(timezone.utc); u=p["url"]; site=p["site"]; cur=float(p["price"]); eff=float(p.get("campaign_price") or cur)
    if eff>=cur:eff=cur
    old=sb_get("products",{"select":"*","product_url":f"eq.{u}","limit":1}); old=old[0] if old else None
    h=history(u); vals=[float(x["price"]) for x in h]
    if not vals or abs(vals[-1]-cur)>0.001:record_price(u,site,cur,now.isoformat());vals.append(cur)
    ref=median(vals) if len(vals)>=MIN_HISTORY_SAMPLES else None; disc=((ref-eff)/ref*100) if ref else None
    lp=float(old["last_posted_price"]) if old and old.get("last_posted_price") is not None else None
    la=datetime.fromisoformat(old["last_posted_at"].replace("Z","+00:00")) if old and old.get("last_posted_at") else None
    post=bool(ref and disc>=MIN_DISCOUNT and (lp is None or eff<lp) and (la is None or now-la>=timedelta(hours=REPOST_COOLDOWN_HOURS)))
    row={"product_url":u,"product_name":p["name"],"site":site,"current_price":cur,"previous_price":p.get("previous_display_price"),"lowest_price":min(vals) if vals else cur,"coupon_code":p.get("coupon_code"),"coupon_price":p.get("campaign_price"),"last_seen_at":now.isoformat(),"updated_at":now.isoformat()}
    if post:
        msg=f"🔥 YENİ DÜŞÜŞ!\n\n🛒 {p['name']}\n\n💰 Son 30 gün normal fiyatı: {ref:,.2f} TL\n🔥 Yeni fiyat: {eff:,.2f} TL\n📉 Gerçek indirim: %{disc:.1f}\n🏪 {site}\n\n🔗 {u}"
        telegram(msg);row["last_posted_price"]=eff;row["last_posted_at"]=now.isoformat()
    print(f"Kontrol: {site} | anlık {cur:.2f} | 30g örnek={len(vals)} | referans={ref if ref else 'None'} | indirim={disc if disc is not None else 'None'} | paylaş={post}")
    sb_upsert(row)


def main():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        try:
            total=0
            for site,seed in SEEDS.items():
                try:
                    ps=discover(site,seed,browser);total+=len(ps)
                    for p in ps:process(p)
                except Exception as e:print(f"{site} hata: {type(e).__name__}: {e}")
            print(f"Toplam işlenen ürün: {total}")
        finally:browser.close()

if __name__=="__main__":main()
