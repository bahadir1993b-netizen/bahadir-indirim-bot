import os
import re
import html as htmlmod
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ['SUPABASE_SERVICE_KEY']
CHAT = '-1004424116637'
MAX_AGE = 90
MIN_DISCOUNT = 10.0
COOLDOWN = 12
AMAZON_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', '').strip()
HEAD = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36', 'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8'}
SOURCES = {'OnuAl':'onual_firsat','EnesOzen':'enesozen','OzelFirsatlar':'ozelfirsat','AmazonOzel':'amazonozel','FirsatZ':'firsatz','FirsatMerkezi':'firsatmerkez','IndirimDeal':'indirimdeal'}
MARKET = {'amazon.com.tr':'Amazon','hepsiburada.com':'Hepsiburada','trendyol.com':'Trendyol'}
SHORT = {'app.hb.biz':'Hepsiburada','hps.im':'Hepsiburada','ty.gl':'Trendyol','tyml.gl':'Trendyol','amzn.to':'Amazon','amzn.eu':'Amazon','link.amazon':'Amazon','onu.al':None}
TRACKING = {'utm_source','utm_medium','utm_campaign','utm_content','utm_term','fbclid','gclid','ref','ref_','tag','ascsubtag','linkcode','creative','creativeasin','camp','adid','dib','dib_tag','pd_rd_i','pd_rd_r','pd_rd_w','pd_rd_wg','pf_rd_i','pf_rd_m','pf_rd_p','pf_rd_r','pf_rd_s','pf_rd_t','_encoding','aff_fcid','aff_fsk','aff_platform','aff_trace_key','spm','partner_id'}
DEAL_WORDS = re.compile(r'kupon|kod(?:u)?|sepette|kampanya|indirim|promosyon|aktif|geçerli|gecerli|2 al 1|3 al 2|4 al 3', re.I)
MONEY_RE = re.compile(r'(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])', re.I)

def sb(method, path, **kw):
    h={'apikey':KEY,'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method=='POST': h['Prefer']='return=representation'
    r=requests.request(method,f'{SB}/rest/v1/{path}',headers=h,timeout=15,**kw); r.raise_for_status(); return r.json() if r.text else []

def clean(u): return htmlmod.unescape(u or '').replace('\\/','/').split('#',1)[0].rstrip('/')

def money(s):
    s=re.sub(r'[^0-9,.]','',str(s).replace('TL','').replace('₺','').replace(' ',''))
    if not s:return None
    if ',' in s and '.' in s: s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1); s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1); s=s.replace('.','') if len(b)>2 else s
    try:
        x=float(s); return x if 0<x<10000000 else None
    except: return None

def prices(t): return [money(m.group()) for m in MONEY_RE.finditer(t or '') if money(m.group()) is not None]

def source_pair(t):
    m=re.search(r'(\d[\d.,]*)\s*(?:TL|₺)\s+yerine\s+(\d[\d.,]*)\s*(?:TL|₺)',t,re.I)
    if m:return money(m.group(2)),money(m.group(1))
    m=re.search(r'(?:yeni|önceki|onceki)\s*[:=]?\s*(\d[\d.,]*)\s*(?:TL|₺).*?(?:şuan|şu an|simdi|şimdi)\s*[:=]?\s*(\d[\d.,]*)\s*(?:TL|₺)',t,re.I|re.S)
    if m:return money(m.group(2)),money(m.group(1))
    p=prices(t); return (p[0],None) if p else (None,None)

def site(u):
    h=urlparse(u).netloc.lower().replace('www.','')
    if h in MARKET:return MARKET[h]
    for k,v in SHORT.items():
        if h==k or h.endswith('.'+k):return v
    return None

