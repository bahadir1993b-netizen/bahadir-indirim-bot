import os,re,requests,hashlib
from datetime import datetime,timezone,timedelta
from bs4 import BeautifulSoup

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']
SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_KEY=os.environ['SUPABASE_SERVICE_KEY']
CHANNEL_ID='-1004424116637'
# Workflow 5 dakikada bir çalışıyor. Eski kuponları değil, yalnızca gerçekten yeni gelenleri yakala.
# 15 dk tolerans: GitHub Actions gecikirse bile kaçırmamak için 3 çalışma aralığı.
MAX_AGE_MINUTES=15
SOURCES={'OnuAl':'onual_firsat','Enes ÖZEN':'enesozen','İndirim Bakanlığı':'indirimbakanligi','Cihaz.TV':'cihaztv'}
SITES=('Amazon','Hepsiburada','Trendyol')
COMMON={'INDIRIM','KAMPANYA','FIRSAT','KODU','KOD','KUPON','PROMOSYON','AMAZON','HEPSIBURADA','TRENDYOL','TL','TRY','MOBIL','UYGULAMADA','SEPETTE'}
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}

def sb(method,path,**kwargs):
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method=='POST': h['Prefer']='return=representation'
    r=requests.request(method,f'{SUPABASE_URL}/rest/v1/{path}',headers=h,timeout=15,**kwargs); r.raise_for_status(); return r.json() if r.text else []

def seen(key): return bool(sb('GET','price_history',params={'select':'recorded_at','product_url':f'eq.coupon://{key}','limit':'1'}))
def remember(key): sb('POST','price_history',json={'price':0,'product_url':f'coupon://{key}','site':'coupon','recorded_at':datetime.now(timezone.utc).isoformat()})

def extract_code(text):
    patterns=[r'\b(?:KOD|KODU|KUPON|KUPON KODU|PROMOSYON(?: KODU)?)\s*[:=\-]?\s*([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{3,23})\b',r'\b([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23})\s+(?:KOD(?:U)?|KUPON(?:U)?)\b']
    for pat in patterns:
        for m in re.finditer(pat,text,re.I):
            code=m.group(1).strip(' -_:')
            if code.isdigit() or code in COMMON or not re.search(r'[A-ZÇĞİÖŞÜ]',code,re.I): continue
            return code
    return None

def site_of(text):
    t=text.lower()
    for s in SITES:
        if s.lower() in t:return s
    return None

def money_values(text):
    return [m.group(0).strip() for m in re.finditer(r'(?<![A-ZÇĞİÖŞÜ])\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?\s*(?:tl|₺)(?![A-ZÇĞİÖŞÜ])',text,re.I)]

def discount_amount(text):
    m=re.search(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:TL|₺)\s*(?:İNDİRİM|INDIRIM|KAZANÇ|KAZANC|AVANTAJ)',text,re.I)
    return f'{m.group(1)} TL' if m else None

def min_spend(text):
    patterns=[r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:TL|₺)\s*(?:ve|üzeri|uzeri|üzerine|tutar|alışveriş|alisveris)',r'(?:minimum|min\.?|en az)\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:TL|₺)']
    for pat in patterns:
        m=re.search(pat,text,re.I)
        if m:return ' '.join(m.group(0).split())
    return None

def conditions(raw,code):
    parts=re.split(r'\n+|(?<=[.!?])\s+',raw); keys=('alt limit','minimum','min.','en az','üzeri','uzeri','üzerine','uzerine','sepet','adet','ürün','urun','kategori','marka','geçerli','gecerli','kod','kupon','kampanya','alışveriş','alisveris','hariç','haric'); out=[]
    for part in parts:
        p=' '.join(part.split()).strip(' -•')
        if not p:continue
        if re.fullmatch(r'(?:KOD|KODU|KUPON|KUPON KODU)\s*[:=\-]?\s*'+re.escape(code),p,re.I):continue
        if any(k in p.lower() for k in keys) and p not in out: out.append(p)
    return out[:5]

def product_link(block,site):
    for a in block.find_all('a',href=True):
        u=a['href']
        if site=='Amazon' and 'amazon.com.tr' in u:return u
        if site=='Hepsiburada' and ('hepsiburada.com' in u or 'hb.biz' in u):return u
        if site=='Trendyol' and ('trendyol.com' in u or 'ty.gl' in u):return u
    return None

def fetch(source,channel):
    url=f'https://t.me/s/{channel}'; r=requests.get(url,headers=HEADERS,timeout=20); print(f'Kupon kaynağı {source}: HTTP {r.status_code}')
    if r.status_code>=400:return 0
    soup=BeautifulSoup(r.text,'html.parser'); now=datetime.now(timezone.utc); sent=0
    for block in soup.select('.tgme_widget_message'):
        tm=block.select_one('time[datetime]')
        if not tm:continue
        try: dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
        except: continue
        age=now-dt
        if age<timedelta(0) or age>timedelta(minutes=MAX_AGE_MINUTES):continue
        text=block.select_one('.tgme_widget_message_text')
        if not text:continue
        raw=text.get_text('\n',strip=True); site=site_of(raw)
        if site not in SITES:continue
        code=extract_code(raw)
        if not code:continue
        post_id=block.get('data-post','').split('/')[-1] or hashlib.sha1(raw.encode()).hexdigest()[:16]; key=f'{channel}:{post_id}:{code}'
        if seen(key):continue
        link=product_link(text,site); source_link=f'https://t.me/{channel}/{post_id}' if post_id.isdigit() else url
        prices=money_values(raw); disc=discount_amount(raw); minimum=min_spend(raw); cond=conditions(raw,code)
        lines=[f'🎟️ {site.upper()} İNDİRİM KODU','',f'🏷️ Kod: {code}']
        if disc: lines.append(f'💸 İndirim: {disc}')
        if minimum: lines.append(f'🛒 Minimum alışveriş: {minimum}')
        elif prices: lines.append(f'💰 Kaynakta görünen fiyat: {prices[0]}')
        if cond: lines += ['', '📋 Kampanya şartları:'] + [f'• {x}' for x in cond]
        if link: lines += ['',f'🔗 Ürün/Kampanya: {link}']
        lines += ['',f'📌 Kaynak: {source}',f'🕒 Kaynak paylaşımı: {dt.astimezone().strftime("%d.%m.%Y %H:%M")}',f'🔎 Kaynak gönderisi: {source_link}']
        rr=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHANNEL_ID,'text':'\n'.join(lines),'disable_web_page_preview':False},timeout=15); rr.raise_for_status(); remember(key); sent+=1
        print(f'KUPON GÖNDERİLDİ | {site} | {code} | {source_link}')
    return sent

def main():
    total=0
    for source,channel in SOURCES.items():
        try: total+=fetch(source,channel)
        except Exception as e: print(f'Kupon kaynak hata {source}: {type(e).__name__}: {e}')
    print(f'=== Kupon monitörü bitti. Gönderilen: {total} ===')

if __name__=='__main__': main()
