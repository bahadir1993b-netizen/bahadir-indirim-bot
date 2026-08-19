import os, re, json, warnings
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, urlunparse, quote, parse_qs, unquote
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from playwright.sync_api import sync_playwright

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
TOKEN=os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID="-1004424116637"
SUPABASE_URL=os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY=os.environ["SUPABASE_SERVICE_KEY"]
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36","Accept-Language":"tr-TR,tr;q=0.9,en;q=0.8","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
SEEDS={"Amazon":"https://www.amazon.com.tr/gp/goldbox","Hepsiburada":"https://www.hepsiburada.com/ara?q=indirim","Trendyol":"https://www.trendyol.com/sr?q=indirim"}
MIN_DISCOUNT=10.0
REPOST_COOLDOWN_HOURS=12
MAX_PRODUCTS_PER_SITE=6
PRICE_HISTORY_DAYS=90
MIN_HISTORY_SAMPLES=3
_products_columns=None
_history_columns=None


def sb_headers(prefer=None):
    h={"apikey":SUPABASE_KEY,"Content-Type":"application/json","Accept":"application/json"}
    if SUPABASE_KEY.startswith("eyJ"): h["Authorization"]=f"Bearer {SUPABASE_KEY}"
    if prefer:h["Prefer"]=prefer
    return h


def sb_get(path,params=None):
    r=requests.get(f"{SUPABASE_URL}/rest/v1/{path}",headers=sb_headers(),params=params,timeout=15)
    if r.status_code>=400: raise requests.HTTPError(f"{r.status_code} {r.text[:500]}",response=r)
    return r.json()


def schema_columns(table):
    try:
        rows=sb_get(table,{"select":"*","limit":"1"})
        return set(rows[0].keys()) if rows else set()
    except Exception as e:
        print(f"Supabase {table} kolon kesfi hatasi: {e}"); return set()


def get_product_columns():
    global _products_columns
    if _products_columns is None:
        _products_columns=schema_columns("products")
        print(f"Supabase products kolonlari: {sorted(_products_columns)}")
    return _products_columns


def get_history_columns():
    global _history_columns
    if _history_columns is None:
        _history_columns=schema_columns("price_history")
        print(f"Supabase price_history kolonlari: {sorted(_history_columns)}")
    return _history_columns


def price(v):
    if v is None:return None
    s=str(v).replace("TL","").replace("₺","").replace(" ","")
    s=re.sub(r"[^0-9,.]","",s)
    if not s:return None
    if "," in s and "." in s:
        s=s.replace(".","").replace(",",".") if s.rfind(",")>s.rfind(".") else s.replace(",","")
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
        if "%" in text[max(0,m.start()-3):m.start()]:continue
        p=price(m.group(1))
        if p and p>0:out.append(p)
    return out


def labeled(text,labels):
    for label in labels:
        m=re.search(re.escape(label)+r"[^0-9]{0,120}(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)",text or "",re.I)
        if m:
            p=price(m.group(1))
            if p:return p
    return None


def canonical(url):
    url=unquote(url or "").replace("\\/","/").strip().strip("\"'")
    p=urlparse(url)
    return urlunparse((p.scheme or "https",p.netloc.lower(),p.path.rstrip("/"),"","",""))


def product_url(site,url):
    p=urlparse(unquote(url or "").replace("\\/","/")).path.lower()
    if site=="Amazon":return bool(re.search(r"/(?:dp|gp/product|gp/aw/d|product)/[a-z0-9]{8,}(?:$|/)",p,re.I))
    if site=="Hepsiburada":return bool(re.search(r"(?:-p-[a-z0-9]+)(?:$|/|\?)",p,re.I))
    if site=="Trendyol":return bool(re.search(r"(?:-p-\d+)(?:$|/|\?)",p,re.I))
    return False


def unwrap(url):
    url=unquote(url or "").replace("\\/","/")
    q=parse_qs(urlparse(url).query)
    for k in ("q","url","uddg","u"):
        if q.get(k) and q[k][0].startswith("http"):return unquote(q[k][0])
    return url


