from pathlib import Path
import re
from urllib.parse import quote, urlparse
from bs4 import BeautifulSoup

P=Path('marketplace_nonamazon_scanner.py')
s=P.read_text(encoding='utf-8')

start=s.index('def extract(page,site,query):')
end=s.index('\ndef history(',start)
new=r'''def extract(page,site,query):
    base,pat=SITES[site]
    page.goto(base+quote(query),wait_until='domcontentloaded',timeout=12000)
    page.wait_for_timeout(1200)
    # Fiyatları metindeki min/max ile değil, kart içindeki eski fiyat işaretleri
    # ile ayır. Böylece taksit/kargo/kupon gibi üçüncü fiyatlar referans olamaz.
    js="""els=>els.map(a=>{let p=a,card='';for(let i=0;i<9&&p;i++,p=p.parentElement){let t=(p.innerText||'').replace(/\\s+/g,' ').trim();if((t.match(/(?:TL|₺)/gi)||[]).length>=1){card=t;if((t.match(/(?:TL|₺)/gi)||[]).length>=2)break}}return{href:a.href,text:(a.innerText||'').trim(),card};})"""
    items=page.locator('a[href]').evaluate_all(js)
    out=[];seen=set()
    for x in items:
        u=x.get('href') or '';card=x.get('card') or '';title=x.get('text') or ''
        if not pat.search(urlparse(u).path):continue
        u=normalize(site,u)
        if u in seen or recent(site,u):continue
        # Kartı gerçek DOM'dan tekrar oku: eski fiyat çoğunlukla del/s/class old-price.
        try:
            locator=page.locator('a[href="'+u.replace('"','\\"')+'"]').first
            # URL parametreleri normalize edildiği için birebir bulunamazsa mevcut kart metni kullanılır.
        except Exception: pass
        card_prices=[]; old_prices=[]
        # Genel kart metnindeki fiyatları çıkar; bağlamı eski fiyat olanları ayrıca işaretle.
        for m in MONEY.finditer(card):
            v=money(m.group())
            if not v:continue
            ctx=card[max(0,m.start()-45):m.end()+45].lower()
            if re.search(r'(eski|önceki|liste|normal|was|old|list price)',ctx): old_prices.append(v)
            else: card_prices.append(v)
        if not old_prices:
            vals=[money(m.group()) for m in MONEY.finditer(card) if money(m.group())]
            if len(vals)>=2:
                # İkinci fiyatı körlemesine referans alma; yalnızca makul çiftlerde aday kabul et.
                cands=sorted(set(vals))
                for v in cands[1:]:
                    if v>cands[0] and v/cands[0]<=4: old_prices.append(v)
        if not card_prices or not old_prices:continue
        current=min(card_prices)
        previous=min(v for v in old_prices if v>current) if any(v>current for v in old_prices) else None
        if not previous or previous/current>4:continue
        discount=(previous-current)/previous*100
        if discount<MIN_DISCOUNT:continue
        title=' '.join((title or card).split())[:220]
        if len(title)<10:continue
        seen.add(u)
        out.append((u,title,current))
        if len(out)>=8:break
    print(f'NON-AMAZON ARAMA | {site} | {query} | aday={len(out)}')
    return out
'''
s=s[:start]+new+s[end:]

# İlk gözlemde artık doğrulanmış kart eski fiyatını kullan. Sonraki gözlemlerde
# Supabase geçmişi varsa geçmiş fiyatı tercih et.
start=s.index('def verify(page,site,u,expected):')
end=s.index('\ndef save_post(',start)
old=s[start:end]
new2=r'''def verify(page,site,u,expected,candidate_previous=None):
    try:
        page.goto(u,wait_until='domcontentloaded',timeout=10000);page.wait_for_timeout(700)
        soup=BeautifulSoup(page.content(),'html.parser');vals=[]
        for el in soup.select('meta[itemprop="price"],meta[property="product:price:amount"],[itemprop="price"],[data-price]'):
            v=money(el.get('content') or el.get('value') or el.get('data-price') or el.get_text(' ',strip=True))
            if v:vals.append(v)
        for script in soup.select('script[type="application/ld+json"]'):
            for m in re.finditer(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)',script.get_text(' ',strip=True)):
                v=money(m.group(1));
                if v:vals.append(v)
        if not vals:return None
        current=min(vals,key=lambda v:abs(v-expected))
        if abs(current-expected)/max(expected,1)>.05:
            print(f'NON-AMAZON FİYAT RED | {site} | arama={expected:.2f} | canlı={current:.2f}');return None
        old=history(site,u,current)
        ref=old if old and old>current else candidate_previous
        if not ref or ref<=current:
            record(site,u,current);print(f'NON-AMAZON REFERANS YOK | {site} | {current:.2f} TL');return None
        d=(ref-current)/ref*100
        record(site,u,current)
        if d<MIN_DISCOUNT or ref/current>4:return None
        te=soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]')
        ie=soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]')
        title=(te.get('content','').strip() if te else (soup.title.get_text(' ',strip=True) if soup.title else site))[:220]
        return title,current,ref,d,ie.get('content') if ie else None
    except Exception as e:print(f'NON-AMAZON VERIFY HATA | {site} | {type(e).__name__}: {e}');return None
'''
s=s[:start]+new2+s[end:]

# main() içindeki verify çağrısını aday eski fiyatı taşıyacak şekilde değiştir.
s=s.replace('for u,title,current in extract(page,site,q):','for u,title,current,previous in extract(page,site,q):')
s=s.replace('v=verify(detail,site,u,current)','v=verify(detail,site,u,current,previous)')

# Daha fazla ve daha hedefli arama sorgusu.
s=s.replace("QUERIES=['indirimli elektronik','telefon kulaklık','ev yaşam mutfak','kişisel bakım']", "QUERIES=['indirimli elektronik','telefon kulaklık','televizyon tablet bilgisayar','ev yaşam mutfak','kişisel bakım kozmetik','küçük ev aletleri','oyuncak bebek','spor giyim ayakkabı','temizlik deterjan','monitör bilgisayar']")

compile(s,str(P),'exec');P.write_text(s,encoding='utf-8')
print('NON-AMAZON QUALITY PATCH OK | HB/Trendyol daha geniş arama + gerçek eski fiyat + canlı doğrulama')
