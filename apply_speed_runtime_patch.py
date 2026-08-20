from pathlib import Path
p = Path('marketplace_scanner.py')
s = p.read_text(encoding='utf-8')
s = s.replace("QUERIES = ['indirim','fırsat','kampanya','elektronik','telefon','kulaklık','televizyon','ev yaşam','mutfak','kişisel bakım','bebek','oyuncak','spor','kozmetik']", "QUERIES = ['indirim','kampanya','elektronik','telefon','kulaklık','televizyon','ev yaşam','mutfak']")
s = s.replace('timeout=12000', 'timeout=8000')
s = s.replace('page.wait_for_timeout(1000)', 'page.wait_for_timeout(300)')
marker = "if __name__ == '__main__':"
if '_SPEED_ORIGINAL_EXTRACT' not in s:
    wrapper = r'''
_SPEED_ORIGINAL_EXTRACT = extract_search_candidates
_SPEED_COUNTS = {'Amazon': 0, 'Hepsiburada': 0, 'Trendyol': 0}

def extract_search_candidates(page, site, query):
    used = _SPEED_COUNTS.get(site, 0)
    if used >= 6:
        return []
    out = _SPEED_ORIGINAL_EXTRACT(page, site, query)
    remaining = 6 - used
    out = out[:remaining]
    _SPEED_COUNTS[site] = used + len(out)
    return out

'''
    s = s.replace(marker, wrapper + marker, 1)
p.write_text(s, encoding='utf-8')
compile(s, str(p), 'exec')
print('Speed runtime patch OK | 8 sorgu | site başına 6 aday | timeout 8s')
