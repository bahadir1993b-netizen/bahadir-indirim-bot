import os,re,json,time,subprocess,shutil,requests
from pathlib import Path
from datetime import datetime,timezone

REPORT_DIR=Path('/app/data/reports');REPORT_DIR.mkdir(parents=True,exist_ok=True)
CHAT_CACHE=Path('/app/data/report_chat_id.txt')
BOT_TOKEN=(os.environ.get('TELEGRAM_BOT_TOKEN') or os.environ.get('BOT_TOKEN') or '').strip()
REPORT_CHAT_ID=(os.environ.get('REPORT_CHAT_ID') or '').strip()

def discover_chat_id():
    global REPORT_CHAT_ID
    if REPORT_CHAT_ID:return REPORT_CHAT_ID
    try:
        if CHAT_CACHE.exists():
            x=CHAT_CACHE.read_text('utf-8').strip()
            if x:REPORT_CHAT_ID=x;return x
    except Exception:pass
    if not BOT_TOKEN:return ''
    try:
        r=requests.get(f'https://api.telegram.org/bot{BOT_TOKEN}/getUpdates',timeout=15)
        if r.ok:
            for u in reversed(r.json().get('result') or []):
                m=u.get('message') or u.get('edited_message') or {}
                c=m.get('chat') or {}
                if c.get('type')=='private' and c.get('id'):
                    REPORT_CHAT_ID=str(c['id']);CHAT_CACHE.write_text(REPORT_CHAT_ID,'utf-8');print(f'RAPOR CHAT otomatik bulundu: {REPORT_CHAT_ID}');return REPORT_CHAT_ID
    except Exception as e:print(f'RAPOR CHAT BULMA HATA: {type(e).__name__}: {e}')
    return ''

def key(title):return re.sub(r'\s+',' ',(title or '').strip().lower())[:180]
def ensure(items,title,site=''):
    k=key(title) or f'unknown-{len(items)+1}';row=items.setdefault(k,{'title':title.strip() if title else '','site':site,'events':[]})
    if title and len(title)>len(row.get('title','')):row['title']=title.strip()
    if site:row['site']=site
    return row

def parse_line(line,items,run):
    s=line.strip()
    m=re.search(r'^Kontrol:\s*([^|]+)\|\s*([\d.]+) TL\s*\|\s*referans=([\d.]+)\s*\|\s*geçmiş=(\d+)\s*\|\s*kaynak=([^|]+)\|\s*(.+)$',s)
    if m:
        site,current,ref,hist,src,title=m.groups();r=ensure(items,title,site.strip());r.update({'current_price':float(current),'reference_price':float(ref),'history_count':int(hist),'reference_source':src.strip()});r['events'].append('kontrol');return
    m=re.search(r'^PİYASA KONTROL:\s*([^|]+)\|\s*ürün=([\d.]+)\s*\|\s*en_ucuz=([\d.]+)\s*\|\s*medyan=([\d.]+)\s*\|\s*n=(\d+)\s*\|\s*(.+)$',s)
    if m:
        site,cur,floor,med,n,title=m.groups();r=ensure(items,title,site.strip());r.update({'market_floor':float(floor),'market_median':float(med),'market_samples':int(n)});r['events'].append('piyasa_kontrol');return
    m=re.search(r'^PİYASA ENGELİ:\s*([^|]+)\|\s*(.*?)\|\s*(.+)$',s)
    if m:
        site,reason,title=m.groups();r=ensure(items,title,site.strip());r.update({'result':'GÖNDERİLMEDİ','reason':'PİYASA_ENGELİ: '+reason.strip()});r['events'].append('piyasa_engeli');return
    m=re.search(r'^STOK ENGELİ:\s*([^|]+)\|\s*stokta yok\s*\|\s*(.*?)\|\s*(https?://\S+)',s)
    if m:
        site,title,url=m.groups();r=ensure(items,title,site.strip());r.update({'url':url,'stock_http':'YOK','result':'GÖNDERİLMEDİ','reason':'STOK_YOK'});r['events'].append('stok_engeli');return
    m=re.search(r'^STOK BELİRSİZ:\s*([^|]+)\|.*?\|\s*(.+)$',s)
    if m:
        site,title=m.groups();r=ensure(items,title,site.strip());r['stock_http']='BELİRSİZ';r['events'].append('stok_belirsiz');return
    m=re.search(r'^RENDER STOK ENGELİ:\s*([^|]+)\|\s*(.+)$',s)
    if m:
        site,title=m.groups();r=ensure(items,title,site.strip());r.update({'stock_render':'YOK','result':'GÖNDERİLMEDİ','reason':'RENDER_STOK_YOK'});r['events'].append('render_stok_engeli');return
    m=re.search(r'^RENDER DOĞRULANAMADI:\s*([^|]+)\|.*?\|\s*(.+)$',s)
    if m:
        site,title=m.groups();r=ensure(items,title,site.strip());r.update({'stock_render':'BELİRSİZ','result':'GÖNDERİLMEDİ','reason':'RENDER_DOĞRULANAMADI'});r['events'].append('render_belirsiz');return
    m=re.search(r'^RENDER FİYAT DÜZELTİLDİ:\s*([^|]+)\|\s*([\d.]+)->([\d.]+)\s*\|\s*(.+)$',s)
    if m:
        site,old,new,title=m.groups();r=ensure(items,title,site.strip());r.update({'pre_render_price':float(old),'render_price':float(new)});r['events'].append('render_fiyat_duzeltildi');return
    m=re.search(r'^CANLI FİYAT DÜZELTİLDİ:\s*([^|]+)\|\s*Serper=([\d.]+) -> Sayfa=([\d.]+)\s*\|\s*(.+)$',s)
    if m:
        site,old,new,title=m.groups();r=ensure(items,title,site.strip());r.update({'serper_price':float(old),'page_price':float(new)});r['events'].append('canli_fiyat_duzeltildi');return
    m=re.search(r'^Direkt ürün linki doğrulandı:\s*([^|]+)\|\s*(https?://\S+)',s)
    if m:run.setdefault('resolved_links',[]).append({'site':m.group(1).strip(),'url':m.group(2)});return
    m=re.search(r'^Telegram HTTP:\s*(\d+)\s*\|\s*foto=(\S+)',s)
    if m:run.setdefault('telegram_results',[]).append({'http':int(m.group(1)),'photo':m.group(2)});return
    if s.startswith('=== Bitti.'):
        run['summary_line']=s;pairs=re.findall(r'([A-Za-z_çğıöşüÇĞİÖŞÜ]+)=([\d.]+)',s);run['summary']={k:(float(v) if '.' in v else int(v)) for k,v in pairs}

