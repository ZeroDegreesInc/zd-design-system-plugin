// /ds-sync: write markdown-side changes into the live Figma file.
// figma_write.py fills in INPUT. Each op names one variable, style or component and carries its
// whole projection from tokens.json, so running the same script twice gives the same result.
// Order: renames, creates (so new names exist for references), values, deletes.
const INPUT = __INPUT__;

const done = {}, errors = [];
const fail = (name, e) => errors.push({ name, error: String(e && e.message || e) });

let vars = await figma.variables.getLocalVariablesAsync();
const cols = await figma.variables.getLocalVariableCollectionsAsync();
const colByName = {}; cols.forEach(c => colByName[c.name] = c);
const styleLists = {
  text: await figma.getLocalTextStylesAsync(),
  effect: await figma.getLocalEffectStylesAsync(),
  grid: await figma.getLocalGridStylesAsync(),
};
const byName = () => { const o = {}; vars.forEach(v => o[v.name] = v); return o; };
let V = byName();
const S = {}; for (const [k, l] of Object.entries(styleLists)) l.forEach(s => S[s.name] = s);
const find = (op, name) => op.kind === 'var' ? V[name] : S[name];

// ── values ──
const WEIGHT_NAME = { 100: 'Thin', 200: 'ExtraLight', 300: 'Light', 400: 'Regular', 500: 'Medium', 600: 'Semibold', 700: 'Bold', 800: 'ExtraBold', 900: 'Black' };
const WEIGHT = { Thin: 100, ExtraLight: 200, 'Extra Light': 200, Light: 300, Regular: 400, Medium: 500, Semibold: 600, 'Semi Bold': 600, SemiBold: 600, Bold: 700, ExtraBold: 800, 'Extra Bold': 800, Black: 900 };
const RESOLVED = { color: 'COLOR', dimension: 'FLOAT', number: 'FLOAT', duration: 'TIMING', cubicBezier: 'EASING', fontFamily: 'STRING', fontWeight: 'STRING', string: 'STRING', boolean: 'BOOLEAN' };
function rgba(s) {
  const [h, a] = s.split('/');
  return { r: parseInt(h.slice(1, 3), 16) / 255, g: parseInt(h.slice(3, 5), 16) / 255, b: parseInt(h.slice(5, 7), 16) / 255, a: a === undefined ? 1 : +a };
}
function ref(s) {
  // '@name' → { v }, '@name/0.12' → { v, opacity }
  const full = s.slice(1);
  if (V[full]) return { v: V[full] };
  const i = full.lastIndexOf('/');
  if (i > 0 && V[full.slice(0, i)] && /^[0-9.]+$/.test(full.slice(i + 1))) return { v: V[full.slice(0, i)], opacity: +full.slice(i + 1) };
  throw new Error('no variable named ' + full);
}
function toValue(t, x, current) {
  if (typeof x === 'string' && x.startsWith('@')) {
    const r = ref(x);
    const alias = figma.variables.createVariableAlias(r.v);
    return r.opacity === undefined ? alias : { color: alias, opacity: Math.round(r.opacity * 10000) / 100 };
  }
  if (t === 'color') return rgba(x);
  if (t === 'duration') return x / 1000;
  if (t === 'cubicBezier') return { type: 'CUSTOM_CUBIC_BEZIER', easingFunctionCubicBezier: { x1: x[0], y1: x[1], x2: x[2], y2: x[3] } };
  if (t === 'fontWeight') return typeof current === 'string' && WEIGHT[current] === x ? current : (WEIGHT_NAME[x] || String(x));
  return x;
}
function resolve(x, modeName) {
  // the plain value of '@name' (default mode, or the named mode when the variable has it)
  let v = x;
  for (let i = 0; i < 10 && typeof v === 'string' && v.startsWith('@'); i++) {
    const r = ref(v); const col = cols.find(c => c.id === r.v.variableCollectionId);
    const mode = (col.modes.find(m => m.name === modeName) || col.modes.find(m => m.modeId === col.defaultModeId)).modeId;
    const raw = r.v.valuesByMode[mode];
    v = raw && raw.type === 'VARIABLE_ALIAS' ? '@' + vars.find(z => z.id === raw.id).name : raw;
  }
  return v;
}

