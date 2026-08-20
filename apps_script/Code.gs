const SITES = {
  Hepsiburada: {
    search: 'https://www.hepsiburada.com/ara?q=',
    host: 'hepsiburada.com',
    re: /-p-[A-Za-z0-9]+(?:[/?#&]|$)/i
  },
  Trendyol: {
    search: 'https://www.trendyol.com/sr?q=',
    host: 'trendyol.com',
    re: /-p-\d+(?:[/?#&]|$)/i
  }
};

function doGet(e) {
  try {
    const site = String((e.parameter && e.parameter.site) || '').trim();
    const term = String((e.parameter && e.parameter.q) || '').trim();
    if (!SITES[site] || !term) return json({ok:false,error:'site ve q gerekli'});

    const direct = directSearch(site, term);
    if (direct.products.length) {
      return json({ok:true,site:site,q:term,source:'marketplace',http_status:direct.status,products:direct.products.slice(0,20)});
    }

    let products = bingHtmlSearch(site, term);
    let source = 'bing-html';
    if (!products.length) {
      products = bingRssSearch(site, term);
      source = 'bing-rss';
    }
    if (!products.length) {
      products = googleSearch(site, term);
      source = 'google';
    }

    return json({ok:true,site:site,q:term,source:source,http_status:direct.status,products:products.slice(0,20)});
  } catch (err) {
    return json({ok:false,error:String(err)});
  }
}

function request(url) {
  return UrlFetchApp.fetch(url, {
    muteHttpExceptions:true,
    followRedirects:true,
    headers:{
      'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36',
      'Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8',
      'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
  });
}

function directSearch(site, term) {
  const cfg = SITES[site];
  try {
    const res = request(cfg.search + encodeURIComponent(term));
    const status = res.getResponseCode();
    return {status:status,products:extractProducts(site,res.getContentText())};
  } catch (_) {
    return {status:0,products:[]};
  }
}

function bingHtmlSearch(site, term) {
  const cfg = SITES[site];
  const query = 'site:' + cfg.host + ' ' + term;
  const out = [];
  const seen = {};
  try {
    const res = request('https://www.bing.com/search?q=' + encodeURIComponent(query) + '&count=30&setlang=tr');
    const html = normalizeSearchText(res.getContentText());
    extractSearchLinks(site, html, out, seen);
  } catch (_) {}
  return out;
}

function bingRssSearch(site, term) {
  const cfg = SITES[site];
  const out = [];
  const seen = {};
  try {
    const res = request('https://www.bing.com/search?format=rss&count=50&q=' + encodeURIComponent('site:' + cfg.host + ' ' + term));
    const xml = normalizeSearchText(res.getContentText());
    const links = xml.match(/<link>[\s\S]*?<\/link>/gi) || [];
    for (const tag of links) {
      let raw = tag.replace(/^<link>/i,'').replace(/<\/link>$/i,'').trim();
      addSearchUrl(site, raw, out, seen);
      if (out.length >= 20) break;
    }
    if (out.length < 20) extractSearchLinks(site, xml, out, seen);
  } catch (_) {}
  return out;
}

function googleSearch(site, term) {
  const cfg = SITES[site];
  const out = [];
  const seen = {};
  try {
    const q = 'site:' + cfg.host + ' ' + term;
    const res = request('https://www.google.com/search?q=' + encodeURIComponent(q) + '&num=30&hl=tr&gl=tr');
    const html = normalizeSearchText(res.getContentText());
    extractSearchLinks(site, html, out, seen);
  } catch (_) {}
  return out;
}

function extractSearchLinks(site, html, out, seen) {
  const cfg = SITES[site];
  const text = normalizeSearchText(html);

  // 1) href içindeki her adayı çöz; Google sonuçları çoğunlukla
  // /url?q=..., /url?url=... veya benzeri yönlendirme kullanır.
  const hrefs = text.match(/href\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi) || [];
  for (const h of hrefs) {
    let raw = h.replace(/^href\s*=\s*/i,'').replace(/^['"]|['"]$/g,'');
    collectUrlCandidates(site, raw, out, seen);
    if (out.length >= 20) return;
  }

  // 2) HTML içinde çıplak/escape edilmiş marketplace URL'lerini tara.
  const directRe = new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s<>&"\\\\]+', 'ig');
  const direct = text.match(directRe) || [];
  for (const raw of direct) {
    addSearchUrl(site, raw, out, seen);
    if (out.length >= 20) return;
  }

  // 3) Google/Bing'in encode ettiği URL'ler HTML'de href dışında da bulunabilir.
  const encoded = text.match(/(?:https?%3A%2F%2F|https?\\u003A\\u002F\\u002F)[^\s"'<>]+/ig) || [];
  for (const raw of encoded) {
    collectUrlCandidates(site, raw, out, seen);
    if (out.length >= 20) return;
  }
}

function collectUrlCandidates(site, raw, out, seen) {
  let u = normalizeCandidate(raw);
  if (!u) return;

  // Önce doğrudan URL ise kabul et.
  addSearchUrl(site, u, out, seen);
  if (out.length >= 20) return;

  // Arama motoru yönlendirmelerinde gerçek URL; q/url/u/uddg/target
  // parametrelerinden birinin içinde olabilir. Birkaç kat decode ederek çöz.
  for (let depth = 0; depth < 4; depth++) {
    const next = unwrapOne(u);
    if (!next || next === u) break;
    u = next;
    addSearchUrl(site, u, out, seen);
    if (out.length >= 20) return;
  }

  // Ham metnin içinde gömülü doğrudan marketplace URL'si varsa onu çıkar.
  const cfg = SITES[site];
  const m = u.match(new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s<>&"\\\\]+', 'i'));
  if (m) addSearchUrl(site, m[0], out, seen);
}

function unwrapOne(raw) {
  let u = normalizeCandidate(raw);
  if (!u) return '';

  try {
    // Tam URL'nin query parametrelerini oku.
    if (/^https?:\/\//i.test(u)) {
      const m = u.match(/[?&](?:q|url|u|uddg|target|dest|destination)=([^&#]+)/i);
      if (m) {
        const decoded = safeDecode(m[1]);
        if (decoded && decoded !== u) return decoded;
      }
    }

    // Bazı sonuçlarda parametre adı görünmeden encode edilmiş gerçek URL bulunur.
    const enc = u.match(/https?%3A%2F%2F[^\s"'<>]+/i);
    if (enc) return safeDecode(enc[0]);
  } catch (_) {}
  return '';
}

function addSearchUrl(site, raw, out, seen) {
  const cfg = SITES[site];
  let u = normalizeCandidate(raw);
  if (!/^https?:\/\//i.test(u)) return;

  // İç içe URL varsa doğrudan marketplace kısmını ayıkla.
  const m = u.match(new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s<>&"\\\\]+', 'i'));
  if (m) u = m[0];

  try {
    const x = new URL(u);
    const host = x.hostname.toLowerCase().replace(/^www\./,'');
    if (host !== cfg.host || !cfg.re.test(x.pathname)) return;
    const clean = 'https://www.' + cfg.host + x.pathname.replace(/\/$/,'');
    if (seen[clean]) return;
    seen[clean] = true;
    out.push({url:clean});
  } catch (_) {}
}

function extractProducts(site, html) {
  const out = [];
  const seen = {};
  const normalized = normalizeSearchText(html);
  const re = /href\s*=\s*["']([^"']+)["']/gi;
  let m;
  while ((m = re.exec(normalized)) !== null) {
    collectUrlCandidates(site,m[1],out,seen);
    if (out.length >= 20) break;
  }
  if (out.length < 20) extractSearchLinks(site,normalized,out,seen);
  return out;
}

function normalizeSearchText(s) {
  return decodeEntities(String(s || ''))
    .replace(/\\u002F/gi,'/')
    .replace(/\\u003A/gi,':')
    .replace(/\\\//g,'/');
}

function normalizeCandidate(s) {
  let u = decodeEntities(String(s || '')).trim();
  u = u.replace(/^['"]|['"]$/g,'').replace(/\\u002F/gi,'/').replace(/\\u003A/gi,':').replace(/\\\//g,'/');
  for (let i = 0; i < 3; i++) {
    const d = safeDecode(u);
    if (!d || d === u) break;
    u = d;
  }
  return u.replace(/[<>]+$/g,'').trim();
}

function safeDecode(s) {
  try { return decodeURIComponent(String(s)); } catch (_) { return String(s); }
}

function decodeEntities(s) {
  return String(s || '')
    .replace(/&amp;/gi,'&').replace(/&quot;/gi,'"').replace(/&#39;/g,"'")
    .replace(/&#x27;/gi,"'").replace(/&lt;/gi,'<').replace(/&gt;/gi,'>');
}

function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