def human_report(run,items,raw_path):
    lines=['BAHADIR İNDİRİM BOTU - SERPER V2 TUR RAPORU','='*60,f"Başlangıç: {run.get('started_at','')}",f"Bitiş: {run.get('finished_at','')}",f"Süre: {run.get('duration_seconds',0):.1f} sn",'','ÖZET','-'*60,run.get('summary_line','Özet satırı bulunamadı.'),'','ÜRÜNLER / TEŞHİS','-'*60]
    rows=list(items.values());rows.sort(key=lambda x:(x.get('result')!='GÖNDERİLDİ',x.get('site',''),x.get('title','')))
    for i,r in enumerate(rows,1):
        lines += [f'[{i}] {r.get("site","")} | {r.get("title","")}',f'  Fiyat: {r.get("current_price",r.get("page_price",r.get("serper_price","?")))}',f'  Referans: {r.get("reference_price","?")} | kaynak={r.get("reference_source","?")}',f'  Piyasa: en_ucuz={r.get("market_floor","?")} medyan={r.get("market_median","?")} n={r.get("market_samples","?")}',f'  Stok HTTP: {r.get("stock_http","?")} | Render: {r.get("stock_render","?")}',f'  Sonuç: {r.get("result","İNCELENDİ")} | Neden: {r.get("reason","-")}',f'  URL: {r.get("url","-")}',f'  Olaylar: {", ".join(r.get("events",[])) or "-"}','']
    lines += ['HAM LOG','-'*60,f'Bakınız: {raw_path.name}'];return '\n'.join(lines)

def send_report(path,caption):
    if not BOT_TOKEN or not REPORT_CHAT_ID:return
    try:
        with path.open('rb') as f:r=requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendDocument',data={'chat_id':REPORT_CHAT_ID,'caption':caption[:900]},files={'document':(path.name,f)},timeout=30)
        print(f'RAPOR TELEGRAM: {path.name} HTTP={r.status_code}')
    except Exception as e:print(f'RAPOR TELEGRAM HATA: {type(e).__name__}: {e}')

def main():
    discover_chat_id();stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S');raw_path=REPORT_DIR/f'{stamp}_raw.log';json_path=REPORT_DIR/f'{stamp}.json';txt_path=REPORT_DIR/f'{stamp}.txt';run={'started_at':datetime.now(timezone.utc).isoformat(),'report_version':2,'engine':'run_serper_v2.py'};items={};start=time.time()
    with raw_path.open('w',encoding='utf-8') as raw:
        p=subprocess.Popen(['python','-u','run_serper_v2.py'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in p.stdout:print(line,end='');raw.write(line);raw.flush();parse_line(line,items,run)
        code=p.wait()
    run['exit_code']=code;run['finished_at']=datetime.now(timezone.utc).isoformat();run['duration_seconds']=round(time.time()-start,2)
    for r in items.values():
        cur=r.get('current_price');ref=r.get('reference_price')
        if cur and ref and ref>cur:r['discount_pct']=round((ref-cur)/ref*100,2)
    payload={'run':run,'items':list(items.values())};json_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');txt_path.write_text(human_report(run,items,raw_path),encoding='utf-8')
    shutil.copyfile(json_path,REPORT_DIR/'latest_report.json');shutil.copyfile(txt_path,REPORT_DIR/'latest_report.txt');shutil.copyfile(raw_path,REPORT_DIR/'latest_raw.log')
    print(f'RAPOR HAZIR: {json_path} | {txt_path} | ürün={len(items)}')
    if REPORT_CHAT_ID:send_report(txt_path,'Serper V2 tur teşhis raporu');send_report(json_path,'Serper V2 JSON raporu')
    raise SystemExit(code)
if __name__=='__main__':main()