def extract_candidate_urls(site,html,base):
    html2=(html or "").replace("\\/","/").replace("\\u002F","/").replace("\\u003A",":")
    soup=BeautifulSoup(html2,"html.parser")
    out=[];seen=set()
    def add(raw,title="Ürün"):
        raw=unwrap(raw).strip('"\'')
        if not raw:return
        u=canonical(urljoin(base,raw))
        if product_url(site,u) and u not in seen:
            seen.add(u);out.append((u,re.sub(r"\s+"," ",title or "Ürün").strip()[:300]))
    for a in soup.find_all("a",href=True):
        add(a.get("href"),a.get("title") or a.get("aria-label") or a.get_text(" ",strip=True))
        if len(out)>=MAX_PRODUCTS_PER_SITE:return out
    if site=="Amazon":
        pats=[r"(?:https?:)?//(?:www\.)?amazon\.com\.tr/[^\"'<>\s]+(?:dp|gp/product|gp/aw/d)[^\"'<>\s]*",r"/(?:dp|gp/product|gp/aw/d)/[A-Za-z0-9]{8,}(?:[^\"'<>\s]*)"]
    elif site=="Trendyol":
        pats=[r"(?:https?:)?//(?:www\.)?trendyol\.com/[^\"'<>\s]*?-p-\d+(?:[^\"'<>\s]*)",r"/[^\"'<>\s]+?-p-\d+(?:[^\"'<>\s]*)"]
    else:
        pats=[r"(?:https?:)?//(?:www\.)?hepsiburada\.com/[^\"'<>\s]*?-p-[A-Za-z0-9]+(?:[^\"'<>\s]*)",r"/[^\"'<>\s]+?-p-[A-Za-z0-9]+(?:[^\"'<>\s]*)"]
    before=len(out)
    for pat in pats:
        for m in re.finditer(pat,html2,re.I):
            add(m.group(0))
            if len(out)>=MAX_PRODUCTS_PER_SITE:return out
    print(f"{site} aday regex eslesmesi: {len(out)-before}")
    return out


def search_fallback(site):
    domain="hepsiburada.com" if site=="Hepsiburada" else "trendyol.com"
    q=quote(f"site:{domain} ürün")
    urls=[f"https://www.bing.com/search?q={q}",f"https://www.google.com/search?q={q}"]
    for u in urls:
        try:
            r=requests.get(u,headers=HEADERS,timeout=8)
            print(f"{site} arama {urlparse(u).netloc} HTTP: {r.status_code}")
            if r.status_code==200:
                got=extract_candidate_urls(site,r.text,u)
                if got:return got
        except Exception as e:print(f"{site} arama hata: {type(e).__name__}: {e}")
    return []


def parse_jsonld(html):
    res={};soup=BeautifulSoup(html or "","html.parser")
    for s in soup.find_all("script",type="application/ld+json"):
        try:o=json.loads(s.string or s.get_text())
        except:continue
        stack=o if isinstance(o,list) else [o]
        for x in stack:
            if not isinstance(x,dict):continue
            typ=x.get("@type")
            if typ=="Product" or (isinstance(typ,list) and "Product" in typ):
                res["name"]=x.get("name") or res.get("name")
                offers=x.get("offers") or {}
                if isinstance(offers,list):offers=offers[0] if offers else {}
                if isinstance(offers,dict):res["price"]=price(offers.get("price")) or res.get("price")
    return res


def make_product(site,name,url,text,current=None,previous=None):
    url=canonical(url)
    if not product_url(site,url):return None
    text=re.sub(r"\s+"," ",text or "").strip()
    if current is None:current=labeled(text,["Sepetteki Fiyat","Sepette","İndirimli Fiyat","Fırsatın Fiyatı","Teklif Fiyatı","Güncel Fiyat","Satış Fiyatı","Peşin Fiyat"])
    if current is None:
        ps=prices(text); current=min(ps) if ps else None
    if not current or current<=0:return None
    if previous is None:
        for label in ["Önce","Eski Fiyat","Liste Fiyatı","Piyasa Fiyatı","Eski fiyat"]:
            candidate=labeled(text,[label])
            if candidate and candidate>current:
                previous=candidate;break
    campaign=labeled(text,["Sepetteki Fiyat","Sepette"])
    coupon=None
    m=re.search(r"(?:kupon kodu|kupon|kod)\s*[:：]?\s*([A-Z0-9_-]{4,30})",text,re.I)
    if m:coupon=m.group(1).upper()
    return {"name":re.sub(r"\s+"," ",name or "Ürün").strip()[:300],"price":current,"previous_display_price":previous if previous and previous>current else None,"campaign_price":campaign if campaign and campaign<current else None,"coupon_code":coupon,"url":url,"site":site}