def valid(s,u):
    p=urlparse(u); h=p.netloc.lower().replace('www.',''); path=p.path
    if s=='Amazon':return h.endswith('amazon.com.tr') and bool(re.search(r'/(?:dp|gp/product)/[A-Z0-9]{8,}',path,re.I))
    if s=='Hepsiburada':return h.endswith('hepsiburada.com') and bool(re.search(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)',path,re.I))
    if s=='Trendyol':return h.endswith('trendyol.com') and bool(re.search(r'-p-\d+(?:[/?#&]|$)',path,re.I))
    return False

def normalize(s,u):
    if not u or s not in MARKET.values():return None
    p=urlparse(clean(u)); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRACKING]
    if s=='Amazon' and AMAZON_TAG:q.append(('tag',AMAZON_TAG))
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))

def tokens(text):
    stop={'ürün','ürünü','hızlı','fırsat','indirim','adet','parça','set','marka','model','yeni','şimdi','tl'}
    return {x for x in re.findall(r'[a-zçğıöşü0-9]{3,}',(text or '').lower()) if x not in stop}

def title_score(title,candidate_text):
    a,b=tokens(title),tokens(candidate_text)
    return len(a&b)/max(1,len(a)) if a and b else 0

def direct_from_page(page,u,s):
    try:
        page.goto(u,wait_until='domcontentloaded',timeout=12000); page.wait_for_timeout(900)
        f=clean(page.url); ss=site(f)
        if ss and valid(ss,f):return normalize(ss,f)
        for a in page.locator('a[href]').all():
            h=clean(a.get_attribute('href') or ''); ss=site(h)
            if ss and valid(ss,h):return normalize(ss,h)
    except: pass
    return None

def search_marketplace(page,s,title):
    base={'Amazon':'https://www.amazon.com.tr/s?k=','Hepsiburada':'https://www.hepsiburada.com/ara?q=','Trendyol':'https://www.trendyol.com/sr?q='}.get(s)
    if not base or not title or len(title)<6:return None
    try:
        page.goto(base+requests.utils.quote(' '.join(title.split())[:160]),wait_until='domcontentloaded',timeout=15000); page.wait_for_timeout(900)
        candidates=[]
        for a in page.locator('a[href]').all():
            u=clean(a.get_attribute('href') or '')
            if not valid(s,u):continue
            candidates.append((title_score(title,(a.inner_text() or '')[:500]+' '+u),u))
        if candidates:
            candidates.sort(reverse=True,key=lambda x:x[0]); score,u=candidates[0]
            if score>=0.18:return normalize(s,u)
    except: pass
    return None

def resolve(page,u,s,title):
    try:
        r=requests.get(clean(u),headers=HEAD,timeout=8,allow_redirects=True); f=clean(r.url); ss=site(f)
        if ss and valid(ss,f):return normalize(ss,f)
    except: pass
    return direct_from_page(page,u,s) or search_marketplace(page,s,title)

def marketplace_price_check(page,s,u,expected):
    try:
        page.goto(u,wait_until='domcontentloaded',timeout=15000); page.wait_for_timeout(1000)
        text=page.locator('body').inner_text(timeout=8000); ps=prices(text)
        if not ps or not expected:return None,None
        current=min(ps,key=lambda x:abs(x-expected))
        if abs(current-expected)/max(expected,1)>0.35:return None,None
        old=None
        patterns=[r'(?:önce|önceki fiyat|eski fiyat|liste fiyatı|liste fiyat|üstü çizili|indirim öncesi)\D{0,80}(\d[\d.,]*)\s*(?:TL|₺)',r'(\d[\d.,]*)\s*(?:TL|₺)\D{0,80}(?:indirim|tasarruf|kazanç)']
        for pat in patterns:
            m=re.search(pat,text,re.I|re.S)
            if m:
                old=money(m.group(1))
                if old and old>current:break
                old=None
        if old and (old-current)/old*100>=MIN_DISCOUNT:return current,old
        return current,None
    except: return None,None

