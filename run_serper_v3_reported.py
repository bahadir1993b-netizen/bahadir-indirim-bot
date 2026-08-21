import time,json,shutil,subprocess
from datetime import datetime,timezone
import run_serper_reported as r

def main():
    r.discover_chat_id();stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    raw_path=r.REPORT_DIR/f'{stamp}_raw.log';json_path=r.REPORT_DIR/f'{stamp}.json';txt_path=r.REPORT_DIR/f'{stamp}.txt'
    run={'started_at':datetime.now(timezone.utc).isoformat(),'report_version':3,'engine':'run_serper_v3.py'};items={};start=time.time()
    with raw_path.open('w',encoding='utf-8') as raw:
        p=subprocess.Popen(['python','-u','run_serper_v3.py'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in p.stdout:
            print(line,end='');raw.write(line);raw.flush();r.parse_line(line,items,run)
        code=p.wait()
    run['exit_code']=code;run['finished_at']=datetime.now(timezone.utc).isoformat();run['duration_seconds']=round(time.time()-start,2)
    for row in items.values():
        cur=row.get('current_price');ref=row.get('reference_price')
        if cur and ref and ref>cur:row['discount_pct']=round((ref-cur)/ref*100,2)
    payload={'run':run,'items':list(items.values())}
    json_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    txt_path.write_text(r.human_report(run,items,raw_path),encoding='utf-8')
    shutil.copyfile(json_path,r.REPORT_DIR/'latest_report.json');shutil.copyfile(txt_path,r.REPORT_DIR/'latest_report.txt');shutil.copyfile(raw_path,r.REPORT_DIR/'latest_raw.log')
    print(f'RAPOR HAZIR: {json_path} | {txt_path} | ürün={len(items)}')
    if r.REPORT_CHAT_ID:
        r.send_report(txt_path,'Serper V3 tur teşhis raporu');r.send_report(json_path,'Serper V3 JSON raporu')
    raise SystemExit(code)

if __name__=='__main__':main()