function setVar(op, v) {
  const p = op.proj, col = colByName[op.collection] || cols.find(c => c.id === v.variableCollectionId);
  const def = col.modes.find(m => m.modeId === col.defaultModeId);
  v.setValueForMode(def.modeId, toValue(p.type, p.value, v.valuesByMode[def.modeId]));
  for (const [mname, mv] of Object.entries(p.modes || {})) {
    const m = col.modes.find(z => z.name === mname);
    if (!m) throw new Error('collection ' + col.name + ' has no mode ' + mname);
    v.setValueForMode(m.modeId, toValue(p.type, mv, v.valuesByMode[m.modeId]));
  }
  if (p.type !== 'duration' && p.type !== 'cubicBezier') v.scopes = p.scopes.length ? p.scopes : [];
  v.description = p.description || '';
  v.hiddenFromPublishing = !!p.hidden;
}

async function setText(op, s) {
  const p = op.proj;
  const family = resolve(p.fontFamily), weight = resolve(p.fontWeight);
  const want = typeof weight === 'number' ? weight : WEIGHT[weight];
  const fonts = (await figma.listAvailableFontsAsync()).filter(f => f.fontName.family === family);
  const font = fonts.find(f => f.fontName.style === weight) || fonts.find(f => WEIGHT[f.fontName.style] === want && !/italic/i.test(f.fontName.style));
  if (!font) throw new Error('font ' + family + ' ' + weight + ' isn\'t available');
  await figma.loadFontAsync(font.fontName);
  s.fontName = font.fontName;
  const bind = (field, x, set) => {
    if (typeof x === 'string' && x.startsWith('@')) s.setBoundVariable(field, ref(x).v);
    else { if (s.boundVariables && s.boundVariables[field]) s.setBoundVariable(field, null); if (x !== null && x !== undefined) set(x); }
  };
  bind('fontFamily', p.fontFamily, () => {});
  bind('fontStyle', p.fontWeight, () => {});
  bind('fontSize', p.fontSize, x => s.fontSize = x);
  bind('lineHeight', p.lineHeight, x => s.lineHeight = x === 'auto' ? { unit: 'AUTO' } : String(x).endsWith('%') ? { unit: 'PERCENT', value: parseFloat(x) } : { unit: 'PIXELS', value: x });
  bind('paragraphSpacing', p.paragraphSpacing, x => s.paragraphSpacing = x);
  const ls = String(p.letterSpacing);
  s.letterSpacing = ls.endsWith('%') ? { unit: 'PERCENT', value: parseFloat(ls) } : { unit: 'PIXELS', value: parseFloat(ls) };
  s.textDecoration = p.textDecoration.toUpperCase();
  s.textCase = p.textCase.toUpperCase();
  s.description = p.description || '';
}

function setEffect(op, s) {
  const p = op.proj, old = s.effects;
  s.effects = p.layers.map((l, i) => {
    const prev = old[i] && (old[i].type === 'DROP_SHADOW' || old[i].type === 'INNER_SHADOW') ? old[i] : null;
    const isRef = typeof l.color === 'string' && l.color.startsWith('@');
    let e = { type: prev ? prev.type : 'DROP_SHADOW', visible: true, blendMode: 'NORMAL', showShadowBehindNode: prev ? !!prev.showShadowBehindNode : false,
      color: isRef ? { r: 0, g: 0, b: 0, a: 0.1 } : rgba(l.color), offset: { x: l.x, y: l.y }, radius: l.blur, spread: l.spread };
    if (e.type === 'INNER_SHADOW') delete e.showShadowBehindNode;
    if (isRef) e = figma.variables.setBoundVariableForEffect(e, 'color', ref(l.color).v);
    return e;
  });
  s.description = p.description || '';
}

function setGrid(op, s) {
  const p = op.proj, prev = s.layoutGrids[0] || {};
  const center = p.alignment === 'center';
  let g = { pattern: 'COLUMNS', visible: prev.visible !== false, color: prev.color || { r: 1, g: 0, b: 0, a: 0.1 }, alignment: p.alignment.toUpperCase(), count: p.columns, gutterSize: 0 };
  if (center) g.sectionSize = p.columnWidth; else g.offset = 0;
  if (typeof p.gutter === 'string') g = figma.variables.setBoundVariableForLayoutGrid(g, 'gutterSize', ref(p.gutter).v); else g.gutterSize = p.gutter;
  if (!center) { if (typeof p.margin === 'string') g = figma.variables.setBoundVariableForLayoutGrid(g, 'offset', ref(p.margin).v); else g.offset = p.margin; }
  s.layoutGrids = [g].concat(s.layoutGrids.slice(1));
  s.description = p.description || '';
}

// fonts a change needs, checked before anything is written
if (INPUT.require_fonts && INPUT.require_fonts.length) {
  const have = await figma.listAvailableFontsAsync();
  const missing = INPUT.require_fonts.filter(r => !have.some(f => f.fontName.family === r.family && WEIGHT[f.fontName.style] === r.weight));
  if (missing.length) return { ds_sync_figma_write: { nonce: INPUT.nonce, done: {}, errors: missing.map(m => ({ name: 'font', error: m.family + ' ' + m.weight + ' isn\'t available in Figma. Nothing was written.' })) } };
}