def coupon_code(text):
    pats=[r'\b(?:KOD|KODU|KUPON|KUPON KODU|PROMOSYON(?: KODU)?)\s*[:=\-]?\s*([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{3,23})\b',r'\b([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23})\s+(?:KOD(?:U)?|KUPON(?:U)?)\b']
    for pat in pats:
        for m in re.finditer(pat,text or '',re.I):
            code=m.group(1).upper()
            if code.isdigit() or not re.search(r'[A-ZÇĞİÖŞÜ]',code) or code in {'INDIRIM','KAMPANYA','FIRSAT','AMAZON','HEPSIBURADA','TRENDYOL'}:continue
            return code
    return None

def collect_coupons():
    out=[]; now=datetime.now(timezone.utc)
    for source,channel in SOURCES.items():
        try:r=requests.get(f'https://t.me/s/{channel}',headers=HEAD,timeout=15)
        except:continue
        if r.status_code>=400:continue
        for b in BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message'):
            tm=b.select_one('time[datetime]'); tx=b.select_one('.tgme_widget_message_text')
            if not tm or not tx:continue
            try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
            except:continue
            if now-dt>timedelta(minutes=MAX_AGE):continue
            raw=tx.get_text(' ',strip=True); code=coupon_code(raw)
            st=next((x for x in MARKET.values() if re.search(r'\b'+re.escape(x)+r'\b',raw,re.I)),None)
            if code and st:out.append((st,code,raw,dt,source))
    return out

def match_coupon(s,title,raw,coupons):
    best=None; score=0; tt=tokens(title)
    for cs,code,ctext,dt,source in coupons:
        if cs!=s:continue
        overlap=len(tt&tokens(ctext))+(5 if re.search(r'\b'+re.escape(code)+r'\b',raw,re.I) else 0)
        if overlap>score:score=overlap;best=(code,source)
    return best if score>=1 else None

def seen(k):return bool(sb('GET','price_history',params={'select':'recorded_at','product_url':f'eq.telegram://{k}','limit':'1'}))
def remember(k):sb('POST','price_history',json={'price':0,'product_url':f'telegram://{k}','site':'telegram','recorded_at':datetime.now(timezone.utc).isoformat()})

def save(s,u,t,c,p):
    now=datetime.now(timezone.utc).isoformat(); rows=sb('GET','products',params={'select':'*','product_url':f'eq.{u}','limit':'1'}); payload={'product_name':t,'current_price':c,'previous_price':p,'product_url':u,'site':s,'updated_at':now}
    if rows:sb('PATCH',f'products?id=eq.{rows[0]["id"]}',json=payload);return rows[0]
    return (sb('POST','products',json=payload) or [payload])[0]

def send(s,u,t,c,p,source,post_id,signal,coupon=None):
    if not valid(s,u):return False
    key=f'{source}:{post_id}'
    if seen(key):return False
    disc=(p-c)/p*100 if p and p>c else None
    if disc is not None and disc<MIN_DISCOUNT:remember(key);return False
    if disc is None and not coupon and not DEAL_WORDS.search(signal or ''):remember(key);return False
    row=save(s,u,t,c,p); last=row.get('last_posted_at') if isinstance(row,dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):remember(key);return False
        except:pass
    lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else '🔥 SICAK FIRSAT','','🛍️ '+t,'',f'💰 {c:,.2f} TL']
    if disc is not None:lines.append(f'🏷️ Önce: {p:,.2f} TL')
    lines.append('🛒 '+s)
    if coupon:lines += ['',f'🎟️ Kupon Kodu: {coupon[0]}']
    if signal:lines += ['', '📌 '+re.sub(r'\s+',' ',signal)[:260]]
    lines += ['','👇 Fırsata git']
    payload={'chat_id':CHAT,'text':'\n'.join(lines),'disable_web_page_preview':False,'reply_markup':{'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}}
    requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json=payload,timeout=15).raise_for_status();sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat()});remember(key)
    print(f'GÖNDERİLDİ | {s} | {t[:70]} | {c:.2f} | kaynak={source} | link={u}');return True

