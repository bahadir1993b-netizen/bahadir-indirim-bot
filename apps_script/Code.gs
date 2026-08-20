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

    // Marketplace GitHub/Apps Script IP'sini engelliyorsa arama motorlarından
    // yalnızca ürün URL'si keşfedilir. Fiyat kesinlikle arama motorundan alınmaz.
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
    const res = request('https://www.bing.com/search?q=' + encodeURIComponent(query) + '&count=30');
    const html = decodeEntities(res.getContentText());
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
    const xml = decodeXml(res.getContentText());
    const links = xml.match(/<link>[\s\S]*?<\/link>/gi) || [];
    for (const tag of links) {
      let raw = tag.replace(/^<link>/i,'').replace(/<\/link>$/i,'').trim();
      raw = decodeXml(raw);
      addSearchUrl(site, raw, out, seen);
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
    const res = request('https://www.google.com/search?q=' + encodeURIComponent(q) + '&num=30&hl=tr');
    const html = decodeEntities(res.getContentText());
    extractSearchLinks(site, html, out, seen);
  } catch (_) {}
  return out;
}

function extractSearchLinks(site, html, out, seen) {
  const cfg = SITES[site];
  const hrefs = html.match(/href\s*=\s*["'][^"']+["']/gi) || [];
  for (const h of hrefs) {
    const raw = h.replace(/^href\s*=\s*["']/i,'').replace(/["']$/,'');
    collectUrlCandidates(site, raw, out, seen);
    if (out.length >= 20) return;
  }

  // Arama motoru HTML'sinde doğrudan marketplace URL'si gömülü ise onu da yakala.
  const direct = html.match(new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s<>&"\\\\]+', 'ig')) || [];
  for (const raw of direct) {
    addSearchUrl(site, raw, out, seen);
    if (out.length >= 20) return;
  }
}

function collectUrlCandidates(site, raw, out, seen) {
  let u = decodeEntities(raw).replace(/\\\//g,'/');
  addSearchUrl(site,u,out,seen);

  // Bing/Google sonucu yönlendirme URL'sinin içindeki gerçek URL'yi çıkar.
  try {
    const q = u.match(/[?&](?:u|url|q)=([^&]+)/i);
    if (q) addSearchUrl(site,decodeURIComponent(q[1]),out,seen);
  } catch (_) {}
}

function addSearchUrl(site, raw, out, seen) {
  const cfg = SITES[site];
  let u = decodeEntities(String(raw || ''))
    .replace(/\\\//g,'/')
    .replace(/["'<>]+$/g,'')
    .trim();
  if (!/^https?:\/\//i.test(u)) return;

  // URL'nin içinde marketplace alan adı geçiyorsa doğrudan olan kısmı ayıkla.
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
  const re = /href\s*=\s*["']([^"']+)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const raw = decodeEntities(m[1]).replace(/\\\//g,'/');
    const urls = raw.match(new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(SITES[site].host) + '\\/[^\\s"<>]+', 'ig')) || [];
    for (const u0 of urls) addSearchUrl(site,u0,out,seen);
    if (out.length >= 20) break;
  }
  if (out.length < 20) {
    const re2 = new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(SITES[site].host) + '\\/[^\\s"<>\\\\]+', 'ig');
    const matches = html.match(re2) || [];
    for (const u0 of matches) addSearchUrl(site,u0,out,seen);
  }
  return out;
}

function decodeEntities(s) {
  return String(s || '')
    .replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'")
    .replace(/&lt;/g,'<').replace(/&gt;/g,'>');
}

function decodeXml(s) {
  return decodeEntities(String(s || '').replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g,'$1'));
}

function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
