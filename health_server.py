import json,os
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from datetime import datetime,timezone
import local_store as ls

REPORT_DIR=Path('/app/data/reports');PORT=int(os.environ.get('HEALTH_PORT','8787'))
EXPECTED_MAX_AGE={'trusted-fast-lane':300,'web-first':600,'direct':1800}

def age_seconds(value):
    if not value:return None
    try:return int((datetime.now(timezone.utc)-datetime.fromisoformat(str(value).replace('Z','+00:00'))).total_seconds())
    except:return None

def report_info():
    p=REPORT_DIR/'latest_report.json'
    if not p.exists():return {'status':'no_report_yet'}
    try:
        data=json.loads(p.read_text('utf-8'));run=data.get('run') or {}
        return {'status':'ok','started_at':run.get('started_at'),'finished_at':run.get('finished_at'),'age_seconds':age_seconds(run.get('finished_at')),'duration_seconds':run.get('duration_seconds'),'exit_code':run.get('exit_code'),'report_version':run.get('report_version')}
    except Exception as e:return {'status':'error','error':type(e).__name__}

def latest_payload():
    try:snap=ls.runtime_snapshot()
    except Exception as e:return {'ok':False,'status':'db_error','error':type(e).__name__,'report':report_info()}
    services=[];alerts=[]
    by={x.get('service'):x for x in snap.get('services',[])}
    for name,max_age in EXPECTED_MAX_AGE.items():
        row=dict(by.get(name) or {'service':name,'status':'never_seen'});age=age_seconds(row.get('finished_at') or row.get('started_at'));row['age_seconds']=age;row['expected_max_age_seconds']=max_age
        if not by.get(name):alerts.append(f'{name}: hiç çalışma kaydı yok')
        elif row.get('status')=='error':alerts.append(f'{name}: son tur hata')
        elif age is not None and age>max_age:alerts.append(f'{name}: son başarılı tur eski ({age} sn)')
        elif int(row.get('errors') or 0)>0:alerts.append(f'{name}: son turda {row.get("errors")} hata')
        services.append(row)
    last_pub=snap.get('last_publish');pub_age=age_seconds(last_pub.get('published_at')) if last_pub else None
    return {'ok':not any('son tur hata' in x or 'hiç çalışma' in x or 'başarılı tur eski' in x for x in alerts),'status':'healthy' if not alerts else 'warning','alerts':alerts,'services':services,'last_publish':last_pub,'last_publish_age_seconds':pub_age,'report':report_info()}

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/','/health','/health.json'):
            self.send_response(404);self.end_headers();return
        body=json.dumps(latest_payload(),ensure_ascii=False,indent=2).encode('utf-8');self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,fmt,*args):pass

if __name__=='__main__':HTTPServer(('0.0.0.0',PORT),H).serve_forever()