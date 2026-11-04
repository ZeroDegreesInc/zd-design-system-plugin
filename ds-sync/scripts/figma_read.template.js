// /ds-sync: read the live Figma file. It changes nothing in Figma.
// figma_read.py fills in INPUT. Two modes:
//   "fingerprints": a fingerprint for every token, style and component part, plus icons,
//                   new components and missing nodes. Python compares them with the snapshot.
//   "details":      the full projection of the tokens and component parts named in INPUT.want.
// canon() and fnv() must match synclib.py exactly.
const INPUT = __INPUT__;

figma.skipInvisibleInstanceChildren = false;

// ── canonical JSON and fingerprints (same as synclib.py) ──
const r4 = x => { const r = Math.round(x * 10000) / 10000; return Object.is(r, -0) ? 0 : r; };
function norm(x) {
  if (x === null || typeof x === 'boolean' || typeof x === 'string') return x;
  if (typeof x === 'number') return r4(x);
  if (Array.isArray(x)) return x.map(norm);
  if (typeof x === 'object') { const o = {}; for (const k of Object.keys(x).sort()) o[k] = x[k] === undefined ? null : norm(x[k]); return o; }
  return x;
}
const canon = x => JSON.stringify(norm(x));
function fnv(s) { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; } return h.toString(16).padStart(8, '0'); }
const fp = x => fnv(canon(x));
const globToRe = g => new RegExp('^' + g.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') + '$');
const EXCLUDE = (INPUT.exclude || []).map(globToRe);
const excluded = n => EXCLUDE.some(re => re.test(n));

// ── tokens and styles → projections ──
const cols = await figma.variables.getLocalVariableCollectionsAsync();
const vars = await figma.variables.getLocalVariablesAsync();
const byId = {}; vars.forEach(v => byId[v.id] = v);
const vname = id => byId[id] ? byId[id].name : id;
const hex = c => '#' + [c.r, c.g, c.b].map(x => Math.round(x * 255).toString(16).padStart(2, '0')).join('');
const colorStr = c => { const a = c.a === undefined ? 1 : Math.round(c.a * 100) / 100; return a >= 1 ? hex(c) : hex(c) + '/' + r4(a); };
const WEIGHT = { Thin: 100, ExtraLight: 200, 'Extra Light': 200, Light: 300, Regular: 400, Medium: 500, Semibold: 600, 'Semi Bold': 600, SemiBold: 600, Bold: 700, ExtraBold: 800, 'Extra Bold': 800, Black: 900 };
function typeOf(v) {
  if (v.resolvedType === 'COLOR') return 'color';
  if (v.resolvedType === 'TIMING') return 'duration';
  if (v.resolvedType === 'EASING') return 'cubicBezier';
  if (v.resolvedType === 'BOOLEAN') return 'boolean';
  if (v.resolvedType === 'STRING') return v.scopes.includes('FONT_FAMILY') ? 'fontFamily' : v.scopes.includes('FONT_STYLE') ? 'fontWeight' : 'string';
  return v.scopes.length === 0 ? 'number' : 'dimension';
}
function value(t, raw) {
  if (raw && raw.type === 'VARIABLE_ALIAS') return '@' + vname(raw.id);
  if (t === 'color') {
    if (raw && raw.color) return (raw.color.type === 'VARIABLE_ALIAS' ? '@' + vname(raw.color.id) : hex(raw.color)) + '/' + r4(raw.opacity / 100);
    return colorStr(raw);
  }
  if (t === 'duration') return Math.round(raw * 1000);
  if (t === 'cubicBezier') { const b = raw.easingFunctionCubicBezier; return [b.x1, b.y1, b.x2, b.y2]; }
  if (t === 'fontWeight') return WEIGHT[raw] || raw;
  return raw;
}
const proj = {}, ids = {}, where = {};
for (const c of cols) {
  const others = c.modes.filter(m => m.modeId !== c.defaultModeId);
  for (const id of c.variableIds) {
    const v = byId[id]; if (!v || excluded(v.name)) continue;
    const t = typeOf(v);
    const modes = {}; if (others.length) for (const m of others) modes[m.name] = value(t, v.valuesByMode[m.modeId]);
    proj[v.name] = { kind: 'var', type: t, description: v.description || '', value: value(t, v.valuesByMode[c.defaultModeId]), modes, scopes: [...v.scopes].sort(), hidden: !!v.hiddenFromPublishing };
    ids[v.name] = v.id; where[v.name] = c.name;
  }
}
const bref = (b, k, raw) => b && b[k] ? '@' + vname(b[k].id) : raw;
for (const s of await figma.getLocalTextStylesAsync()) {
  if (excluded(s.name)) continue;
  const b = s.boundVariables || {};
  const lh = s.lineHeight.unit === 'PIXELS' ? s.lineHeight.value : s.lineHeight.unit === 'PERCENT' ? s.lineHeight.value + '%' : 'auto';
  where[s.name] = 'Text styles';
  proj[s.name] = { kind: 'text', description: s.description || '', fontFamily: bref(b, 'fontFamily', s.fontName.family), fontWeight: bref(b, 'fontStyle', s.fontName.style), fontSize: bref(b, 'fontSize', s.fontSize), lineHeight: bref(b, 'lineHeight', lh), letterSpacing: r4(s.letterSpacing.value) + (s.letterSpacing.unit === 'PERCENT' ? '%' : 'px'), paragraphSpacing: bref(b, 'paragraphSpacing', s.paragraphSpacing), textDecoration: s.textDecoration.toLowerCase(), textCase: s.textCase.toLowerCase() };
  ids[s.name] = s.id;
}
for (const s of await figma.getLocalEffectStylesAsync()) {
  if (excluded(s.name)) continue;
  where[s.name] = 'Effect styles';
  proj[s.name] = { kind: 'effect', description: s.description || '', layers: s.effects.filter(e => e.type === 'DROP_SHADOW' || e.type === 'INNER_SHADOW').map(e => ({ color: e.boundVariables && e.boundVariables.color ? '@' + vname(e.boundVariables.color.id) : colorStr(e.color), x: e.offset.x, y: e.offset.y, blur: e.radius, spread: e.spread || 0 })) };
  ids[s.name] = s.id;
}
for (const s of await figma.getLocalGridStylesAsync()) {
  if (excluded(s.name)) continue;
  const g = s.layoutGrids[0] || {}; const b = g.boundVariables || {};
  const center = g.alignment === 'CENTER';
  where[s.name] = 'Grid styles';
  proj[s.name] = { kind: 'grid', description: s.description || '', columns: g.count, columnWidth: center ? g.sectionSize : null, gutter: bref(b, 'gutterSize', g.gutterSize), margin: center ? null : bref(b, 'offset', g.offset), alignment: (g.alignment || '').toLowerCase() };
  ids[s.name] = s.id;
}
const DETAILS = INPUT.mode === 'details';

// ── components (same walk that built the snapshot) ──
const N = {}; vars.forEach(v => N[v.id] = v.name);
const TS = {}; (await figma.getLocalTextStylesAsync()).forEach(s => TS[s.id] = s.name);
const ES = {}; (await figma.getLocalEffectStylesAsync()).forEach(s => ES[s.id] = s.name);
const FLOAT = { itemSpacing: 'gap', paddingTop: 'padding-top', paddingRight: 'padding-right', paddingBottom: 'padding-bottom', paddingLeft: 'padding-left', topLeftRadius: 'radius', strokeTopWeight: 'stroke-width', strokeLeftWeight: 'stroke-left-width', strokeWeight: 'stroke-width', width: 'width', height: 'height' };
const pv = arr => (arr || []).filter(p => p.visible !== false).map(p => p.boundVariables && p.boundVariables.color ? N[p.boundVariables.color.id] : (p.type === 'SOLID' ? 'raw' : p.type.toLowerCase())).join('+');
const isIcon = n => n.type === 'INSTANCE' && n.children.length === 1 && n.children[0].name === 'Glyph';
function walk(n, path, out) {
  const b = n.boundVariables || {};
  for (const [k, alias] of Object.entries(FLOAT)) if (b[k]) { const id = Array.isArray(b[k]) ? b[k][0].id : b[k].id; out[path + '.' + alias] = N[id]; }
  if (n.type !== 'TEXT') { const f = pv(n.fills); if (f) out[path + '.fill'] = f; }
  if (n.strokes && n.strokes.length) out[path + '.stroke'] = pv(n.strokes);
  if (n.effectStyleId) out[path + '.effect'] = ES[n.effectStyleId];
  if (n.type === 'TEXT') { out[path + '.text-style'] = TS[n.textStyleId] || 'raw'; out[path + '.color'] = pv(n.fills); }
  if (n.visible === false) out[path + '.hidden'] = true;
}
async function scan(node, path, out) {
  for (const c of node.children || []) {
    const p = path ? path + '/' + c.name : c.name;
    if (isIcon(c)) { const mc = await c.getMainComponentAsync(); const g = c.children[0]; out[p + '.icon'] = mc && mc.name; const w = c.boundVariables && c.boundVariables.width; if (w) out[p + '.size'] = N[w.id]; out[p + '.color'] = pv(g.strokes); if (g.boundVariables && g.boundVariables.strokeWeight) out[p + '.stroke-width'] = N[g.boundVariables.strokeWeight.id]; if (c.visible === false) out[p + '.hidden'] = true; continue; }
    walk(c, p, out);
    if (c.type === 'INSTANCE') {
      const mc = await c.getMainComponentAsync(); const setName = mc && (mc.parent.type === 'COMPONENT_SET' ? mc.parent.name : mc.name);
      if (setName && (!setName.startsWith('.') || setName === '.MenuItem')) { out[p + '.component'] = setName + ' ' + JSON.stringify(Object.fromEntries(Object.entries(c.componentProperties).filter(([k, x]) => x.type === 'VARIANT').map(([k, x]) => [k, x.value]))); continue; }
    }
    await scan(c, p, out);
  }
}
async function entryOf(s) {
  const props = {};
  for (const [k, d] of Object.entries(s.componentPropertyDefinitions)) props[k.split('#')[0]] = d.type === 'VARIANT' ? d.variantOptions : { type: d.type.toLowerCase(), default: d.type === 'INSTANCE_SWAP' ? ((await figma.getNodeByIdAsync(d.defaultValue)) || {}).name : d.defaultValue };
  const parts = { description: s.description || '', properties: props, default_variant: s.type === 'COMPONENT_SET' ? s.defaultVariant.name : null };
  for (const v of (s.type === 'COMPONENT_SET' ? s.children : [s])) {
    const out = {}; walk(v, '.', out); await scan(v, '', out);
    const b = {}; for (const [k, x] of Object.entries(out)) b[k.startsWith('..') ? 'self' + k.slice(1) : k] = x;
    parts['variant:' + v.name] = b;
  }
  return parts;
}
const comps = {};
for (const [name, node] of Object.entries(INPUT.components)) {
  if (DETAILS && !(INPUT.want.components || {})[name]) continue;
  const s = await figma.getNodeByIdAsync(node);
  if (!s || (s.type !== 'COMPONENT_SET' && s.type !== 'COMPONENT')) { comps[name] = null; continue; }
  const parts = await entryOf(s);
  if (DETAILS) {
    const out = {}; for (const k of INPUT.want.components[name]) out[k] = k in parts ? parts[k] : null;
    if (s.name !== name) out.name = s.name;
    comps[name] = out;
  } else {
    const hashes = {}; for (const [k, x] of Object.entries(parts)) hashes[k] = fp(x);
    comps[name] = { name: s.name, parts: hashes };
  }
}
if (DETAILS) {
  const tok = {}; for (const n of INPUT.want.tokens || []) tok[n] = proj[n] ? { proj: proj[n], id: ids[n], collection: where[n] } : null;
  return JSON.stringify({ ds_sync_figma_details: { nonce: INPUT.nonce, tokens: tok, components: comps } });
}
const known = new Set(Object.values(INPUT.components));
const added = [];
for (const pid of INPUT.component_pages || []) {
  const page = await figma.getNodeByIdAsync(pid); if (!page) continue; await page.loadAsync();
  for (const s of page.findAllWithCriteria({ types: ['COMPONENT_SET', 'COMPONENT'] })) {
    if (s.type === 'COMPONENT' && s.parent.type === 'COMPONENT_SET') continue;
    if (!known.has(s.id) && !excluded(s.name)) added.push({ name: s.name, node: s.id, page: page.name.trim(), variants: s.type === 'COMPONENT_SET' ? s.children.length : 1 });
  }
}

// ── icons ──
const icons = {};
if (INPUT.icon_page) {
  const page = await figma.getNodeByIdAsync(INPUT.icon_page); await page.loadAsync();
  page.findAllWithCriteria({ types: ['COMPONENT'] }).forEach(c => icons[c.name] = c.id);
}

// ── the Introduction page: each frame holding a "heading" and a "body" text is one section of design.md ──
// Figma's soft line break (U+2028) becomes a newline. Written with fromCharCode, not escapes, so pasting the script can't break it.
let intro = null;
if (INPUT.intro_page) {
  const page = await figma.getNodeByIdAsync(INPUT.intro_page);
  if (page) {
    await page.loadAsync();
    const txt = (f, name) => { const t = f.children.find(c => c.type === 'TEXT' && c.name === name); return t ? t.characters.split(String.fromCharCode(8232)).join(String.fromCharCode(10)) : null; };
    intro = { page: page.name.trim(), sections: page.findAll(n => n.type === 'FRAME' && txt(n, 'heading') !== null && txt(n, 'body') !== null)
      .map(f => ({ key: f.name, heading: txt(f, 'heading'), body: txt(f, 'body') })) };
  }
}

// ── nodes named in markdown ──
const missing_nodes = [];
for (const id of INPUT.nodes || []) if (!(await figma.getNodeByIdAsync(id))) missing_nodes.push(id);

const tokens = {}; for (const [n, p] of Object.entries(proj)) tokens[n] = [fp(p), ids[n]];
return JSON.stringify({ ds_sync_figma_fingerprints: { nonce: INPUT.nonce, file_key: figma.fileKey || null, read_at: new Date().toISOString(), tokens, components: comps, added, icons, intro, missing_nodes } });