def page_product(site,url,title,browser):
    ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"],viewport={"width":1440,"height":1000},extra_http_headers=HEADERS)
    page=ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        r=page.goto(url,wait_until="domcontentloaded",timeout=15000)
        if not r or r.status>=400:return None
        page.wait_for_timeout(700)
        html=page.content();text=page.locator("body").inner_text(timeout=5000);jd=parse_jsonld(html)
        current=jd.get("price") or labeled(text,["Sepetteki Fiyat","Sepette","İndirimli Fiyat","Satış Fiyatı","Güncel Fiyat","Fiyat"])
        previous=None
        if site=="Amazon":
            selectors=[".a-price .a-offscreen","#corePrice_feature_div .a-offscreen","#priceblock_ourprice","#priceblock_dealprice",".apexPriceToPay .a-offscreen","[data-a-color='price'] .a-offscreen"]
            vals=[]
            for sel in selectors:
                try:
                    for i in range(min(page.locator(sel).count(),3)):
                        v=price(page.locator(sel).nth(i).inner_text(timeout=700))
                        if v:vals.append(v)
                except:pass
            if vals:
                current=current or min(vals)
                bigger=[v for v in vals if v>current*1.03]
                if bigger:previous=min(bigger)
            if previous is None:
                for sel in [".a-text-price .a-offscreen",".basisPrice .a-offscreen",".priceBlockStrikePriceString"]:
                    try:
                        v=price(page.locator(sel).first.inner_text(timeout=700))
                        if v and v>current:previous=v;break
                    except:pass
        if current is None:
            ps=prices(text);current=min(ps) if ps else None
        title2=jd.get("name")
        if not title2:
            try:title2=page.locator("meta[property='og:title']").get_attribute("content")
            except:pass
        if not title2:
            try:title2=page.title()
            except:pass
        p=make_product(site,title2 or title,url,text,current,previous)
        if p:print(f"{site} ÜRÜN: {p['price']:.2f} TL | {p['name'][:90]} | eski={p.get('previous_display_price')}")
        return p
    except Exception as e:
        print(f"{site} ürün sayfası hata: {type(e).__name__}: {e}");return None
    finally:ctx.close()


def direct_discover(site,seed,browser):
    ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"],viewport={"width":1440,"height":1000},extra_http_headers=HEADERS)
    page=ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        r=page.goto(seed,wait_until="domcontentloaded",timeout=25000)
        status=r.status if r else 0;print(f"{site} web HTTP: {status}")
        if status!=200:return []
        page.wait_for_timeout(1200)
        for _ in range(2):page.mouse.wheel(0,3500);page.wait_for_timeout(400)
        html=page.content();print(f"{site} HTML={len(html)}")
        if site=="Trendyol":print(f"Trendyol teşhis: -p- sayısı={html.lower().count('-p-')}, productId sayısı={html.count('productId')}")
        return extract_candidate_urls(site,html,seed)
    except Exception as e:print(f"{site} discover hata: {type(e).__name__}: {e}");return []
    finally:ctx.close()


def db_payload(p,cols):
    mapping={
        "product_name":p["name"],"current_price":p["price"],"previous_price":p.get("previous_display_price"),
        "coupon_code":p.get("coupon_code"),"product_url":p["url"],"site":p["site"],
        "last_seen_at":datetime.now(timezone.utc).isoformat(),"updated_at":datetime.now(timezone.utc).isoformat()
    }
    return {k:v for k,v in mapping.items() if k in cols}