// changing a font family or weight variable re-renders every text layer bound to it, and Figma
// refuses unless those fonts are loaded: load every style of each family involved, old and new
{
  const fam = new Set((INPUT.require_fonts || []).map(r => r.family));
  for (const op of INPUT.ops) {
    if (!op.proj || op.kind !== 'var' || (op.proj.type !== 'fontFamily' && op.proj.type !== 'fontWeight')) continue;
    if (op.proj.type === 'fontFamily') [op.proj.value, ...Object.values(op.proj.modes || {})].forEach(x => typeof x === 'string' && !x.startsWith('@') && fam.add(x));
    const v = V[op.name];
    if (v) Object.values(v.valuesByMode).forEach(x => typeof x === 'string' && op.proj.type === 'fontFamily' && fam.add(x));
    if (op.proj.type === 'fontWeight') vars.filter(z => z.resolvedType === 'STRING' && z.scopes.includes('FONT_FAMILY')).forEach(z => Object.values(z.valuesByMode).forEach(x => typeof x === 'string' && fam.add(x)));
  }
  if (fam.size) {
    const all = await figma.listAvailableFontsAsync();
    for (const f of all) if (fam.has(f.fontName.family)) { try { await figma.loadFontAsync(f.fontName); } catch (e) { /* a style that won't load can't be in use */ } }
  }
}

const ops = INPUT.ops;
// 1. renames
for (const op of ops.filter(o => o.op === 'rename')) {
  try {
    const x = find(op, op.from);
    if (!x) throw new Error('nothing named ' + op.from);
    x.name = op.name;
    if (op.kind === 'var') V = byName(); else { delete S[op.from]; S[op.name] = x; }
    done[op.name] = x.id;
  } catch (e) { fail(op.name, e); }
}
// 2. creates
for (const op of ops.filter(o => o.op === 'create')) {
  try {
    if (find(op, op.name)) continue;            // already there (a re-run)
    let x;
    if (op.kind === 'var') {
      const col = colByName[op.collection];
      if (!col) throw new Error('no collection named ' + op.collection);
      x = figma.variables.createVariable(op.name, col, RESOLVED[op.proj.type]);
      vars.push(x); V = byName();
    } else {
      x = op.kind === 'text' ? figma.createTextStyle() : op.kind === 'effect' ? figma.createEffectStyle() : figma.createGridStyle();
      x.name = op.name; S[op.name] = x;
      // file it after the last style in its folder, not at the end of the list
      // (Figma applies the move, though a read in the same run still lists the old order)
      const folder = n => n.includes('/') ? n.slice(0, n.lastIndexOf('/')) : '';
      const peers = styleLists[op.kind].filter(s => s !== x && folder(s.name) === folder(op.name));
      const move = { text: 'moveLocalTextStyleAfter', effect: 'moveLocalEffectStyleAfter', grid: 'moveLocalGridStyleAfter' }[op.kind];
      if (peers.length) { try { figma[move](x, peers[peers.length - 1]); } catch (e) { /* order is cosmetic */ } }
    }
  } catch (e) { fail(op.name, e); }
}
// 3. values, for creates, updates and renames that also changed
for (const op of ops.filter(o => o.proj && o.op !== 'delete')) {
  if (errors.some(e => e.name === op.name)) continue;
  try {
    const x = find(op, op.name);
    if (!x) throw new Error('nothing named ' + op.name);
    if (op.kind === 'var') setVar(op, x);
    else if (op.kind === 'text') await setText(op, x);
    else if (op.kind === 'effect') setEffect(op, x);
    else setGrid(op, x);
    done[op.name] = x.id;
  } catch (e) { fail(op.name, e); }
}
// 4. component descriptions
for (const op of ops.filter(o => o.op === 'description')) {
  try {
    const n = await figma.getNodeByIdAsync(op.node);
    if (!n || (n.type !== 'COMPONENT_SET' && n.type !== 'COMPONENT')) throw new Error('node ' + op.node + ' isn\'t a component');
    n.description = op.description;
    done['component:' + op.name] = n.id;
  } catch (e) { fail('component:' + op.name, e); }
}
// 5. deletes
for (const op of ops.filter(o => o.op === 'delete')) {
  try {
    const x = find(op, op.name);
    if (x) x.remove();
    done[op.name] = null;
  } catch (e) { fail(op.name, e); }
}

return { ds_sync_figma_write: { nonce: INPUT.nonce, done, errors } };
