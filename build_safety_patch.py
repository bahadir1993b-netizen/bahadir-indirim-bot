"""Build-time validation only.

Runtime safety rules now live in the actual source modules and publish_core.py.
Do not rewrite source files during image builds: textual patching caused fixes to
silently diverge between services.
"""
from pathlib import Path

FILES=[
    'publish_core.py','local_store.py','sitecustomize.py','run_telegram_realtime.py',
    'run_trusted_fast_lane.py','run_web_first_deals.py','run_direct_watch_v3.py',
    'run_price_analyst.py','health_server.py'
]
for name in FILES:
    p=Path(name)
    if not p.exists():raise SystemExit(f'build safety: missing {name}')
    source=p.read_text(encoding='utf-8')
    compile(source,name,'exec')
print('build safety validation OK (no source rewriting)')
