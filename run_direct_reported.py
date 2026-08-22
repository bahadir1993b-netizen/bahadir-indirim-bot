import time,json,shutil,subprocess,re
from datetime import datetime,timezone
import run_serper_reported as r

def parse_direct(line,items,run):
    s=line.strip();r.parse_line(line,items,run)
    m=re.match(r'^KONTROL:\s*([^|]+)\|\s*([\d.]+)\s*->\s*ref\s*([\d.]+)\s*\|\s*%([\d.]+).*?\|\s*ref=([^|]+)\|\s*(.+)$',s)
    if m:
        site,cur,ref,disc,refsrc,title=m.groups();row=r.ensure(items,title.strip(),site.strip());row.update({'current_price':float(cur),'reference_price':float(ref),'discount_pct':float(disc),'reference_source':refsrc.strip()});row['events'].append('direct_check');return
    m=re.match(r'^REFERANS YOK:\s*([^|]+)\|\s*([\d.]+).*?neden=([^|]+)\|\s*(.+)$',s)
    if m:
        site,cur,why,title=m.groups();row=r.ensure(items,title.strip(),site.strip());row.update({'current_price':float(cur),'result':'GÖNDERİLMEDİ','reason':'REFERANS_YOK','reference_source':why.strip()});row['events'].append('referans_yok');return
    m=re.match(r'^FİYAT YOK:\s*([^|]+)\|\s*(.+)$',s)
    if m:
        site,title=m.groups();row=r.ensure(items,title.strip(),site.strip());row.update({'result':'GÖNDERİLMEDİ','reason':'FİYAT_YOK'});row['events'].append('fiyat_yok');return
    m=re.match(r'^GÖNDERİLDİ:\s*([^|]+)\|\s*([\d.]+)->([\d.]+)\s*\|\s*%([\d.]+).*?\|\s*(.+)$',s)
    if m:
        site,cur,ref,disc,title=m.groups();row=r.ensure(items,title.strip(),site.strip());row.update({'current_price':float(cur),'reference_price':float(ref),'discount_pct':float(disc),'result':'GÖNDERİLDİ'});row['events'].append('gonderildi');return
    if s.startswith('=== API-SİZ V4 BİTTİ'):
        run['summary_line']=s;run['summary']={k:int(v) for k,v in re.findall(r'([a-z_]+)=([0-9]+)',s)}

def main():
    r.discover_chat_id();stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S');raw_path=r.REPORT_DIR/f'{stamp}_raw.log';json_path=r.REPORT_DIR/f'{stamp}.json';txt_path=r.REPORT_DIR/f'{stamp}.txt'
    run={'started_at':datetime.now(timezone.utc).isoformat(),'report_version':7,'engine':'run_direct_watch_v3.py','mode':'api-free-v4-multisource'};items={};start=time.time()
    with raw_path.open('w',encoding='utf-8') as raw:
        p=subprocess.Popen(['python','-u','run_direct_watch_v3.py'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in p.stdout:print(line,end='');raw.write(line);raw.flush();parse_direct(line,items,run)
        code=p.wait()
    run['exit_code']=code;run['finished_at']=datetime.now(timezone.utc).isoformat();run['duration_seconds']=round(time.time()-start,2);payload={'run':run,'items':list(items.values())}
    json_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');txt_path.write_text(r.human_report(run,items,raw_path).replace('SERPER V2 TUR RAPORU','API-SİZ V4 ÇOK KAYNAKLI FİYAT RAPORU'),encoding='utf-8')
    shutil.copyfile(json_path,r.REPORT_DIR/'latest_report.json');shutil.copyfile(txt_path,r.REPORT_DIR/'latest_report.txt');shutil.copyfile(raw_path,r.REPORT_DIR/'latest_raw.log')
    print(f'RAPOR HAZIR: {json_path} | {txt_path} | ürün={len(items)}')
    if r.REPORT_CHAT_ID:r.send_report(txt_path,'API-siz V4 fiyat raporu');r.send_report(json_path,'API-siz V4 JSON raporu')
    raise SystemExit(code)

if __name__=='__main__':main()