def parse(block,page,coupons):
    tm=block.select_one('time[datetime]'); node=block.select_one('.tgme_widget_message_text')
    if not tm or not node:return None
    try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
    except:return None
    if abs(datetime.now(timezone.utc)-dt)>timedelta(minutes=MAX_AGE):return None
    raw=node.get_text('\n',strip=True); lines=[x.strip() for x in raw.splitlines() if x.strip()]
    hint=next((s for s in MARKET.values() if re.search(r'\b'+re.escape(s)+r'\b',raw,re.I)),None)
    title='Ürün'
    for line in lines:
        if re.search(r'(TL|₺|yerine|kupon|kod(?:u)?|sepette|indirim|kampanya|FIRSATA GİT)',line,re.I) or line.startswith(('http','#')):continue
        title=re.sub(r'^[^A-Za-zÇĞİÖŞÜ0-9]+','',line).strip()
        if len(title)>=4:break
    links=[]
    for a in block.select('a[href]'):
        original=clean(a.get('href',''))
        if not original.startswith('http'):continue
        s=site(original) or hint
        if s not in MARKET.values():continue
        resolved=resolve(page,original,s,title)
        if resolved and valid(s,resolved) and resolved not in [x[1] for x in links]:links.append((s,resolved))
    if not links and hint:
        found=search_marketplace(page,hint,title)
        if found:links.append((hint,found))
    if not links:return None
    current,previous=source_pair(raw); s,u=links[0]
    if current:
        mc,mp=marketplace_price_check(page,s,u,current)
        if mc is not None:current=mc
        if mp is not None:previous=mp
    if current is None:return None
    post_id=block.get('data-post','').split('/')[-1]
    if not post_id.isdigit():return None
    signal=' | '.join(x for x in lines[1:] if re.search(r'en düşük|son \d+|dip|ortalama|düştü|sepette|kupon|kod(?:u)?|kampanya|indirim|aktif|2 al 1|3 al 2|4 al 3|ödeme anında|%\s*\d+',x,re.I))[:500]
    coupon=match_coupon(s,title,raw,coupons)
    verified_discount=previous and previous>current and (previous-current)/previous*100>=MIN_DISCOUNT
    if not verified_discount and not coupon and not DEAL_WORDS.search(signal):return None
    return s,u,title[:300],current,previous,post_id,signal,coupon

def main():
    print('=== Telegram fırsat keşfi başladı ==='); candidates=[]; sent=0; coupons=collect_coupons(); print(f'Anlık kupon hafızası: {len(coupons)} aktif aday')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox']); page=browser.new_page(); page.set_default_timeout(15000)
        try:
            for source,channel in SOURCES.items():
                try:r=requests.get(f'https://t.me/s/{channel}',headers=HEAD,timeout=20)
                except Exception as e:print(f'Telegram kaynak hata {source}: {type(e).__name__}: {e}');continue
                print(f'Telegram kaynak {source}: HTTP {r.status_code}')
                if r.status_code>=400:continue
                for block in BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message'):
                    try:item=parse(block,page,coupons)
                    except Exception as e:print(f'Parse hata {source}: {type(e).__name__}: {e}');continue
                    if item:
                        candidates.append((source,item)); print(f'Aday: {item[0]} | {item[2][:80]} | {item[3]:.2f} TL | kaynak={source} | eski={item[4]}')
        finally:browser.close()
    for source,item in candidates:
        try:
            sent += 1 if send(item[0],item[1],item[2],item[3],item[4],source,item[5],item[6],item[7]) else 0
        except Exception as e:print(f'Ürün işlem hata {source}: {type(e).__name__}: {e}')
    print(f'=== Telegram fırsat keşfi bitti. Aday={len(candidates)} Gönderilen={sent} ===')

if __name__=='__main__':main()
