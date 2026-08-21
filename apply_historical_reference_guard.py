from pathlib import Path


def patch_marketplace():
    p = Path('marketplace_scanner.py')
    s = p.read_text(encoding='utf-8')
    if 'def _bahadir_historical_only_verify' in s:
        return
    marker = "if __name__=='__main__':"
    if marker not in s:
        marker = "if __name__ == '__main__':"
    guard = r'''

_ORIGINAL_HISTORICAL_VERIFY = verify

def _bahadir_historical_only_verify(page, site, url, fallback_title, expected_current, candidate_previous):
    try:
        historical = history(site, url, expected_current)
    except Exception:
        historical = None
    if not historical or historical <= expected_current:
        record_price(site, url, expected_current)
        print(f'HARİCİ REFERANS YOK | {site} | canlı={expected_current:.2f} | RED | ilk gözlem')
        return None
    result = _ORIGINAL_HISTORICAL_VERIFY(page, site, url, fallback_title, expected_current, historical)
    if result:
        return result
    print(f'HARİCİ REFERANS DOĞRULAMA RED | {site} | canlı={expected_current:.2f} | önceki={historical:.2f}')
    return None

verify = _bahadir_historical_only_verify

'''
    if marker not in s:
        raise RuntimeError('marketplace marker bulunamadı')
    s = s.replace(marker, guard + marker, 1)
    p.write_text(s, encoding='utf-8')
    compile(s, str(p), 'exec')


def patch_nonamazon():
    p = Path('marketplace_nonamazon_scanner.py')
    if not p.exists():
        return
    s = p.read_text(encoding='utf-8')
    if 'def _bahadir_historical_only_nonamazon_verify' in s:
        return
    marker = "if __name__=='__main__':"
    if marker not in s:
        marker = "if __name__ == '__main__':"
    guard = r'''

_ORIGINAL_NONAMAZON_VERIFY = verify

def _bahadir_historical_only_nonamazon_verify(page, site, url, fallback_title, expected_current, candidate_previous=None):
    try:
        historical = history(site, url, expected_current)
    except Exception:
        historical = None
    if not historical or historical <= expected_current:
        record_price(site, url, expected_current)
        print(f'NON-AMAZON REFERANS YOK | {site} | canlı={expected_current:.2f} | RED | ilk gözlem')
        return None
    return _ORIGINAL_NONAMAZON_VERIFY(page, site, url, fallback_title, expected_current, historical)

verify = _bahadir_historical_only_nonamazon_verify

'''
    if marker in s:
        s = s.replace(marker, guard + marker, 1)
        p.write_text(s, encoding='utf-8')
        compile(s, str(p), 'exec')

patch_marketplace()
patch_nonamazon()
print('HISTORICAL REFERENCE GUARD OK | önceki fiyat sadece gerçek fiyat geçmişinden | ilk gözlem paylaşılmaz')
