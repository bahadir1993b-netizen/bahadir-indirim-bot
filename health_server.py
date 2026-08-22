import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPORT_DIR=Path('/app/data/reports')
PORT=int(os.environ.get('HEALTH_PORT','8787'))

SAFE_KEYS={
    'Hedef','Amazon','HB','Trendyol','fiyat_yok','link_yok','stok_yok','stok_belirsiz',
    'referans_yok','esik_alti','cooldown','hata','piyasa_engel','piyasa_ref',
    'kampanya','render_ok','render_stok','render_belirsiz','render_fiyat','Gönderilen'
}

def latest_payload():
    p=REPORT_DIR/'latest_report.json'
    if not p.exists():
        return {'ok':False,'status':'no_report_yet'}
    try:
        data=json.loads(p.read_text('utf-8'))
        run=data.get('run') or {}
        sm=run.get('summary') or {}
        safe={k:v for k,v in sm.items() if k in SAFE_KEYS}
        return {
            'ok':True,
            'status':'running',
            'started_at':run.get('started_at'),
            'finished_at':run.get('finished_at'),
            'duration_seconds':run.get('duration_seconds'),
            'exit_code':run.get('exit_code'),
            'summary':safe,
            'report_version':run.get('report_version')
        }
    except Exception as e:
        return {'ok':False,'status':'report_error','error':type(e).__name__}

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/','/health','/health.json'):
            self.send_response(404);self.end_headers();return
        body=json.dumps(latest_payload(),ensure_ascii=False,indent=2).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def log_message(self,fmt,*args):
        pass

if __name__=='__main__':
    HTTPServer(('0.0.0.0',PORT),H).serve_forever()
