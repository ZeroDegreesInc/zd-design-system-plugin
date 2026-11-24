// /brand-intake: list the site's pages by the kind of template they probably use, to choose what to scan.
// Run it as-is on the home page. It reads links only and changes nothing.
const KINDS = [
  ['form', /\b(apply|request|inquir\w*|contact|register|rsvp|sign-?up|donate|give)\b|\bform\b/i],
  ['news or story', /\b(news|stor(y|ies)|articles?|blog|press)\b/i],
  ['event', /\b(events?|calendar)\b/i],
  ['program or product', /\b(programs?|degrees?|majors?|minors?|courses?|products?)\b/i],
  ['people', /\b(faculty|staff|directory|team|people|profiles?)\b/i],
  ['search or results', /\b(search|results)\b/i],
];
// a path's folders below the site's own prefix: a first folder nearly every link shares (such as /nextgen/),
// locale folders (/us/, /en/, /global/) and a trailing .html don't count. Depth is how many are left.
const segs = p => p.replace(/\.html?$/, '').split('/').filter(Boolean);
const LOCALE = /^([a-z]{2}([-_][a-z]{2})?|global|intl|international)$/i;
function sitePath(paths) {
  const lead = {};
  for (const p of paths) { const s = segs(p)[0]; if (s) lead[s] = (lead[s] || 0) + 1; }
  const common = new Set(Object.entries(lead).filter(([, n]) => n >= paths.length * 0.8).map(([s]) => s));
  return p => { const s = segs(p); return s.filter((x, i) => i === s.length - 1 || !((i === 0 && common.has(x)) || LOCALE.test(x))); };
}
const rel = sitePath([...document.querySelectorAll('a[href]')].map(a => { try { const u = new URL(a.href); return u.origin === location.origin ? u.pathname : null; } catch (e) { return null; } }).filter(Boolean));
const seen = new Map();
for (const a of document.querySelectorAll('a[href]')) {
  let u; try { u = new URL(a.href); } catch (e) { continue; }
  if (u.origin !== location.origin || u.hash || u.pathname.length < 2 || /\.(pdf|jpe?g|png|docx?|xlsx?|zip)$/i.test(u.pathname) || u.pathname === location.pathname || seen.has(u.pathname)) continue;
  // icon links can carry inline <style> text; use the accessible name instead
  const inner = (a.innerText || '').trim();
  const text = (inner && !/[{}]/.test(inner) ? inner : a.getAttribute('aria-label') || a.title || '').trim().replace(/\s+/g, ' ').slice(0, 40);
  const depth = rel(u.pathname).length;
  const kind = (KINDS.find(([, re]) => re.test(u.pathname.replace(/[-_/]/g, ' ') + ' ' + text)) || [])[0] || (depth === 1 ? 'section landing' : 'content');
  seen.set(u.pathname, { path: u.pathname, text, kind, depth, where: a.closest('header,nav') ? 'nav' : a.closest('footer') ? 'footer' : 'page' });
}
// other sites this one links to repeatedly, such as a separate news site: scan them as their own intake
const hosts = {};
for (const a of document.querySelectorAll('a[href]')) {
  let h; try { h = new URL(a.href).host; } catch (e) { continue; }
  if (h && h !== location.host && !/facebook|instagram|twitter|x\.com|linkedin|youtube|tiktok|google|apple|spotify|flickr|pinterest/i.test(h)) hosts[h] = (hosts[h] || 0) + 1;
}
const offsite = Object.entries(hosts).filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).map(([h, n]) => `${h} (${n} links)`);
const by = {};
for (const l of seen.values()) (by[l.kind] = by[l.kind] || []).push(`${l.path}  "${l.text}" (${l.where}, depth ${l.depth})`);
Object.entries(by).map(([k, v]) => `## ${k} (${v.length})\n` + v.slice(0, 12).join('\n')).join('\n')
  + (offsite.length ? `\n## other sites linked more than once (can't be scanned from here)\n${offsite.join(', ')}` : '');