def sb_upsert(p):
    url=p["url"];existing=sb_get("products",{"select":"*","product_url":f"eq.{url}","limit":"1"});cols=get_product_columns();payload=db_payload(p,cols)
    if existing:
        row=existing[0];rid=row.get("id")
        if rid is not None and payload:
            r=requests.patch(f"{SUPABASE_URL}/rest/v1/products?id=eq.{rid}",headers=sb_headers("return=representation"),json=payload,timeout=15)
            if r.ok:
                d=r.json();return d[0] if d else row
        return row
    r=requests.post(f"{SUPABASE_URL}/rest/v1/products",headers=sb_headers("return=representation"),json=payload,timeout=15)
    if not r.ok:raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:500]}",response=r)
    d=r.json();return d[0] if d else payload


def record_price(url,site,value,at):
    cols=get_history_columns();payload={k:v for k,v in {"product_url":url,"site":site,"price":value,"recorded_at":at}.items() if k in cols}
    if not payload:return
    r=requests.post(f"{SUPABASE_URL}/rest/v1/price_history",headers=sb_headers("return=minimal"),json=payload,timeout=15)
    if not r.ok:print(f"Supabase price_history yazilamadi: {r.status_code} {r.text[:300]}")


def history(url):
    cols=get_history_columns();cutoff=(datetime.now(timezone.utc)-timedelta(days=PRICE_HISTORY_DAYS)).isoformat();tk="recorded_at" if "recorded_at" in cols else ("created_at" if "created_at" in cols else None)
    try:
        p={"select":"price","product_url":f"eq.{url}"}
        if tk:p[tk]=f"gte.{cutoff}"
        return sb_get("price_history",p)
    except Exception as e:print(f"Supabase history okunamadi: {e}");return []


def telegram(text):
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":CHANNEL_ID,"text":text,"disable_web_page_preview":False},timeout=15)
    print(f"Telegram send: {r.status_code} | {r.text[:250]}");return r.ok


def format_price(x):return f"{x:,.2f} TL".replace(",","X").replace(".",",").replace("X",".")


def process(site,p):
    try:
        row=sb_upsert(p);record_price(p["url"],site,p["price"],datetime.now(timezone.utc).isoformat())
        hs=history(p["url"]);vals=[price(x.get("price")) for x in hs];vals=[x for x in vals if x]
        baseline=p.get("previous_display_price")
        if len(vals)>=MIN_HISTORY_SAMPLES:
            hist=min(vals);baseline=hist if baseline is None else min(baseline,hist)
        if not baseline or baseline<=p["price"]:return False
        discount=(baseline-p["price"])/baseline*100
        if discount<MIN_DISCOUNT:return False
        msg=f"🔥 %{discount:.0f} İNDİRİM\n\n{p['name']}\n\n💰 {format_price(p['price'])}\n🏷️ Önce: {format_price(baseline)}\n🛍️ {site} 🔗 {p['url']}"
        if telegram(msg):
            cols=get_product_columns();rid=row.get("id") if isinstance(row,dict) else None
            if rid is not None and "last_posted_at" in cols:
                try:requests.patch(f"{SUPABASE_URL}/rest/v1/products?id=eq.{rid}",headers=sb_headers(),json={"last_posted_at":datetime.now(timezone.utc).isoformat()},timeout=10)
                except:pass
            return True
    except Exception as e:print(f"{site} ürün işleme hata: {type(e).__name__}: {e}")
    return False


def main():
    print("=== İndirim botu başladı ===")
    try:
        r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe",timeout=15);print(f"Telegram getMe: {r.status_code} | {r.text[:300]}")
    except Exception as e:print(f"Telegram getMe hata: {e}")
    sent=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        for site,seed in SEEDS.items():
            print(f"--- {site} keşif başladı ---")
            links=direct_discover(site,seed,browser)
            if not links and site in ("Hepsiburada","Trendyol"):links=search_fallback(site)
            print(f"{site}: {len(links)} ürün linki bulundu")
            found=0
            for url,title in links[:MAX_PRODUCTS_PER_SITE]:
                p=page_product(site,url,title,browser)
                if p:
                    found+=1
                    if process(site,p):sent+=1
            print(f"{site}: {found} fiyatlı ürün bulundu")
        browser.close()
    print(f"=== Bitti. Gönderilen: {sent} ===")

if __name__=="__main__":main()
