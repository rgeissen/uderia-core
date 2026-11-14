import { nodesLabel, relationshipsLabel } from '../kgVocabulary.js?v=1.0';   // the graph's counting vocabulary
/**
 * KG Execution Theatre — the knowledge graph as the STAGE of a turn.
 *
 * One question, told as an ER story in seven beats while the agent works:
 *   understand → seed → expand → inject → execute → verify → receipt
 *
 * Design contract (see docs/Architecture/KG_INSPECTOR_ARCHITECTURE.md, "Execution theatre"):
 *   • ONE reducer feeds live and reload. Live events (kg_stage notices, kg_enrichment,
 *     tool_intent/tool_result, conversation_tool_*, grounding_gate, honesty) and a persisted
 *     turn (eventsFromTurn) produce the same ops, so the replay is the same story.
 *   • Narration leads, the graph follows. Every op is applied to the model the instant it
 *     arrives; the stage animates from that model on its own clock and never blocks an event.
 *   • Onion: WATCH (the consumer default) shows one lit path, one caption, one animating mark.
 *     INSPECT opens the doors: attribute types, the physical plane, the trail with timings.
 *   • Relevance decides the skin: worst first (prevented > out of scope > flagged > applied >
 *     skipped > quiet); while a beat runs, the current stage sentence.
 *   • No two rendered labels overlap: every text goes through the label layer, which keeps an
 *     occupancy list, tries alternative anchors, and folds a label into the hover otherwise.
 *   • Zero tokens, zero awaits: everything here is derived from data the platform already
 *     computes and ships on the wire.
 *
 * Pure exports (Node-testable, no DOM): sqlRoles · buildModel · createReducer · placeLabels ·
 * eventsFromTurn · skinSentence. DOM export: mountTheatre.
 */

export const THEATRE_VERSION = '1.25';
export const BEATS = ['understand', 'seed', 'expand', 'inject', 'execute', 'verify', 'receipt'];
export const BEAT_LABEL = {
    understand: 'Understand', seed: 'Seed', expand: 'Expand', inject: 'Inject',
    execute: 'Execute', verify: 'Verify', receipt: 'Receipt',
};
// What each beat DOES — one sentence, the same on every turn. What it DID on this turn is
// read from the state (explainBeat); what it is doing NOW is read from the stage (beatView).
export const BEAT_EXPLAIN = {
    understand: 'Reads the question and picks out its terms — the words the graph will be searched by.',
    seed: 'Finds the entities and attributes those terms reach in the graph, by name and by meaning. These seeds are where the context starts.',
    expand: 'Follows the declared relationships and correspondences outward from the seeds, so the agent sees the tables it will need to join.',
    inject: 'Writes the selected part of the graph — entities, keys, relationships, value domains — into the agent\u2019s context. Attributes over budget are folded away.',
    execute: 'The agent writes and runs its data statements; each one is checked against the model before the database sees it, and every fact it applied or ignored is recorded.',
    verify: 'The grounding gate checks the answer against the trusted scope: the databases the agent may answer from.',
    receipt: 'The turn\u2019s verdict: what was applied, what was stopped, whether the seeds were used and the scope held.',
};
// The stage's marks, named. Every visual state a viewer can meet has a row here; the DOM
// side draws the swatch from the same tokens the stage draws with, so the key cannot lie.
export const LEGEND = [
    { group: 'Trail', items: [
        { sw: 'dot', v: 'pending', label: 'waiting — the turn has not reached this beat' },
        { sw: 'dot', v: 'queued', label: 'up next — known, not yet performed on stage' },
        { sw: 'dot', v: 'active', label: 'on stage now' },
        { sw: 'dot', v: 'done', label: 'performed' },
        { sw: 'dot', v: 'danger', label: 'a statement was stopped, or the answer withheld' },
        { sw: 'bar', v: 'held', label: 'held here — click the beat again to continue; beats after it read ahead (dimmed)' },
        { sw: 'time', v: '1.2 s', label: 'seconds after the turn began — recorded only while it ran live' },
    ] },
    { group: 'Entities', items: [
        { sw: 'box', v: 'dim', label: 'in the graph, not reached by this turn' },
        { sw: 'box', v: 'known', label: 'reached through the chain from the seeds' },
        { sw: 'box', v: 'lit', label: 'a seed the question reached, or touched by the statement' },
        { sw: 'box', v: 'solid', label: 'injected into the agent\u2019s context' },
        { sw: 'box', v: 'background', label: 'in the context, but not used by the statement (from Execute on)' },
        { sw: 'box', v: 'cooling', label: 'a seed the agent never used' },
        { sw: 'ring', v: 'grain', label: 'the grain of the answer — the entity the measure counts' },
        { sw: 'text', v: '\u25b8 N attributes folded', label: 'attributes left out of the context to fit the budget' },
        { sw: 'text', v: 'IMPLEMENTS', label: 'the deployed table behind the entity (Inspect)' },
    ] },
    { group: 'Lines', items: [
        { sw: 'thread', v: 'lexical', label: 'a question word \u2192 a seed it reached (one thread for every seed the word reached)' },
        { sw: 'thread', v: 'semantic', label: 'reached by meaning (no word names it)' },
        { sw: 'rel', v: 'known', label: 'a declared relationship, not used' },
        { sw: 'rel', v: 'lit', label: 'joined on it in the statement' },
        { sw: 'rel', v: 'pass', label: 'verified inside the trusted scope' },
        { sw: 'rel', v: 'danger', label: 'stopped before the database' },
        { sw: 'rel', v: 'bridge', label: 'a correspondence between subject areas \u2014 never a join' },
    ] },
    { group: 'Marks', items: [
        { sw: 'perimeter', v: 'scope', label: 'trusted scope \u2014 the databases the agent may answer from' },
        { sw: 'ghost', v: 'oos', label: 'a reference outside the trusted scope' },
        { sw: 'barrier', v: 'stop', label: 'the statement was stopped before the database' },
        { sw: 'receipt', v: 'card', label: 'the turn\u2019s verdict' },
    ] },
    { group: 'Rows in a box', items: [
        { sw: 'icon', v: 'seed', label: 'a seeded attribute \u2014 the question word that reached it (Seed)' },
        { sw: 'icon', v: 'key', label: 'part of the declared key' },
        { sw: 'icon', v: 'filter', label: 'filtered on' },
        { sw: 'icon', v: 'dimension', label: 'grouped by' },
        { sw: 'icon', v: 'measure', label: 'the measure' },
        { sw: 'icon', v: 'absent', label: 'used by the statement but not declared \u2014 not a key' },
    ] },
];

// The stage's counts, read ONCE. An entity is a box the question can be answered from; a
// correspondence node is not one, and one correspondence is one however many edges tie it
// to its two ends (session f622f5ef turn 2 read "16 entities, 2 correspondences" for 15 and 1).
export function entityCount(model) {
    return model ? Object.values(model.entities).filter(e => e.type !== 'product_bridge').length : 0;
}
export function correspondenceCount(model) {
    if (!model) return 0;
    const keys = new Set();
    for (const b of model.bridges) {
        const s = model.entities[b.source], t = model.entities[b.target];
        if (s && s.type === 'product_bridge') keys.add(s.name);
        else if (t && t.type === 'product_bridge') keys.add(t.name);
        else keys.add(b.name || `${[b.source, b.target].sort().join('|')}`);
    }
    return keys.size;
}
// How the ENTITIES came into scope — entities only, never their attributes or a domain node.
export function reachCounts(model) {
    const tags = {};
    if (model) for (const e of Object.values(model.entities)) {
        if (e.type === 'product_bridge') continue;
        const t = e.provenance || (model.provenance || {})[e.name]; if (t) tags[t] = (tags[t] || 0) + 1;
    }
    return tags;
}
function _plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + 's')}`; }
// The reading for one beat: what it does (constant), what it did (the state, only once the
// stage performed it), and what it is doing now (the view). Pure: the DOM side hands in
// the view it computed from the stage, so the sentence and the row can never disagree.
export function explainBeat(state, beat, view) {
    view = view || {};
    const S = state || {}; const m = S.model;
    const does = BEAT_EXPLAIN[beat] || '';
    let did = '';
    if (view.done || view.ahead) {
        switch (beat) {
            case 'understand': { const t = S.terms || []; did = t.length ? `Read ${_plural(t.length, 'term')} from the question: ${t.slice(0, 6).join(', ')}${t.length > 6 ? ', \u2026' : ''}.` : 'No terms were read from the question.'; break; }
            case 'seed': { const seeds = (S.seeds || []).filter(x => x.channel !== 'product'); const byM = seeds.filter(x => x.channel === 'semantic').length; const prods = (S.seeds || []).length - seeds.length;
                did = seeds.length ? `${_plural(seeds.length, 'seed')} reached: ${seeds.slice(0, 4).map(x => _leaf(x.name)).join(', ')}${seeds.length > 4 ? ', \u2026' : ''}${byM ? ` \u2014 ${byM} by meaning, the rest by the words` : ' \u2014 all by the words'}.${prods ? ` ${_plural(prods, 'data product contract')} also offered.` : ''}` : 'Nothing in the graph matched the question.'; break; }
            case 'expand': { if (m) { const n = entityCount(m); const tags = reachCounts(m);
                    const order = ['seed', 'fk_chain', 'joinable', 'bridge', 'semantic', 'database'];
                    const how = Object.entries(tags).sort((a, b) => (order.indexOf(a[0]) + 99) % 99 - (order.indexOf(b[0]) + 99) % 99).map(([t, c]) => `${c} ${({ seed: 'seeded', fk_chain: 'through declared keys', joinable: 'by joinable columns', bridge: 'over a correspondence', database: 'from the same database', semantic: 'by meaning' })[t] || t}`).join(', ');
                    did = `${_plural(m.rels.length, 'relationship')} and ${_plural(correspondenceCount(m), 'correspondence')} followed; ${nodesLabel(n)} in scope${how ? ` (${how})` : ''}.`; } break; }
            case 'inject': { if (m) { const n = entityCount(m); const folded = Object.values(m.omitted || {}).reduce((a, b) => a + (Number(b) || 0), 0);
                    did = m.surfaceOnly ? `${_plural(m.products.length, 'data product contract')} injected; the schemas were withheld (surface-only agent).` : `${nodesLabel(n)}, ${_plural(m.rels.length, 'relationship')}${correspondenceCount(m) ? ` and ${_plural(correspondenceCount(m), 'correspondence')}` : ''} written into the context${folded ? `; ${_plural(folded, 'attribute')} folded away` : ''}.`; } break; }
            case 'execute': { const sts = S.statements || []; const applied = (S.consequences || []).filter(c => c.kind === 'applied'); const prevented = (S.consequences || []).filter(c => c.kind === 'prevented');
                did = sts.length ? `${_plural(sts.length, 'statement')} ran; ${_plural(applied.length, 'fact')} from the model applied${applied.length ? ` (${[...new Set(applied.map(a => a.fact === 'rewrite' ? 'statement rewritten' : a.fact.replace(/_/g, ' ')))].join(', ')})` : ''}${prevented.length ? `; ${_plural(prevented.length, 'statement')} stopped \u2014 ${prevented[prevented.length - 1].sentence}` : ''}` : 'No data statement ran.'; break; }
            case 'verify': { const v = S.verdict; did = v ? (v.decision === 'block' ? `The answer reached beyond the trusted scope (${(v.oos || []).join(', ')}) and was withheld.` : (v.oos || []).length ? `Reached outside the trusted scope: ${v.oos.join(', ')}; the answer was annotated.` : `Stayed inside the trusted scope${(v.scope || []).length ? ` (${v.scope.slice(0, 3).join(', ')})` : ''}.${v.decision === 'annotate' && (v.ungrounded || []).length ? ` The answer was annotated: ${_plural(v.ungrounded.length, 'figure')} could not be matched to the data (${v.ungrounded.slice(0, 6).join(', ')}).` : ''}`) + (S.honesty && S.honesty.acted ? ` ${S.honesty.sentence}` : '') + (v.judged ? ' A judge was consulted.' : '') : ''; break; }
            case 'receipt': { const r = S.receipt; did = r ? `${r.sentence}${r.seeds ? ` ${r.seedsUsed} of ${_plural(r.seeds, 'seed')} used.` : ''}${r.facts && r.facts.length ? ` Facts applied: ${r.facts.join(', ')}.` : ''}` : ''; break; }
            default: break;
        }
        if (S.synthetic && ['understand', 'seed', 'expand', 'inject'].includes(beat)) did += (did ? ' ' : '') + 'Reconstructed from the enrichment: no live stage notice was recorded for this beat.';
    }
    let status;
    if (view.preview) status = 'Previewing \u2014 click the beat to hold the story here; move away to return.';
    else if (view.heldSt != null) status = `Held on statement ${view.heldSt} \u2014 click it again to let the story continue.`;
    else if (view.held) status = 'Held here \u2014 click the beat again to let the story continue.';
    else if (view.ahead) status = 'After the hold \u2014 known, shown dimmed until the story continues.';
    else if (view.current) status = 'On stage now.';
    else if (view.queued) status = 'Up next \u2014 the stage has not performed it yet.';
    else if (view.absent) status = ({ verify: 'Did not run this turn: no grounding gate reported \u2014 its master switch is off, or this agent class does not run it.', execute: 'Did not run this turn: the agent ran no data statement.', receipt: 'No receipt yet.' })[beat] || 'Did not happen on this turn.';
    else if (view.done) status = 'Performed.';
    else status = 'Waiting \u2014 the turn has not reached this beat.';
    return { label: BEAT_LABEL[beat] || beat, does, did, status };
}
export function explainStatement(state, st, view) {
    if (!st) return null;
    const applied = (state.consequences || []).filter(c => c.kind === 'applied' && c.statement === st.id);
    const prevented = (state.consequences || []).find(c => c.kind === 'prevented' && c.statement === st.id);
    const tables = ((st.roles || {}).tables || []);
    const does = st.sql ? `Statement ${st.id}: ${st.rewritten ? `the statement Uderia ran in place of the agent\u2019s (${st.rewritten.label})` : `the agent\u2019s SQL`}${tables.length ? ` over ${tables.join(', ')}` : ''}, checked against the model before the database saw it.` : `Statement ${st.id}: a call to ${st.tool}.`;
    const did = prevented ? prevented.sentence : applied.length ? applied.map(a => a.sentence).join(' ') : st.result ? (st.result.ok ? `Ran${st.result.rows != null ? `; ${_plural(st.result.rows, 'row')} returned` : ''}.` : 'Failed at the database.') : 'Running.';
    const status = (view || {}).held ? 'Held here \u2014 click the statement again to continue.' : '';
    return { label: `Statement ${st.id}`, does, did, status };
}

// Tools that only READ the catalog — a table queried with none of these first was
// "already known" from the injected schema (the SKIPPED consequence).
const DISCOVERY_TOOLS = /^(base_databaseList|base_tableList|base_columnDescription|base_tableDDL|base_tablePreview|base_tableAffinity|base_tableUsage|dba_tableSqlList|qlty_columnSummary|TDA_DatabaseNavigator)$/i;
const FRAMEWORK_TOOLS = /^TDA_/i;

// ─────────────────────────────────────────────────────────────────────────────
// 1. Statement roles — a light clause reader over the SQL the agent wrote.
//    Deterministic, best-effort: it names what it can prove from the text and
//    stays silent otherwise (a role it cannot read is not a role it invents).
// ─────────────────────────────────────────────────────────────────────────────
const _KW = /\b(SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|QUALIFY|UNION|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|AS|AND|OR|TOP|DISTINCT|SAMPLE)\b/gi;

function _leaf(name) {
    const s = String(name || '').replace(/["`\[\]]/g, '');
    const parts = s.split('.');
    return parts[parts.length - 1].toLowerCase();
}

// Words a bare identifier in an expression can be that are NOT a column.
const _NOT_COLUMN = new Set(('as case when then else end and or not null in is like between distinct asc desc cast date '
    + 'format integer int varchar char character decimal numeric float double precision timestamp time interval year month day '
    + 'hour minute second from for top over partition by order rows range unbounded preceding following current row with zone '
    + 'true false all any some exists select where group having limit qualify union join on left right inner outer full cross '
    + 'trim leading trailing both substring substr extract coalesce count sum avg min max').split(' '));

function _splitTopLevel(text) {
    const parts = []; let depth = 0, cur = '';
    for (const ch of text) {
        if (ch === '(') depth++;
        else if (ch === ')') depth = Math.max(0, depth - 1);
        if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue; }
        cur += ch;
    }
    if (cur.trim()) parts.push(cur);
    return parts.map(x => x.trim()).filter(Boolean);
}

// `resolveBare(col, tables)` names the table an UNQUALIFIED column belongs to when the
// statement reads several (the reducer passes the graph's own attribute lists); with one
// table the answer is that table. A column it cannot place stays unplaced — never guessed.
export function sqlRoles(sql, resolveBare) {
    const out = { tables: [], aliases: {}, joins: [], filters: [], groupBy: [], measures: [], projected: [], roles: {}, ok: false };
    if (!sql || typeof sql !== 'string') return out;
    // keep literals but neutralise their content for clause detection
    const lits = [];
    let text = sql.replace(/'(?:[^']|'')*'/g, (m) => { lits.push(m); return `'\u0000${lits.length - 1}'`; });
    text = text.replace(/\s+/g, ' ').trim();
    const unlit = (s) => String(s).replace(/'\u0000(\d+)'/g, (_, i) => lits[Number(i)]);

    // tables + aliases
    // the alias is optional and is never a keyword — `FROM a.t1 JOIN a.t2` once consumed `JOIN`
    // as t1's alias, so the joined table was never read at all
    const tblRe = /\b(FROM|JOIN)\s+([A-Za-z_][\w."]*)(?:\s+(?:AS\s+)?(?!(?:ON|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|GROUP|ORDER|HAVING|LIMIT|QUALIFY|SAMPLE|SELECT|UNION)\b)([A-Za-z_]\w*))?/gi;
    let m;
    while ((m = tblRe.exec(text))) {
        const full = m[2].replace(/"/g, '');
        const leaf = _leaf(full);
        const alias = (m[3] && !/^(ON|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|GROUP|ORDER|HAVING|LIMIT|QUALIFY|SAMPLE|SELECT)$/i.test(m[3])) ? m[3].toLowerCase() : leaf;
        if (!out.tables.includes(leaf)) out.tables.push(leaf);
        out.aliases[alias] = leaf;
        out.aliases[leaf] = leaf;
    }
    const resolve = (alias, col) => ({ table: out.aliases[String(alias || '').toLowerCase()] || (alias ? String(alias).toLowerCase() : null), col: String(col || '').toLowerCase() });
    // select-list aliases: `SUBSTR(incident_date_text, 4, 3) AS month_text` — a GROUP BY on
    // `month_text` groups by incident_date_text, and the reader must say so
    const selectAlias = {};
    const _selPre = /\bSELECT\b\s*(?:TOP\s+\d+\s+)?(?:DISTINCT\s+)?([\s\S]*?)\bFROM\b/i.exec(text);
    for (const item of (_selPre ? _splitTopLevel(_selPre[1]) : [])) {
        const am = /^([\s\S]*?)\s+AS\s+([A-Za-z_]\w*)\s*$/i.exec(item);
        if (am) selectAlias[am[2].toLowerCase()] = am[1];
    }
    const bare = (col) => {
        const c = String(col).toLowerCase();
        const t = out.tables.length === 1 ? out.tables[0] : (typeof resolveBare === 'function' ? (resolveBare(c, out.tables.slice()) || null) : null);
        return { table: t, col: c };
    };
    // every column an expression names: qualified (alias.col) and bare (col), never a
    // keyword, a function name, a table alias or a select alias
    const colsIn = (expr) => {
        const found = [];
        const e = String(expr || '');
        const q = /([A-Za-z_]\w*)\.([A-Za-z_]\w*)/g; let x;
        while ((x = q.exec(e))) found.push(resolve(x[1], x[2]));
        const b = /(^|[^\w.'\u0000])([A-Za-z_]\w*)(?![\w.]|\s*\()/g;
        while ((x = b.exec(e))) {
            const w = x[2].toLowerCase();
            if (_NOT_COLUMN.has(w) || out.aliases[w] || selectAlias[w]) continue;
            if (/\.\s*$/.test(e.slice(0, x.index + x[1].length))) continue;
            found.push(bare(w));
        }
        return found;
    };

    // joins: every a.x = b.y in an ON clause
    const onRe = /\bON\b([\s\S]*?)(?=\b(?:JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|QUALIFY)\b|$)/gi;
    while ((m = onRe.exec(text))) {
        const seg = m[1];
        const eq = /([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)/g;
        let e;
        while ((e = eq.exec(seg))) {
            const a = resolve(e[1], e[2]), b = resolve(e[3], e[4]);
            if (a.table && b.table && a.table !== b.table) out.joins.push({ a, b });
        }
        // a join on constants / no equality between two tables → cross join in disguise
        if (!/=/.test(seg)) out.joins.push({ a: null, b: null, unkeyed: true });
    }
    if (/\bCROSS\s+JOIN\b/i.test(text)) out.joins.push({ a: null, b: null, unkeyed: true });

    // where
    const whereM = /\bWHERE\b([\s\S]*?)(?=\b(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|QUALIFY|UNION)\b|$)/i.exec(text);
    if (whereM) {
        // a predicate on a FUNCTION of a column: `SUBSTR(incident_date_text, 8, 4) = '2023'`
        const fnPred = /\b([A-Za-z_]\w*)\s*\(([^()]*)\)\s*(=|<>|!=|>=|<=|>|<)\s*('\u0000\d+'|-?\d+(?:\.\d+)?)/gi;
        let fp;
        while ((fp = fnPred.exec(whereM[1]))) {
            for (const r of colsIn(fp[2])) out.filters.push({ ...r, op: fp[3], literal: unlit(fp[4]), fn: fp[1].toUpperCase() });
        }
        const pred = /(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*(=|<>|!=|>=|<=|>|<|\bLIKE\b|\bIN\b|\bBETWEEN\b|\bIS\b)\s*('\u0000\d+'|-?\d+(?:\.\d+)?|\([^)]*\)|NULL|NOT\s+NULL|[A-Za-z_]\w*(?:\.\w+)?)/gi;
        let p;
        while ((p = pred.exec(whereM[1]))) {
            const r = resolve(p[1], p[2]);
            if (/^(AND|OR|NOT)$/i.test(p[2])) continue;
            out.filters.push({ ...r, op: p[3].toUpperCase().trim(), literal: unlit(p[4]) });
        }
    }
    // group by
    const gbM = /\bGROUP\s+BY\b([\s\S]*?)(?=\b(?:ORDER\s+BY|HAVING|LIMIT|QUALIFY|UNION)\b|$)/i.exec(text);
    if (gbM) {
        const selItems = _selPre ? _splitTopLevel(_selPre[1]) : [];
        for (const raw of _splitTopLevel(gbM[1])) {
            // `GROUP BY 1` names the first select item: read the column it stands for
            const pos = /^\d+$/.test(raw.trim()) ? selItems[Number(raw.trim()) - 1] : null;
            const item = pos ? pos.replace(/\s+AS\s+[A-Za-z_]\w*\s*$/i, '') : raw;
            const a = selectAlias[item.toLowerCase()];
            for (const r of colsIn(a !== undefined ? a : item)) out.groupBy.push(a !== undefined ? { ...r, via: item.toLowerCase() } : r);
        }
    }
    // measures + projected (select list)
    const selM = /\bSELECT\b\s*(?:TOP\s+\d+\s+)?(?:DISTINCT\s+)?([\s\S]*?)\bFROM\b/i.exec(text);
    if (selM) {
        const list = selM[1];
        const agg = /\b(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*(?:DISTINCT\s+)?(?:([A-Za-z_]\w*)\.)?([A-Za-z_*]\w*)/gi;
        let a;
        while ((a = agg.exec(list))) {
            const r = a[3] === '*' ? { table: out.aliases[String(a[2] || '').toLowerCase()] || null, col: '*' } : resolve(a[2], a[3]);
            out.measures.push({ fn: a[1].toUpperCase(), ...r });
        }
        for (const r of colsIn(list)) out.projected.push(r);
    }
    // roles by precedence: key > measure > filter > dimension > projected
    const set = (r, role) => { if (!r || !r.table || !r.col || r.col === '*') return; const k = `${r.table}.${r.col}`; if (!out.roles[k]) out.roles[k] = role; };
    for (const j of out.joins) { if (j.a) { set(j.a, 'key'); set(j.b, 'key'); } }
    for (const r of out.measures) set(r, 'measure');
    for (const r of out.filters) if (!r.fn) set(r, 'filter');
    for (const r of out.groupBy) set(r, 'dimension');
    for (const r of out.filters) if (r.fn) set(r, 'filter');
    for (const r of out.projected) set(r, 'projected');
    out.ok = out.tables.length > 0;
    return out;
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. The ER model, read from the enrichment event's own facts.
// ─────────────────────────────────────────────────────────────────────────────
export function buildModel(event) {
    const enr = (event && (event.enrichments || (event.details && event.details.enrichments))) || [];
    const model = {
        kg: null, entities: {}, rels: [], bridges: [], areas: [], seeds: [], terms: [],
        provenance: {}, omitted: {}, scope: [], products: [], surfaceOnly: false,
        semantic: null, byLeaf: {}, valueDomains: {}, twins: {},
    };
    for (const e of enr) {
        if (!e || typeof e !== 'object') continue;
        model.kg = model.kg || { id: e.kg_id, name: e.kg_name };
        const facts = e.entity_facts || {};
        const attrs = e.attribute_facts || {};
        const keys = e.entity_keys || {};
        const fks = e.entity_foreign_keys || {};
        const areaByLeaf = e.subject_areas || {};
        const cols = e.table_columns || {};
        const names = new Set(Object.keys(facts));
        // entities without facts (an older event) still count from entity_names
        for (const n of (e.entity_names || [])) if (!n.includes('.') || Object.keys(facts).length === 0) names.add(n);
        // A physical table whose name another entity DEPLOYS as is that entity's twin, never a
        // box of its own: drawn separately it duplicated Incident/Offence with no relationships,
        // and a statement on v_Incident landed on the copy, so its join lit nothing (session
        // 34f5d1b6, turn 3). The twin folds into the logical entity.
        const deployedBy = {};
        for (const [k, f] of Object.entries(facts)) if (f && f.deployed && f.type !== 'table') deployedBy[String(f.deployed).toLowerCase()] = k;
        for (const n of names) {
            const f = facts[n] || {};
            if (f.type && !['entity', 'table', 'data_product', 'product_bridge', 'metric'].includes(f.type)) continue;
            const twinOf = deployedBy[String(n).toLowerCase()];
            if (twinOf && twinOf !== n) { model.twins[n] = twinOf; continue; }
            const leaf = _leaf(n);
            const deployed = f.deployed || null;
            const depLeaf = deployed ? _leaf(deployed) : null;
            const area = (f.area || areaByLeaf[depLeaf] || areaByLeaf[leaf] || null);
            const attrMap = attrs[n] || {};
            const attrNames = Object.keys(attrMap);
            const tc = cols[deployed] || cols[n] || [];
            for (const c of tc) if (!attrMap[c]) attrMap[c] = {};
            const ent = {
                name: n, leaf, label: String(n).split('.').pop(), type: f.type || 'entity', deployed, depLeaf,
                area: area ? String(area).toLowerCase() : null, description: f.description || '',
                rows: f.rows ?? null, keyUnique: f.key_unique ?? null,
                keys: (keys[n] || []).slice(), fks: (fks[n] || []).slice(),
                attrs: attrMap, attrOrder: Object.keys(attrMap),
                provenance: (e.provenance || {})[n] || null,
                basis: f.basis || null, mechanism: f.mechanism || null,
            };
            model.entities[n] = ent;
            model.byLeaf[leaf] = n;
            if (depLeaf) model.byLeaf[depLeaf] = n;
            if (deployed) model.byLeaf[String(deployed).toLowerCase()] = n;
            if (ent.area && !model.areas.includes(ent.area)) model.areas.push(ent.area);
            void attrNames;
        }
        for (const r of (e.entity_relationships || [])) {
            if (!model.entities[r.source] || !model.entities[r.target]) continue;
            const isBridge = r.type === 'relates_to' || String(r.mechanism || '').includes('correspondence')
                || model.entities[r.source].type === 'product_bridge' || model.entities[r.target].type === 'product_bridge';
            (isBridge ? model.bridges : model.rels).push({
                source: r.source, target: r.target, type: r.type, cardinality: r.cardinality || null,
                name: r.name || null, fromCols: (r.from_columns || []).map(String), toCols: (r.to_columns || []).map(String),
                declared: r.type === 'foreign_key' || r.type === 'is_a',
            });
        }
        // class-level bridges the guard knows about (may name entities not both rendered)
        for (const b of (e.class_bridges || [])) {
            const from = b.from_entity || b.from || null, to = b.to_entity || b.to || null;
            if (!from && !to) continue;
            model.bridges.push({ source: from, target: to, type: 'correspondence', declared: true, basis: b.basis || null,
                fromArea: b.from_area || null, toArea: b.to_area || null, name: b.name || null });
        }
        for (const vd of (e.value_domains || [])) {
            const t = _leaf(vd.table || ''); if (!t) continue;
            const cols = model.valueDomains[t] = model.valueDomains[t] || {};
            for (const c of (vd.columns || [])) if (c && c.name) cols[String(c.name).toLowerCase()] = { values: c.values || {}, kind: c.kind || 'code', pair: c.pair || null };
        }
        // a physical COLUMN folds with its table: `Policing_Core.v_ChainOfCustody.evidence_object_id`
        // is the attribute `ChainOfCustody.evidence_object_id`, never a second seed beside it
        const _fold = (nm) => {
            if (model.twins[nm]) return model.twins[nm];
            const i = String(nm).lastIndexOf('.'); if (i < 0) return nm;
            const owner = deployedBy[String(nm).slice(0, i).toLowerCase()];
            return owner ? `${owner}.${String(nm).slice(i + 1)}` : nm;
        };
        // one seed per fact; the folded copies' evidence (words, reasons, best score) is kept
        const _seedBy = new Map();
        for (const s0 of (e.seeds || [])) {
            const nm = _fold(s0.name); const cur = _seedBy.get(nm);
            if (!cur) { _seedBy.set(nm, { ...s0, name: nm, terms: [...(s0.terms || [])], reasons: [...(s0.reasons || [])] }); continue; }
            for (const t of (s0.terms || [])) if (!cur.terms.includes(t)) cur.terms.push(t);
            for (const r of (s0.reasons || [])) if (!cur.reasons.includes(r)) cur.reasons.push(r);
            if ((s0.score || 0) > (cur.score || 0)) cur.score = s0.score;
            if (s0.channel === 'semantic' && cur.channel !== 'product') cur.channel = cur.channel || 'semantic';
        }
        model.seeds = [..._seedBy.values()];
        model.terms = (e.question_terms || []).slice();
        for (const [k, v] of Object.entries(e.provenance || {})) { const kk = _fold(k); if (!(kk in model.provenance)) model.provenance[kk] = v; }
        Object.assign(model.omitted, e.omitted_columns || {});
        for (const d of (e.scope_databases || [])) if (!model.scope.includes(d)) model.scope.push(d);
        for (const p of (e.data_products || [])) if (!model.products.includes(p)) model.products.push(p);
        if (e.surface_only) model.surfaceOnly = true;
        model.semantic = e.semantic_seeding || model.semantic;
    }
    if (model.areas.length === 0) model.areas.push(null);
    return model;
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. The reducer — events in, ops out. ONE for live and reload.
// ─────────────────────────────────────────────────────────────────────────────
const SEVERITY = { prevented: 5, out_of_scope: 4, flagged: 3, wasted: 2, applied: 1, skipped: 1, quiet: 0 };

function _plainList(items, max = 3) {
    const a = items.slice(0, max);
    if (items.length > max) a.push(`${items.length - max} more`);
    if (a.length <= 1) return a[0] || '';
    return a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];
}

export function skinSentence(state) {
    // relevance rule: worst first; while a beat runs, the current stage sentence
    const c = state.consequences;
    const prevented = c.filter(x => x.kind === 'prevented');
    if (prevented.length) return { text: prevented[prevented.length - 1].sentence, state: 'danger' };
    if (state.verdict && state.verdict.decision === 'block') return { text: 'The answer reached beyond the trusted scope and was withheld.', state: 'danger' };
    if (state.verdict && (state.verdict.oos || []).length) return { text: `Reached outside the trusted scope: ${_plainList(state.verdict.oos)}.`, state: 'warn' };
    if (state.honesty && state.honesty.acted) return { text: state.honesty.sentence, state: 'warn' };
    if (state.beat === 'receipt' && state.receipt) return { text: state.receipt.sentence, state: state.receipt.state };
    const applied = c.filter(x => x.kind === 'applied');
    if (state.beat === 'execute' && applied.length) return { text: applied[applied.length - 1].sentence, state: 'live' };
    return { text: state.stageSentence || 'Waiting for the question…', state: state.beat === 'receipt' ? 'pass' : 'live' };
}

export function createReducer() {
    const state = {
        beat: null, beatsSeen: [], beatSentences: {}, question: null, terms: [], seeds: [], model: null,
        statements: [], consequences: [], touched: new Set(), queried: new Set(), discovery: [],
        verdict: null, honesty: null, receipt: null, stageSentence: '', synthetic: false,
        stageTimes: {}, startedAt: null, ops: [], timesReal: true,
    };

    function _beat(name, sentence, ts) {
        if (!state.beatsSeen.includes(name)) state.beatsSeen.push(name);
        state.beat = name;
        if (sentence) { state.stageSentence = sentence; state.beatSentences[name] = sentence; }
        if (ts && !state.stageTimes[name]) state.stageTimes[name] = ts;
    }
    // ONE clock: the time an event reached the stage. The wire's `ts` (the server's clock) was
    // mixed with Date.now() for the beats that carry none (execute, verify, receipt), so the
    // trail's seconds were read off two clocks. Arrival time is the viewer's experience of the
    // turn; a replay keeps the live run's times (see keepTimes) rather than re-stamping them.
    function _ts() { return Date.now(); }
    function _emit(op) { op.beat = state.beat; op.st = op.statement ? op.statement.id : (state.statements.length ? state.statements[state.statements.length - 1].id : 0); state.ops.push(op); return op; }

    function _entityForTable(leaf) {
        const m = state.model; if (!m || !leaf) return null;
        return m.entities[m.byLeaf[String(leaf).toLowerCase()]] || null;
    }
    function _relBetween(a, b) {
        const m = state.model; if (!m) return null;
        return m.rels.find(r => (r.source === a && r.target === b) || (r.source === b && r.target === a)) || null;
    }
    function _bridgeBetween(a, b) {
        const m = state.model; if (!m) return null;
        return m.bridges.find(r => (r.source === a && r.target === b) || (r.source === b && r.target === a)) || null;
    }

    // An unqualified column belongs to the ONE table of the statement whose entity carries it.
    function _bareResolver() {
        return (col, tables) => {
            const owners = tables.filter(t => { const e = _entityForTable(t); return e && [...Object.keys(e.attrs), ...e.keys, ...e.fks].some(a => String(a).toLowerCase() === col); });
            return owners.length === 1 ? owners[0] : null;
        };
    }
    // The grain of the answer: the measure's own table when it names one; for COUNT(*) over a
    // join, the FINEST table — the one that is not the 1-side of a 1:N to another table of the
    // statement (Incident ⋈ Offence counts offence rows, not incidents).
    function _grainOf(st) {
        const m = state.model; const roles = st.roles;
        if (!m || !roles || !roles.ok) return null;
        const mm = roles.measures[0];
        if (mm && mm.table) { const e = _entityForTable(mm.table); if (e) return e.name; }
        const ents = roles.tables.map(t => _entityForTable(t)).filter(Boolean);
        if (!ents.length) return null;
        if (ents.length === 1 || !mm) return ents[0].name;
        const names = new Set(ents.map(e => e.name));
        const finest = ents.filter(e => !m.rels.some(r => r.source === e.name && names.has(r.target) && r.target !== e.name
            && /^1\s*:\s*(n|m|\*)$/i.test(String(r.cardinality || ''))));
        return (finest.length === 1 ? finest[0] : ents[0]).name;
    }

    function _consequencesFor(st) {
        const ops = [];
        if (!state.model) return ops;
        if (st.sql) { st.roles = sqlRoles(st.sql, _bareResolver()); st.applied = []; st.suspect = []; }
        const roles = st.roles;
        if (!roles.ok) return ops;
        st.grain = _grainOf(st);
        const m = state.model;
        // touched entities
        for (const t of roles.tables) {
            const ent = _entityForTable(t);
            if (ent) { state.touched.add(ent.name); state.queried.add(ent.name); }
        }
        // joins against declared keys
        for (const j of roles.joins) {
            if (j.unkeyed) { st.suspect.push({ kind: 'unkeyed_join' }); continue; }
            const ea = _entityForTable(j.a.table), eb = _entityForTable(j.b.table);
            if (!ea || !eb) continue;
            const rel = _relBetween(ea.name, eb.name);
            const cols = [j.a.col, j.b.col];
            if (rel) {
                const keyCols = new Set([...rel.fromCols, ...rel.toCols].map(c => c.toLowerCase()));
                const allJoinsOnPair = roles.joins.filter(x => x.a && ((x.a.table === j.a.table && x.b.table === j.b.table) || (x.a.table === j.b.table && x.b.table === j.a.table)));
                const used = new Set(allJoinsOnPair.flatMap(x => [x.a.col, x.b.col]));
                const declared = (rel.fromCols.length ? rel.fromCols : rel.toCols).map(c => c.toLowerCase());
                const covered = declared.every(c => used.has(c));
                if (covered && cols.every(c => keyCols.has(c))) {
                    if (!st.applied.some(a => a.rel === rel)) {
                        st.applied.push({ fact: 'declared_key', rel, a: ea.name, b: eb.name, cols: declared,
                            sentence: `Joined ${ea.label} to ${eb.label} on their declared key${declared.length ? ` (${declared.join(', ')})` : ''}.` });
                    }
                } else {
                    st.suspect.push({ kind: 'wrong_key', a: ea.name, b: eb.name, used: [...used], declared });
                }
            } else if (ea.area && eb.area && ea.area !== eb.area) {
                st.suspect.push({ kind: 'cross_area', a: ea.name, b: eb.name, bridge: _bridgeBetween(ea.name, eb.name) });
            } else {
                st.suspect.push({ kind: 'undeclared', a: ea.name, b: eb.name });
            }
        }
        // filters against attribute facts
        for (const f of roles.filters) {
            const ent = f.table ? _entityForTable(f.table) : null;
            const fact = ent && ent.attrs[f.col] || (ent && ent.attrs[Object.keys(ent.attrs).find(k => k.toLowerCase() === f.col)]);
            if (!ent || !fact) continue;
            const quoted = /^'/.test(String(f.literal || ''));
            if (fact.coded && f.op === '=' ) {
                if (quoted) st.applied.push({ fact: 'code_as_string', entity: ent.name, attr: f.col, literal: f.literal,
                    sentence: `Compared ${ent.label}.${f.col} as a code${fact.decoded_as ? '' : ''}, quoted as the value domain declares.` });
                else st.suspect.push({ kind: 'code_unquoted', entity: ent.name, attr: f.col });
            }
            if (fact.decoded_from && f.op === '=') {
                st.applied.push({ fact: 'decoded_label', entity: ent.name, attr: f.col, sentence: `Filtered on the decoded label ${ent.label}.${f.col}.` });
            }
        }
        // dimensions on text dates
        for (const g of roles.groupBy) {
            const ent = g.table ? _entityForTable(g.table) : null;
            const fact = ent && ent.attrs[g.col];
            if (ent && fact && fact.semantic === 'date' && /char|varchar/i.test(String(fact.type || ''))) {
                st.applied.push({ fact: 'date_shape', entity: ent.name, attr: g.col, shape: fact.shape || null,
                    sentence: `Grouped by ${ent.label}.${g.col}${g.via ? ` (as ${g.via})` : ''}, a date stored as text${fact.shape ? ` in the shape ${fact.shape}` : ''}.` });
            }
        }
        for (const a of st.applied) {
            if (!state.consequences.some(c => c.kind === 'applied' && c.fact === a.fact && c.entity === a.entity && c.a === a.a && c.b === a.b)) {
                const c = { kind: 'applied', ...a, statement: st.id };
                state.consequences.push(c);
                ops.push(_emit({ op: 'applied', consequence: c }));
            }
        }
        return ops;
    }

    function _statement(tool, args, source) {
        if (/^tda_/i.test(String(tool))) return [];   // a component or framework call (dashboard, chart, graph read) is not a data statement
        const sql = args && typeof args.sql === 'string' ? args.sql : (args && typeof args.query === 'string' ? args.query : null);
        const id = state.statements.length + 1;
        const st = { id, tool, sql, roles: sql ? sqlRoles(sql) : { ok: false, tables: [], joins: [], filters: [], groupBy: [], measures: [], roles: {} },
            applied: [], suspect: [], result: null, source, args: args || {} };
        state.statements.push(st);
        if (!FRAMEWORK_TOOLS.test(tool || '') && DISCOVERY_TOOLS.test(tool || '')) state.discovery.push(tool);
        // non-SQL data tools touching a named object
        if (!sql && args) {
            for (const k of ['table_name', 'object_name', 'obj_name']) {
                const ent = args[k] ? _entityForTable(args[k]) : null;
                if (ent) { state.touched.add(ent.name); if (!DISCOVERY_TOOLS.test(tool || '')) state.queried.add(ent.name); }
            }
        }
        _beat('execute', sql ? 'Checking the statement against the model…' : `Running ${tool}…`, Date.now());
        const ops = [_emit({ op: 'statement', statement: st })];
        ops.push(..._consequencesFor(st));
        return ops;
    }

    function _preventedFrom(result, tool) {
        if (!result) return null;
        const r = typeof result === 'object' ? result : {};
        const text = typeof result === 'string' ? result : String(r.data || r.error || r.error_message?.details || r.error_message?.summary || '');
        const flagged = r.preflight === true || /\[Preflight/.test(text);
        if (!flagged) return null;
        const kind = r.preflight_cross_domain ? 'cross_domain' : r.preflight_join ? 'join_key' : r.preflight_text_date ? 'text_date' : r.preflight_value ? 'value' : /Database '/.test(text) ? 'schema' : 'preflight';
        const violations = r.violations || r.findings || [];
        let sentence;
        switch (kind) {
            case 'cross_domain': sentence = 'Stopped a join across subject areas — the model declares no key between them.'; break;
            case 'join_key': { const v = violations[0] || {}; sentence = `Stopped a join on the wrong columns${v.key ? ` — the declared key is ${[].concat(v.key).join(', ')}` : ''}.`; break; }
            case 'text_date': sentence = 'Stopped a comparison that treated a text-stored date as a string.'; break;
            case 'value': { const v = violations[0] || {}; sentence = `Stopped a filter on a value the column never holds${v.literal ? ` (${v.literal})` : ''}.`; break; }
            case 'schema': sentence = 'Stopped a query against a database the catalog does not hold.'; break;
            default: sentence = 'Stopped a statement before the database saw it.';
        }
        return { kind: 'prevented', check: kind, tool, message: text.slice(0, 400), violations, sentence };
    }

    // The tool wrapper rewrote the model's statement before it ran (a fan-out join → aggregate-
    // then-join, a text-stored date, a reserved alias, a trailing LIMIT/TOP): the EXECUTED
    // statement is the one the stage narrates — facts read off the original were never applied.
    // Emitted by the ReAct lane only (the Optimize lane re-issues a corrected tool_intent).
    function _rewrite(payload) {
        const tool = payload && payload.tool_name; const sql = payload && payload.executed_sql;
        if (!tool || typeof sql !== 'string' || /^tda_/i.test(String(tool))) return [];
        // The agent emits the rewrite AFTER the tool's completion (live and persisted:
        // invoked → completed → rewrite), so the statement it names usually HAS its result.
        // A statement still waiting is taken first; else the latest one of this tool the
        // rewrite names by its original text, else the latest not yet rewritten.
        const mine = [...state.statements].reverse().filter(s => s.tool === tool && !s.rewritten);
        const _n = (x) => String(x || '').replace(/\s+/g, ' ').trim();
        const st = mine.find(s => !s.result)
            || (payload.original_sql ? mine.find(s => _n(s.sql) === _n(payload.original_sql)) : null)
            || mine[0] || null;
        if (!st) return [];
        st.original_sql = st.sql; st.sql = sql; st.roles = sqlRoles(sql);
        st.rewritten = { kind: payload.kind || 'rewrite', label: payload.label || payload.kind || 'rewrite', summary: payload.summary || '' };
        for (let i = state.consequences.length - 1; i >= 0; i--) if (state.consequences[i].kind === 'applied' && state.consequences[i].statement === st.id) state.consequences.splice(i, 1);
        st.applied = []; st.suspect = [];
        const ops = [_emit({ op: 'statement', statement: st, rewritten: true })];
        const c = { kind: 'applied', fact: 'rewrite', check: st.rewritten.kind, statement: st.id, tool,
            sentence: `Rewrote the statement before it ran (${st.rewritten.label}, no LLM round spent)${st.rewritten.summary ? ` — ${String(st.rewritten.summary).slice(0, 160)}` : ''}.` };
        state.consequences.push(c);
        ops.push(_emit({ op: 'applied', consequence: c }));
        ops.push(..._consequencesFor(st));
        // the result already reported belongs to the statement that ran — say so again, so
        // the stage's rows land on the rewritten statement
        if (st.result && !st.result.prevented) ops.push(_emit({ op: 'executed', statement: st, ok: st.result.ok, rows: st.result.rows }));
        return ops;
    }

    // PROP-008 OBDA-MODEL: the KG tool's propose_* result is the agent proposing — or
    // drafting — a node the model does not have. Read from the SAME event the statement
    // machinery reads (the Optimize tool_result's details, the ReAct completion's content
    // preview), so the live turn and its replay tell one story. A proposal that was not
    // drafted is still narrated: the stage shows what the agent wanted to add.
    function _draftFrom(tool, result) {
        if (!/^tda_knowledgegraph$/i.test(String(tool || ''))) return [];
        let spec = result && (result.spec || (result.details && result.details.spec));
        if (!spec && result && typeof result.result_preview === 'string') {
            try { spec = JSON.parse(result.result_preview).spec; } catch (_) { spec = null; }
        }
        if (!spec || typeof spec !== 'object' || !/^propose_/.test(String(spec.action || ''))) return [];
        const v = spec.verdict || {};
        const kind = spec.action === 'propose_entity' ? 'entity' : 'attribute';
        const name = kind === 'entity' ? spec.entity : spec.attribute;
        const parent = kind === 'entity' ? spec.relates_to : spec.entity;
        if (!name) return [];
        const c = { kind: 'drafted', what: kind, name, parent: parent || null, decision: v.decision || null,
            rule: v.rule || null, drafted: !!spec.drafted, provisional: !!v.provisional, must: !!v.must,
            sentence: spec.drafted
                ? `Drafted ${kind} ${name}${parent ? ` on ${String(parent).split('.').pop()}` : ''} on the authored layer${v.rule ? ` (${String(v.rule).replace(/_/g, ' ')})` : ''}${v.provisional ? ' — provisional, no rows to measure' : ''}.`
                : `Proposed ${kind} ${name}${parent ? ` on ${String(parent).split('.').pop()}` : ''} — the model's rules read it as ${v.decision || 'undecided'}${v.rule ? ` (${String(v.rule).replace(/_/g, ' ')})` : ''}; not drafted.` };
        state.consequences.push(c);
        return [_emit({ op: 'draft', consequence: c })];
    }

    function _result(tool, result, source) {
        if (/^tda_/i.test(String(tool))) return [];
        const st = [...state.statements].reverse().find(s => s.tool === tool && !s.result) || state.statements[state.statements.length - 1];
        const prevented = _preventedFrom(result, tool);
        const ops = [];
        if (st) st.result = { ok: !prevented && !(result && result.status === 'error'), prevented: !!prevented, rows: result && (result.result_rows ?? result.row_count ?? null) };
        if (prevented) {
            // facts read off a statement that never reached the database were not applied
            if (st) for (let i = state.consequences.length - 1; i >= 0; i--) if (state.consequences[i].kind === 'applied' && state.consequences[i].statement === st.id) state.consequences.splice(i, 1);
            const c = { ...prevented, statement: st ? st.id : null, suspect: st ? st.suspect : [] };
            state.consequences.push(c);
            ops.push(_emit({ op: 'prevented', consequence: c, statement: st }));
        } else if (st) {
            ops.push(_emit({ op: 'executed', statement: st, ok: st.result.ok, rows: st.result.rows }));
        }
        return ops;
    }

    function _synthesizeStages(model, ts, only) {
        // `only`: emit a subset of the missing stages — the caller interleaves the model op
        // between Understand and Seed on a reconstructed turn (see kg_enrichment)
        const ops = []; const want = (b) => !only || only.includes(b);
        if (want('understand') && !state.beatsSeen.includes('understand')) {
            state.terms = model.terms; _beat('understand', 'Understanding the question…', ts);
            ops.push(_emit({ op: 'question', terms: model.terms, question: state.question, synthetic: true }));
        }
        if (want('seed') && (!state.beatsSeen.includes('seed') || state._seedPending)) {
            state._seedPending = false;
            if (!state.beatsSeen.includes('seed')) { state.seeds = model.seeds; _beat('seed', `Seeded ${_plainList(model.seeds.slice(0, 3).map(s => _leaf(s.name)), 3) || 'the model'}.`, ts); }
            ops.push(_emit({ op: 'seeds', seeds: model.seeds, synthetic: true }));
        }
        if (want('expand') && !state.beatsSeen.includes('expand')) {
            const nCor = correspondenceCount(model); const nRel = model.rels.length + nCor;
            _beat('expand', `Expanded along ${model.rels.length} declared relationship${model.rels.length === 1 ? '' : 's'}${nCor ? ` and ${nCor} correspondence${nCor === 1 ? '' : 's'}` : ''}.`, ts);
            ops.push(_emit({ op: 'expand', counts: { entities: entityCount(model), relationships: nRel }, synthetic: true }));
        }
        if (want('inject') && !state.beatsSeen.includes('inject')) {
            const n = entityCount(model);
            _beat('inject', model.surfaceOnly ? `Injected ${model.products.length} data product contract${model.products.length === 1 ? '' : 's'}; schemas withheld.`
                : `Injected ${nodesLabel(n)} and ${relationshipsLabel(model.rels.length)} into the agent's context.`, ts);
            ops.push(_emit({ op: 'inject', omitted: model.omitted, surfaceOnly: model.surfaceOnly, synthetic: true }));
        }
        return ops;
    }

    function apply(type, payload) {
        payload = payload || {};
        const ops = [];
        if (!state.startedAt) state.startedAt = Date.now();
        switch (type) {
            case 'kg_stage': {
                const ts = _ts(payload);
                if (payload.stage === 'understand') {
                    state.terms = payload.terms || [];
                    _beat('understand', 'Understanding the question…', ts);
                    ops.push(_emit({ op: 'question', terms: state.terms, question: state.question }));
                } else if (payload.stage === 'seed') {
                    state.seeds = payload.seeds || [];
                    const top = state.seeds.filter(s => s.channel !== 'product').slice(0, 3).map(s => _leaf(s.name));
                    _beat('seed', state.seeds.length ? `Seeded ${_plainList(top, 3)}.` : 'Nothing in the model matches the question.', ts);
                    ops.push(_emit({ op: 'seeds', seeds: state.seeds }));
                } else if (payload.stage === 'expand') {
                    const pc = payload.provenance_counts || {};
                    _beat('expand', `Expanded to ${payload.entities} node${payload.entities === 1 ? '' : 's'} along ${payload.relationships} relationship${payload.relationships === 1 ? '' : 's'}${pc.fk_chain ? ` — ${pc.fk_chain} reached through declared keys` : ''}.`, ts);
                    ops.push(_emit({ op: 'expand', counts: { entities: payload.entities, relationships: payload.relationships }, provenanceCounts: pc }));
                } else if (payload.stage === 'inject') {
                    const folded = Object.values(payload.omitted_columns || {}).reduce((a, b) => a + (Number(b) || 0), 0);
                    _beat('inject', payload.surface_only ? 'Injected the data product contracts; schemas withheld.'
                        : `Injected ${payload.entities} node${payload.entities === 1 ? '' : 's'} into the agent's context${folded ? `, ${folded} attributes folded` : ''}.`, ts);
                    ops.push(_emit({ op: 'inject', omitted: payload.omitted_columns || {}, surfaceOnly: !!payload.surface_only }));
                }
                break;
            }
            case 'kg_enrichment': {
                const model = buildModel(payload);
                state.model = model;
                if (!state.terms.length) state.terms = model.terms;
                if (!state.seeds.length) state.seeds = model.seeds;
                // Every op carries the beat it belongs to. On a RECONSTRUCTED turn (no live stage
                // notices) the model arrives first: emitted with no beat, its op — which lights the
                // seeds and draws the threads — ran under a hold at Understand (the scrub can only
                // stop before an op whose beat is past the target), so a held Understand read as
                // Seed (2026-09-17). Understand is synthesized BEFORE the model op, which then
                // belongs to Seed; live, the model arrives after Inject and keeps that beat.
                const ts = Date.now();
                const synthQ = _synthesizeStages(model, ts, ['understand']);
                if (!state.beatsSeen.includes('seed')) { state.seeds = model.seeds; _beat('seed', `Seeded ${_plainList(model.seeds.slice(0, 3).map(s => _leaf(s.name)), 3) || 'the model'}.`, ts); state._seedPending = true; }
                ops.push(...synthQ, _emit({ op: 'model', model }));
                const synthRest = _synthesizeStages(model, ts, ['seed', 'expand', 'inject']);
                if (synthQ.length || synthRest.length) state.synthetic = true;
                ops.push(...synthRest);
                // re-read consequences for statements that arrived before the model
                for (const st of state.statements) ops.push(..._consequencesFor(st));
                break;
            }
            case 'user_query': {
                state.question = payload.query || payload.text || null;
                break;
            }
            case 'tool_intent': {
                const a = payload.tool_name ? payload : (payload.details || payload);
                if (a && a.tool_name) ops.push(..._statement(a.tool_name, a.arguments || {}, 'optimize'));
                break;
            }
            case 'conversation_tool_invoked': {
                if (payload.tool_name) ops.push(..._statement(payload.tool_name, payload.arguments || {}, 'react'));
                break;
            }
            case 'tool_result': {
                const details = payload.details !== undefined ? payload.details : payload;
                const tool = payload.tool_name || (details && details.tool_name) || (state.statements[state.statements.length - 1] || {}).tool;
                ops.push(..._draftFrom(payload.tool_name || (details && details.tool_name), details));
                ops.push(..._result(tool, details, 'optimize'));
                break;
            }
            case 'conversation_tool_rewrite': {
                ops.push(..._rewrite(payload));
                break;
            }
            case 'conversation_tool_completed': {
                const preview = payload.result_preview || '';
                const flagged = /\[Preflight/.test(preview);
                const res = flagged ? { status: 'error', preflight: true, data: preview,
                    preflight_cross_domain: /subject area|cross-domain|correspondence/i.test(preview),
                    preflight_join: /declared key|join key|not the declared/i.test(preview) && !/subject area/i.test(preview),
                    preflight_text_date: /text-stored date|FORMAT|as a string/i.test(preview),
                    preflight_value: /never holds|value domain|distinct values/i.test(preview) }
                    : { status: payload.success === false ? 'error' : 'success', result_rows: payload.result_rows ?? null };
                ops.push(..._draftFrom(payload.tool_name, payload));
                ops.push(..._result(payload.tool_name, res, 'react'));
                break;
            }
            case 'grounding_gate': {
                const d = payload.details || payload;
                if (d && (d.ran !== false || d.decision)) {
                    state.verdict = { decision: d.decision || 'pass', oos: [...new Set([...(d.out_of_scope_dbs || []), ...(d.answer_oos_refs || [])])], scope: d.scope_databases || [], judged: !!d.judged, ungrounded: (d.ungrounded_numbers || []).slice() };
                    _beat('verify', state.verdict.decision === 'block' ? 'The answer was withheld: it reached beyond the trusted scope.'
                        : state.verdict.oos.length ? 'Reached outside the trusted scope.' : 'Stayed inside the trusted scope.', Date.now());
                    ops.push(_emit({ op: 'verdict', verdict: state.verdict }));
                }
                break;
            }
            case 'honesty': {
                const d = payload.details || payload;
                if (d && d.acted) {
                    const kind = d.kind || 'honesty';
                    const sentence = ({
                        retrieval_failure: 'Refused to call a failed lookup an absence.',
                        retrieval_failed: 'Refused to call a failed lookup an absence.',
                        fabricated_after_failure: 'Flagged figures stated after every data query failed.',
                        sample_extrapolation: 'Flagged a claim about all rows drawn from a sample.',
                        correspondence_overreach: `Noted that the declared correspondence does not cover what was asked${(d.overreach || []).length ? ` (${d.overreach.slice(0, 3).join(', ')})` : ''} \u2014 the answer lists the evidence as such.`,
                        unread_citation: 'Flagged a citation of a section the agent never opened.',
                        empty_relation: 'Refused to read an empty result as “there are none”.',
                        unverified_literal: 'Flagged a zero on a value this turn never verified.',
                        fabricated_identifier: 'Flagged an identifier the data never showed.',
                        grain_substitution: 'Flagged an answer given at a coarser grain than asked.',
                        unbacked_act: 'Flagged a change the answer claims but this turn never made.',
                        unsourced_record: 'Flagged a record shown without any query behind it.',
                        claimed_effect_after_denial: 'Flagged a write the answer reports but the agent\'s authority denied.',
                    })[kind] || 'The honesty check altered the answer.';
                    state.honesty = { kind, decision: d.decision, sentence, acted: true };
                    if (state.beat === 'verify' && state.stageSentence && !state.stageSentence.includes(sentence)) { state.stageSentence += ` ${sentence}`; state.beatSentences.verify = state.stageSentence; }
                    ops.push(_emit({ op: 'honesty', honesty: state.honesty }));
                }
                break;
            }
            default: break;
        }
        return ops;
    }

    function end(meta) {
        meta = meta || {};
        const m = state.model;
        const applied = state.consequences.filter(c => c.kind === 'applied');
        const prevented = state.consequences.filter(c => c.kind === 'prevented');
        const seedNames = state.seeds.filter(s => s.channel !== 'product').map(s => s.name);
        const roleKeys = new Set(state.statements.flatMap(s => Object.keys((s.roles && s.roles.roles) || {})));
        const seedsUsed = seedNames.filter(n => {
            if (state.touched.has(n)) return true;
            const owner = n.split('.').slice(0, -1).join('.'), attr = _leaf(n);
            const ent = m && m.entities[owner];
            return !!(ent && state.touched.has(owner) && (roleKeys.has(`${ent.depLeaf}.${attr}`) || roleKeys.has(`${ent.leaf}.${attr}`)));
        });
        const skipped = m ? [...state.queried].filter(n => !state.discovery.length && m.entities[n]) : [];
        const wasted = m && seedNames.length > 0 && seedsUsed.length === 0 && state.statements.some(s => s.sql);
        const grainEnt = (() => {
            const st = [...state.statements].reverse().find(s => s.roles && s.roles.measures && s.roles.measures.length);
            const g = st ? (st.grain || _grainOf(st)) : null;
            return g && m && m.entities[g] ? m.entities[g].label : null;
        })();
        let sentence, stateWord = 'pass';
        const ranOk = state.statements.some(s => s.result && s.result.ok);
        if (prevented.length && !ranOk) { sentence = `${prevented.length === 1 ? 'One statement' : `${prevented.length} statements`} stopped before the database; nothing was answered from a wrong join.`; stateWord = 'danger'; }
        else if (wasted) { sentence = `The graph offered ${seedNames.length} seed${seedNames.length === 1 ? '' : 's'} and the agent used none of them.`; stateWord = 'warn'; }
        else if (state.verdict && state.verdict.decision === 'block') { sentence = 'The answer was withheld for reaching beyond the trusted scope.'; stateWord = 'danger'; }
        else {
            const parts = [];
            if (grainEnt) parts.push(`answered at the grain of ${grainEnt}`);
            if (applied.some(a => a.fact === 'declared_key')) parts.push('joined on declared keys');
            if (state.verdict) parts.push(state.verdict.oos.length ? 'reached outside the trusted scope' : 'stayed inside the trusted scope');
            sentence = parts.length ? parts.join(', ').replace(/^./, c => c.toUpperCase()) + '.' : (state.statements.length ? 'The turn ran without touching the model.' : 'The graph was read; no statement ran.');
            if (state.verdict && state.verdict.oos.length) stateWord = 'warn';
            // a check that altered the answer is part of the turn's verdict, never a pass beside a warning
            if (state.honesty && state.honesty.acted) { sentence += ` ${state.honesty.sentence}`; stateWord = 'warn'; }
        }
        const factsWords = applied.map(a => ({ declared_key: 'declared key', code_as_string: 'code as string', date_shape: 'date shape', decoded_label: 'decoded label', rewrite: 'statement rewritten' })[a.fact] || a.fact);
        state.receipt = {
            sentence, state: stateWord, applied: applied.length, prevented: prevented.length,
            skipped: skipped.length, seeds: seedNames.length, seedsUsed: seedsUsed.length, wasted: !!wasted,
            facts: [...new Set(factsWords)], altered: !!(state.honesty && state.honesty.acted), inScope: state.verdict ? !state.verdict.oos.length : null,
            durationMs: meta.durationMs || (state.startedAt ? Date.now() - state.startedAt : null),
        };
        _beat('receipt', sentence, Date.now());
        return [_emit({ op: 'receipt', receipt: state.receipt })];
    }

    return { state, apply, end, skin: () => skinSentence(state) };
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Reload replay: the persisted turn → the same events, in turn order.
// ─────────────────────────────────────────────────────────────────────────────
export function eventsFromTurn(turnData) {
    const ev = [];
    if (!turnData) return ev;
    if (turnData.user_query) ev.push(['user_query', { query: turnData.user_query }]);
    if (turnData.kg_enrichment_event) ev.push(['kg_enrichment', turnData.kg_enrichment_event]);
    // Optimize: execution_trace entries {action:{tool_name, arguments}, result}
    for (const step of (turnData.execution_trace || [])) {
        const a = step && step.action; if (!a || !a.tool_name || /^TDA_SystemLog$/i.test(a.tool_name)) continue;
        ev.push(['tool_intent', { tool_name: a.tool_name, arguments: a.arguments || {} }]);
        ev.push(['tool_result', { tool_name: a.tool_name, details: step.result }]);
    }
    // ReAct: conversation_agent_events {type, payload}
    for (const e of (turnData.conversation_agent_events || [])) {
        const t = e && (e.type || e.event_type), p = e && (e.payload || e.details || e);
        if (t === 'conversation_tool_invoked' || t === 'conversation_tool_completed' || t === 'conversation_tool_rewrite') ev.push([t, p]);
    }
    if (turnData.grounding && typeof turnData.grounding === 'object') ev.push(['grounding_gate', turnData.grounding]);
    if (turnData.honesty && typeof turnData.honesty === 'object') ev.push(['honesty', turnData.honesty]);
    return ev;
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. Label layer — no two rendered labels overlap.
//    labels: [{id, w, h, candidates:[{x,y}], priority}] → placements {id:{x,y}|null}
// ─────────────────────────────────────────────────────────────────────────────
export function placeLabels(labels, occupied, bounds) {
    const taken = (occupied || []).map(r => ({ ...r }));
    const out = {};
    const hits = (r) => taken.some(t => !(r.x + r.w <= t.x || t.x + t.w <= r.x || r.y + r.h <= t.y || t.y + t.h <= r.y));
    const inside = (r) => !bounds || (r.x >= bounds.x && r.y >= bounds.y && r.x + r.w <= bounds.x + bounds.w && r.y + r.h <= bounds.y + bounds.h);
    const sorted = [...labels].sort((a, b) => (b.priority || 0) - (a.priority || 0));
    for (const l of sorted) {
        let placed = null;
        for (const c of (l.candidates || [])) {
            const r = { x: c.x, y: c.y, w: l.w, h: l.h };
            if (inside(r) && !hits(r)) { placed = { x: c.x, y: c.y }; taken.push(r); break; }
        }
        out[l.id] = placed;   // null = folded into the hover
    }
    return out;
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. The stage — mountTheatre(host, {depth, onExit, onDepth})
// ─────────────────────────────────────────────────────────────────────────────
const ICONS = {
    key: '<circle cx="7.5" cy="15.5" r="4"/><path d="M10.4 12.6 20 3m-3 3 3 3m-6 0 2 2"/>',
    filter: '<path d="M3 5h18l-7 8v6l-4-2v-4z"/>',
    dimension: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 5v15"/>',
    measure: '<path d="M18 5H6l6 7-6 7h12"/>',
    projected: '<circle cx="12" cy="12" r="3"/>',
    seed: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
    absent: '<circle cx="12" cy="12" r="8"/><line x1="6.5" y1="17.5" x2="17.5" y2="6.5"/>',
    stop: '<circle cx="12" cy="12" r="9"/><line x1="7" y1="7" x2="17" y2="17"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 5-5"/>',
    graph: '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><line x1="11" y1="7" x2="6" y2="17"/><line x1="13" y1="7" x2="18" y2="17"/>',
    replay: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
    chev: '<polyline points="6 9 12 15 18 9"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-5M12 8h.01"/>',
    legend: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1.2"/><circle cx="4" cy="12" r="1.2"/><circle cx="4" cy="18" r="1.2"/>',
};
const ROLE_ICON = { seed: 'seed', key: 'key', filter: 'filter', dimension: 'dimension', measure: 'measure', projected: 'projected', absent: 'absent' };

function _esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function _svgIcon(name, x, y, size, color, sw = 2) {
    return `<g transform="translate(${x},${y}) scale(${size / 24})"><g fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ''}</g></g>`;
}
function _htmlIcon(name, size = 14) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ''}</svg>`;
}
function _tok(name, fb) {
    try { const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fb; } catch (_) { return fb; }
}
function _reducedMotion() { try { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) { return false; } }

const _CSS_ID = 'kg-theatre-styles';
function _injectCss() {
    if (document.getElementById(_CSS_ID)) return;
    const st = document.createElement('style'); st.id = _CSS_ID;
    st.textContent = `
.kgt-root{position:relative;display:flex;flex-direction:column;flex:1;min-height:0;background:var(--bg-primary,#141619);color:var(--text-primary,#f8fafc);font-family:var(--font-primary,'Inter',sans-serif);
  background-image:radial-gradient(rgba(255,255,255,0.045) 1px, transparent 1px);background-size:22px 22px}
.kgt-stage{flex:1;min-height:0;position:relative;overflow:hidden}
.kgt-stage svg{display:block;width:100%;height:100%}
.kgt-caption{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:56px;padding:6px 14px;background:var(--bg-secondary,#1f2126);border-top:1px solid var(--border-secondary,rgba(255,255,255,0.06))}
.kgt-cap-left{display:flex;align-items:center;gap:12px;min-width:0;flex:1}
.kgt-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;background:var(--teradata-orange,#F15F22)}
.kgt-dot.live{animation:kgt-breathe 1.6s ease-in-out infinite}
.kgt-dot.pass{background:var(--trust-pass,#34d399)}.kgt-dot.warn{background:var(--trust-warn,#fbbf24)}.kgt-dot.danger{background:var(--trust-danger,#f87171)}.kgt-dot.na{background:var(--text-muted,#94a3b8)}
.kgt-cap-text{min-width:0}
.kgt-cap-main{font-size:13px;line-height:1.3;color:var(--text-primary,#f8fafc);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.kgt-cap-sub{font-size:11px;color:var(--text-muted,#94a3b8);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.kgt-ribbon{display:flex;align-items:center;gap:12px;flex-shrink:0}
.kgt-beat{display:inline-flex;align-items:center;gap:6px;font-size:10.5px;color:var(--text-muted,#94a3b8);opacity:.55}
.kgt-beat i{display:inline-block;width:7px;height:7px;border-radius:50%;border:1px dashed var(--text-muted,#94a3b8)}
.kgt-beat.done{opacity:1}.kgt-beat.done i{border:none;background:var(--trust-pass,#34d399)}
.kgt-beat.danger i{background:var(--trust-danger,#f87171)!important;border:none}
.kgt-beat.active{opacity:1;color:var(--text-primary,#f8fafc);font-weight:600}.kgt-beat.active i{border:none;width:9px;height:9px;background:var(--teradata-orange,#F15F22);box-shadow:0 0 0 3px rgba(241,95,34,.2)}
.kgt-beat.active.danger i{box-shadow:0 0 0 3px rgba(248,113,113,.2)}
.kgt-actions{display:flex;align-items:center;gap:6px;flex-shrink:0;margin-left:8px}
.kgt-btn{display:inline-flex;align-items:center;gap:5px;height:24px;padding:0 8px;border-radius:6px;border:1px solid var(--border-secondary,rgba(255,255,255,0.08));background:transparent;color:var(--text-muted,#94a3b8);font-size:10.5px;cursor:pointer}
.kgt-btn:hover{color:var(--text-primary,#f8fafc);background:var(--hover-bg-strong,rgba(255,255,255,0.08))}
.kgt-pills{display:inline-flex;gap:2px;padding:2px;border-radius:9999px;background:rgba(255,255,255,0.04);border:1px solid var(--border-secondary,rgba(255,255,255,0.06))}
.kgt-pill{height:20px;padding:0 9px;border-radius:9999px;font-size:10.5px;font-weight:600;border:1px solid transparent;background:transparent;color:var(--text-muted,#94a3b8);cursor:pointer}
.kgt-pill.on{background:rgba(241,95,34,.12);color:var(--teradata-orange,#F15F22);border-color:rgba(241,95,34,.4)}
.kgt-rail{position:absolute;top:10px;right:10px;width:248px;box-sizing:border-box;max-height:calc(100% - 20px);overflow:auto;border-radius:10px;background:var(--card-bg,rgba(24,26,30,0.92));border:1px solid var(--border-primary,rgba(255,255,255,0.1));padding:10px 12px;display:none}
.kgt-root.inspect .kgt-rail{display:block}
.kgt-rail h4{margin:0 0 8px;font-size:9px;letter-spacing:.12em;color:var(--text-muted,#94a3b8);font-weight:600}
.kgt-trail{display:flex;flex-direction:column;gap:0}
.kgt-tr{display:grid;grid-template-columns:14px 1fr auto;column-gap:8px;padding:6px 6px;margin:0 -6px;border-radius:6px;border-left:0}
.kgt-tr.done{cursor:pointer}.kgt-tr.done:hover{background:var(--hover-bg-strong,rgba(255,255,255,0.06))}
.kgt-tr.held{background:rgba(241,95,34,.10);box-shadow:inset 2px 0 0 var(--teradata-orange,#F15F22)}.kgt-tr.ahead{opacity:.4}.kgt-beat.ahead{opacity:.45}
.kgt-tr.preview,.kgt-tr-st.preview{box-shadow:inset 2px 0 0 transparent;background:repeating-linear-gradient(180deg,var(--teradata-orange,#F15F22) 0 3px,transparent 3px 6px) left/2px 100% no-repeat,rgba(241,95,34,.06)}
.kgt-tr.known{cursor:pointer}.kgt-tr.known:hover{background:var(--hover-bg-strong,rgba(255,255,255,0.06))}
.kgt-tr.queued .d{background:transparent;border:1px dashed var(--teradata-orange,#F15F22);opacity:.9;box-sizing:border-box}.kgt-tr.queued .t{color:var(--text-secondary,#e2e8f0)}.kgt-tr.queued .s,.kgt-tr.absent .s,.kgt-tr.pending .s{font-style:italic;opacity:.6}
.kgt-tr.absent .d{background:transparent;border:1px solid var(--text-muted,#94a3b8);opacity:.5;box-sizing:border-box}.kgt-tr.absent .t{color:var(--text-muted,#94a3b8);text-decoration:line-through;text-decoration-color:rgba(148,163,184,.5)}
.kgt-beat.queued i{border:1px dashed var(--teradata-orange,#F15F22)}
.kgt-btn[aria-pressed="true"]{color:var(--teradata-orange,#F15F22);border-color:rgba(241,95,34,.45);background:rgba(241,95,34,.10)}
.kgt-explain{display:none;align-items:baseline;gap:10px;padding:6px 14px 7px;background:var(--bg-secondary,#1f2126);border-top:1px solid var(--border-secondary,rgba(255,255,255,0.06));font-size:11.5px;line-height:1.45;color:var(--text-secondary,#e2e8f0)}
.kgt-root.explain .kgt-explain{display:flex}
.kgt-explain .lb{flex:0 0 auto;font-size:9px;letter-spacing:.12em;font-weight:600;color:var(--teradata-orange,#F15F22);padding-top:2px}
.kgt-explain .tx{min-width:0}.kgt-explain .does{color:var(--text-muted,#94a3b8)}.kgt-explain .did{color:var(--text-primary,#f8fafc)}.kgt-explain .st{color:var(--text-muted,#94a3b8);font-style:italic}
.kgt-legend{display:none;flex-wrap:wrap;gap:4px 18px;padding:7px 14px 8px;background:var(--bg-secondary,#1f2126);border-top:1px solid var(--border-secondary,rgba(255,255,255,0.06));font-size:10.5px;color:var(--text-secondary,#e2e8f0);max-height:34%;overflow:auto}
.kgt-root.legend .kgt-legend{display:flex}
.kgt-legend .grp{display:flex;flex-wrap:wrap;align-items:center;gap:3px 12px}.kgt-legend .gl{flex:0 0 100%;font-size:9px;letter-spacing:.12em;font-weight:600;color:var(--text-muted,#94a3b8);margin-top:3px}
.kgt-legend .it{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}.kgt-legend .it svg{flex:0 0 auto;display:block}.kgt-legend .it .sv{font-family:var(--font-mono,'JetBrains Mono',monospace);font-size:9.5px;color:var(--text-muted,#94a3b8)}
.kgt-question.kgt-q-focus [data-term="0"]{opacity:.18!important}.kgt-question.kgt-q-focus text[data-term="1"]{opacity:1!important;font-weight:600}.kgt-question.kgt-q-focus rect[data-term="1"]{stroke-opacity:1!important}
svg.kgt-v-focus .kgt-perimeter,svg.kgt-v-focus .kgt-ghost{opacity:1!important}
.kgt-tr .d{width:8px;height:8px;border-radius:50%;background:var(--text-muted,#94a3b8);margin-top:5px;opacity:.5}
.kgt-tr.done .d{background:var(--trust-pass,#34d399);opacity:1}.kgt-tr.active .d{background:var(--teradata-orange,#F15F22);opacity:1;animation:kgt-breathe 1.6s ease-in-out infinite}.kgt-tr.danger .d{background:var(--trust-danger,#f87171);opacity:1}
.kgt-tr .t{font-size:11px;font-weight:600;color:var(--text-primary,#f8fafc)}.kgt-tr.pending .t{color:var(--text-muted,#94a3b8)}
.kgt-tr .s{grid-column:2/4;font-size:10px;color:var(--text-secondary,#e2e8f0);opacity:.85;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kgt-tr .ts{font-size:9.5px;color:var(--text-muted,#94a3b8);font-family:var(--font-mono,'JetBrains Mono',monospace)}
.kgt-strip{position:absolute;left:14px;right:14px;bottom:10px;min-height:32px;max-height:74px;border-radius:8px;background:#1a1c21;border:1px solid var(--border-primary,rgba(255,255,255,0.1));display:block;padding:7px 10px;line-height:1.55;font-family:var(--font-mono,'JetBrains Mono',monospace);font-size:10.5px;color:var(--text-muted,#94a3b8);overflow:hidden;white-space:normal;overflow-wrap:anywhere;cursor:pointer;opacity:0;transition:opacity .3s}
.kgt-strip.on{opacity:1}.kgt-strip.open{max-height:45%;overflow:auto}
.kgt-strip .stmts{display:inline-flex;gap:4px;margin-right:8px;vertical-align:middle}.kgt-strip .st{min-width:18px;height:18px;padding:0 5px;border-radius:9px;border:1px solid var(--border-primary,rgba(255,255,255,.14));background:transparent;color:var(--text-muted,#94a3b8);font:600 10px var(--font-primary,'Inter',sans-serif);cursor:pointer}.kgt-strip .st.ok{border-color:rgba(52,211,153,.5);color:var(--trust-pass,#34d399)}.kgt-strip .st.bad{border-color:rgba(248,113,113,.6);color:var(--trust-danger,#f87171)}.kgt-strip .st.on{background:var(--teradata-orange,#F15F22);border-color:var(--teradata-orange,#F15F22);color:#fff}
.kgt-tr-st{display:grid;grid-template-columns:14px 1fr auto;column-gap:8px;padding:3px 6px 3px 22px;margin:0 -6px;border-radius:6px;font-size:10px;color:var(--text-secondary,#e2e8f0);cursor:pointer}.kgt-tr-st:hover{background:var(--hover-bg-strong,rgba(255,255,255,0.06))}.kgt-tr-st.held{background:rgba(241,95,34,.10);box-shadow:inset 2px 0 0 var(--teradata-orange,#F15F22)}.kgt-tr-st .d{width:6px;height:6px;border-radius:50%;margin-top:4px;background:var(--trust-pass,#34d399)}.kgt-tr-st.bad .d{background:var(--trust-danger,#f87171)}.kgt-tr-st .w{color:var(--text-muted,#94a3b8);font-family:var(--font-mono,'JetBrains Mono',monospace);font-size:9.5px}
.kgt-strip .chev{display:inline-block;width:12px;height:12px;vertical-align:-1px;margin-right:6px;transition:transform .2s}.kgt-strip .chev svg{width:12px;height:12px;display:block}.kgt-strip.open .chev{transform:rotate(180deg)}.kgt-strip .sql{display:inline}.kgt-strip.danger{border-color:rgba(248,113,113,.7)}
.kgt-root.inspect .kgt-strip{right:266px}
.kgt-strip .lbl{position:absolute;right:10px;top:-14px;font-size:8.5px;letter-spacing:.08em;color:var(--text-muted,#94a3b8);font-family:var(--font-primary,'Inter',sans-serif)}
.kgt-strip span.tk{padding:2px 4px;border-radius:4px}
.kgt-strip span.ent{color:#22c55e;background:rgba(34,197,94,.14)}.kgt-strip span.attr{color:#a3e635;background:rgba(163,230,53,.14)}.kgt-strip span.key{color:#fbbf24;background:rgba(251,191,36,.14)}.kgt-strip span.lit{color:#fbbf24;background:rgba(251,191,36,.10)}.kgt-strip span.bad{color:#f87171;background:rgba(248,113,113,.14);text-decoration:underline;text-decoration-thickness:2px}.kgt-strip span.kw{color:var(--text-secondary,#e2e8f0)}
.kgt-root.watch .kgt-strip{cursor:pointer}
.kgt-tip{font-family:var(--font-primary,'Inter',sans-serif);position:fixed;z-index:9999;max-width:300px;padding:9px 11px;border-radius:9px;background:var(--bg-overlay,rgba(38,40,46,0.98));border:1px solid var(--border-primary,rgba(255,255,255,0.1));box-shadow:0 10px 28px rgba(0,0,0,.45);color:var(--text-primary,#f8fafc);font-size:11px;line-height:1.45;pointer-events:none;opacity:0;transition:opacity .12s}
.kgt-tip .t{font-size:12px;font-weight:600;color:var(--text-primary,#f8fafc)}
.kgt-tip .s{font-family:var(--font-mono,monospace);font-size:9.5px;color:var(--text-muted,#94a3b8);margin-bottom:5px}
.kgt-tip .kv{display:flex;gap:10px;align-items:baseline;margin-top:3px}
.kgt-tip .k{flex:0 0 74px;font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted,#94a3b8)}
.kgt-tip .v{color:var(--text-secondary,#e2e8f0);min-width:0;overflow-wrap:anywhere}
.kgt-tip .v.mono{font-family:var(--font-mono,monospace);font-size:10.5px}
.kgt-tip .d{margin-top:6px;padding-top:6px;border-top:1px solid var(--border-secondary,rgba(255,255,255,.06));color:var(--text-muted,#94a3b8);font-size:10.5px}
.kgt-tip .sql{font-family:var(--font-mono,monospace);font-size:10.5px;white-space:pre-wrap;color:var(--text-secondary,#e2e8f0)}
.kgt-tip.pinned{pointer-events:auto;border-color:rgba(241,95,34,.55);box-shadow:0 10px 28px rgba(0,0,0,.55),0 0 0 1px rgba(241,95,34,.18)}
.kgt-tip.on{opacity:1}
.kgt-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:18% 14px 0;box-sizing:border-box;pointer-events:none}
.kgt-root.inspect .kgt-empty{right:268px}   /* the trail rail owns the right of the stage in Inspect: the card centres in the space that is left, never under the rail */
.kgt-empty-card{display:flex;flex-direction:column;align-items:center;gap:5px;max-width:min(420px,100%);box-sizing:border-box;padding:14px 22px;border-radius:12px;background:var(--card-bg,rgba(24,26,30,0.92));border:1px solid var(--border-primary,rgba(255,255,255,0.12));box-shadow:0 12px 36px rgba(0,0,0,.45);text-align:center}
.kgt-empty-main{font-size:15px;font-weight:600;letter-spacing:.01em;color:var(--text-primary,#f8fafc)}
.kgt-empty-sub{font-size:11.5px;color:var(--text-muted,#94a3b8)}.kgt-empty-sub:empty{display:none}
@keyframes kgt-breathe{0%,100%{opacity:.5}50%{opacity:1}}
@keyframes kgt-flow{to{stroke-dashoffset:-28}}
.kgt-flow{stroke-dasharray:8 6;animation:kgt-flow 1s linear infinite}
.kgt-breathe{animation:kgt-breathe 1.8s ease-in-out infinite}
@media (prefers-reduced-motion: reduce){.kgt-flow,.kgt-breathe,.kgt-dot.live,.kgt-tr.active .d{animation:none!important;stroke-dasharray:none!important}}
`;
    document.head.appendChild(st);
}

const BOX_W = 232, BOX_W_INSPECT = 276, HDR = 30, ROW = 21, ROW_ICON = 13;

export function mountTheatre(host, opts) {
    opts = opts || {};
    _injectCss();
    const d3 = window.d3;
    const rm = _reducedMotion();
    const root = document.createElement('div');
    root.className = `kgt-root ${opts.depth === 'inspect' ? 'inspect' : 'watch'}`;
    root.innerHTML = `
      <div class="kgt-stage"><svg xmlns="http://www.w3.org/2000/svg"></svg>
        <div class="kgt-empty"><div class="kgt-empty-card"><div class="kgt-empty-main">Waiting for the question…</div><div class="kgt-empty-sub"></div></div></div>
        <div class="kgt-rail"><h4>REASONING TRAIL</h4><div class="kgt-trail"></div></div>
        <div class="kgt-strip"><span class="lbl">STATEMENT UNDER TEST</span></div>
      </div>
      <div class="kgt-caption">
        <div class="kgt-cap-left"><span class="kgt-dot live"></span><div class="kgt-cap-text"><div class="kgt-cap-main">Waiting for the question…</div><div class="kgt-cap-sub"></div></div></div>
        <div class="kgt-ribbon"></div>
        <div class="kgt-actions">
          <div class="kgt-pills"><button class="kgt-pill" data-depth="watch">Watch</button><button class="kgt-pill" data-depth="inspect">Inspect</button></div>
          <button class="kgt-btn kgt-replay u-tip" data-tip="Replay this turn">${_htmlIcon('replay', 13)} <span class="kgt-replay-l">Replay</span></button>
          <button class="kgt-btn kgt-explain-btn u-tip" aria-pressed="false" data-tip="Explain the step on stage (or the one you click) in a line below">${_htmlIcon('info', 13)} Explain</button>
          <button class="kgt-btn kgt-key u-tip" aria-pressed="false" data-tip="Show a key to the marks on the stage">${_htmlIcon('legend', 13)} Key</button>
          <button class="kgt-btn kgt-graph u-tip" data-tip="Back to the graph">${_htmlIcon('graph', 13)} Graph</button>
        </div>
      </div>
      <div class="kgt-explain"></div>
      <div class="kgt-legend"></div>`;
    host.innerHTML = '';
    host.appendChild(root);
    const svgEl = root.querySelector('svg');
    const svg = d3 ? d3.select(svgEl) : null;
    const tip = document.createElement('div'); tip.className = 'kgt-tip'; document.body.appendChild(tip);

    // ── theme tokens ──
    const T = {
        ent: '#22c55e', attr: '#a3e635', table: '#0d9488', col: '#5eead4', fk: '#f59e0b', pk: '#fbbf24', domain: '#f97316',
        pass: _tok('--trust-pass', '#34d399'), warn: _tok('--trust-warn', '#fbbf24'), danger: _tok('--trust-danger', '#f87171'),
        orange: _tok('--teradata-orange', '#F15F22'), accent: '#6366f1', txt: _tok('--text-primary', '#f8fafc'),
        txt2: _tok('--text-secondary', '#e2e8f0'), muted: _tok('--text-muted', '#94a3b8'), border: _tok('--border-primary', 'rgba(255,255,255,0.1)'),
        card: _tok('--card-bg', 'rgba(24,26,30,0.92)'), bg: _tok('--bg-primary', '#141619'),
    };
    const mono = "var(--font-mono,'JetBrains Mono',monospace)";

    // ── state ──
    let depth = opts.depth === 'inspect' ? 'inspect' : 'watch';
    opts = Object.assign({}, opts || {});
    let reducer = createReducer();
    let S = reducer.state;   // `let`: reset() swaps in a fresh reducer and every closure reads the live one
    const layout = { pos: {}, areas: {}, w: 0, h: 0 };
    const ui = { rowsByEntity: {}, marks: [], userMoved: false, ended: false, timers: [], queue: [], playing: false, danger: false, lastSkin: '',
        performed: new Set(), performedSt: new Set(),   // the beats / statements the STAGE has performed (the reducer runs ahead of it)
        keepTimes: null, replaying: false };           // the live run's clock, kept across replays and scrubs of the same turn
    const pref = (k, fb) => { try { const v = localStorage.getItem(k); return v == null ? fb : v === '1'; } catch (_) { return fb; } };
    ui.legend = pref('kg_theatre_legend', false); ui.explain = pref('kg_theatre_explain', false);
    let _replaySource = null;   // the armed/reloaded story; declared HERE, above every reader — a `let` below a caller is the TDZ trap this file already paid for
    let gMain, gRegions, gLinks, gEntities, gThreads, gMarks, gLabels, gQuestion, gField, zoom;
    const field = { nodes: [], byName: {}, links: [], shown: false, gathered: false, transform: null };

    // ── measuring (canvas) ──
    const canvas = document.createElement('canvas'); const ctx = canvas.getContext('2d');
    function textW(s, size, mono_) { ctx.font = `${size}px ${mono_ ? 'JetBrains Mono, Menlo, monospace' : 'Inter, system-ui, sans-serif'}`; return ctx.measureText(String(s)).width; }
    function ellipsize(s, size, maxW, mono_) {
        let str = String(s); if (textW(str, size, mono_) <= maxW) return str;
        while (str.length > 1 && textW(str + '…', size, mono_) > maxW) str = str.slice(0, -1);
        return str + '…';
    }

    // ── helpers ──
    function showTip(x, y, text) {
        if (ui.tipPinned) return;   // a pinned card is not replaced by a hover
        // lines: the first is the title (a dotted name splits into leaf + home), `key: value`
        // lines become rows, a lone statement renders as code, anything else is prose
        const lines = String(text || '').split('\n').map(l => l.trim()).filter(Boolean);
        let html = '';
        if (lines.length === 1 && /^(select|with|insert|update|delete|merge)\b/i.test(lines[0])) html = `<div class="sql">${_esc(lines[0])}</div>`;
        else lines.forEach((l, i) => {
            const m = /^([a-z][a-z ]{1,22}):\s+(.+)$/i.exec(l);
            if (i === 0 && !m) { const parts = l.split('.'); html += parts.length > 1 ? `<div class="t">${_esc(parts.pop())}</div><div class="s">${_esc(parts.join('.'))}</div>` : `<div class="t">${_esc(l)}</div>`; }
            else if (m) html += `<div class="kv"><span class="k">${_esc(m[1])}</span><span class="v${/^[\w.]+(, [\w.]+)*$/.test(m[2]) ? ' mono' : ''}">${_esc(m[2])}</span></div>`;
            else html += `<div class="d">${_esc(l)}</div>`;
        });
        tip.innerHTML = html; tip.style.left = Math.min(x + 12, window.innerWidth - 320) + 'px'; tip.style.top = Math.max(8, y - 10) + 'px'; tip.classList.add('on');
    }
    function hideTip() { if (ui.tipPinned) return; tip.classList.remove('on'); }
    function later(fn, ms) { const t = setTimeout(fn, rm ? 0 : ms); ui.timers.push(t); return t; }

    // ── stage init ──
    function initStage() {
        if (!svg) return;
        svg.selectAll('*').remove();
        const defs = svg.append('defs');
        const glow = defs.append('filter').attr('id', 'kgt-glow').attr('x', '-40%').attr('y', '-40%').attr('width', '180%').attr('height', '180%');
        glow.append('feGaussianBlur').attr('stdDeviation', 6).attr('result', 'b');
        const fm = glow.append('feMerge'); fm.append('feMergeNode').attr('in', 'b'); fm.append('feMergeNode').attr('in', 'SourceGraphic');
        gMain = svg.append('g').attr('class', 'kgt-main');
        gField = gMain.append('g').attr('class', 'kgt-field');
        gRegions = gMain.append('g').attr('class', 'kgt-regions');
        gLinks = gMain.append('g').attr('class', 'kgt-links');
        gEntities = gMain.append('g').attr('class', 'kgt-entities');
        gMarks = gMain.append('g').attr('class', 'kgt-marks');
        gThreads = gMain.append('g').attr('class', 'kgt-threads');
        gLabels = gMain.append('g').attr('class', 'kgt-labels');
        gQuestion = svg.append('g').attr('class', 'kgt-question');
        zoom = d3.zoom().scaleExtent([0.2, 2.5]).on('zoom', (ev) => { gMain.attr('transform', ev.transform); if (ev.sourceEvent) ui.userMovedAt = Date.now(); if (ui.threadPositions) drawThreads(ui.threadPositions); });
        svg.call(zoom).on('dblclick.zoom', null);
    }
    function size() { const r = root.querySelector('.kgt-stage').getBoundingClientRect(); layout.w = Math.max(320, r.width); layout.h = Math.max(240, r.height); }
    // the trail rail owns the right of the stage in Inspect: reserve exactly its real extent (plus a gutter), never a constant
    function railReserve() {
        if (depth !== 'inspect') return 0;
        const rail = root.querySelector('.kgt-rail'), st = root.querySelector('.kgt-stage');
        if (rail && st && rail.getBoundingClientRect) { const rr = rail.getBoundingClientRect(), sr = st.getBoundingClientRect(); if (rr.width > 0 && sr.width > 0) return Math.max(0, sr.right - rr.left) + 8; }
        return 266;
    }

    // ── ER layout: deterministic and crossing-aware ──
    //   columns  = subject areas (the seeds' area first)
    //   rows     = relationship depth from the seeds (BFS over declared relationships)
    //   order    = barycenter of neighbours, iterated both ways (Sugiyama's crossing pass)
    //   no two boxes overlap by construction; the canvas grows when a row needs it and
    //   the camera fits what it needs to show.
    function foldedCount(ent) {
        const m = S.model; if (!ui.showFolded || !m || !m.omitted) return 0;
        return Number(m.omitted[ent.deployed] || m.omitted[ent.name] || 0);
    }
    function boxHeight(ent) {
        const rows = (ui.rowsByEntity[ent.name] || []).length;
        const facts = depth === 'inspect' && (ent.rows != null || ent.keyUnique != null);
        const folded = foldedCount(ent) ? 14 : 0;     // the "▸ N attributes folded" line sits under the box
        const shadow = depth === 'inspect' && ent.deployed ? 48 : 0;
        return HDR + (facts ? 14 : 0) + (rows ? rows * ROW + 8 : 0) + folded + shadow;
    }
    function computeLayout(model) {
        size();
        const names = Object.keys(model.entities);
        if (!names.length) return;
        const adj = {}; for (const n of names) adj[n] = new Set();
        for (const r of model.rels) if (adj[r.source] && adj[r.target]) { adj[r.source].add(r.target); adj[r.target].add(r.source); }
        for (const b of model.bridges) if (b.source && b.target && adj[b.source] && adj[b.target]) { adj[b.source].add(b.target); adj[b.target].add(b.source); }
        // depth from the seeds (multi-source BFS); unreachable → deepest row
        const seedSet = new Set(model.seeds.map(s => s.name).filter(n => model.entities[n]));
        for (const [n, tag] of Object.entries(model.provenance)) if (tag === 'seed' && model.entities[n]) seedSet.add(n);
        const roots = seedSet.size ? [...seedSet] : [names.slice().sort((a, b) => adj[b].size - adj[a].size)[0]];
        const depthOf = {}; const q = [];
        for (const r of roots) { depthOf[r] = 0; q.push(r); }
        while (q.length) { const n = q.shift(); for (const nb of adj[n]) if (depthOf[nb] === undefined) { depthOf[nb] = depthOf[n] + 1; q.push(nb); } }
        const maxD = Math.max(0, ...Object.values(depthOf));
        for (const n of names) if (depthOf[n] === undefined) depthOf[n] = maxD + 1;
        // areas → columns, seeds' area first
        const areas = model.areas.length ? model.areas : [null];
        const primary = roots.length && model.entities[roots[0]].area != null ? model.entities[roots[0]].area : areas[0];
        const ordered = [primary, ...areas.filter(a => a !== primary)];
        // rows per column
        const cols = ordered.map(a => ({ area: a, rows: {} }));
        for (const n of names) {
            const e = model.entities[n]; const ci = Math.max(0, ordered.indexOf(e.area == null ? ordered[0] : e.area));
            const d = depthOf[n]; (cols[ci].rows[d] = cols[ci].rows[d] || []).push(n);
        }
        // barycenter ordering within each row, iterated down then up
        const pos1 = {};   // provisional x index per node for barycenters
        for (const c of cols) for (const [d, row] of Object.entries(c.rows)) row.forEach((n, i) => { pos1[n] = i; });
        const bary = (n, rowSet) => { const nb = [...adj[n]].filter(x => !rowSet.has(x) && pos1[x] !== undefined); return nb.length ? nb.reduce((a, x) => a + pos1[x], 0) / nb.length : pos1[n]; };
        for (let pass = 0; pass < 4; pass++) {
            for (const c of cols) {
                const depths = Object.keys(c.rows).map(Number).sort((a, b) => pass % 2 ? b - a : a - b);
                for (const d of depths) {
                    const row = c.rows[d]; const rowSet = new Set(row);
                    row.sort((a, b) => bary(a, rowSet) - bary(b, rowSet) || a.localeCompare(b));
                    row.forEach((n, i) => { pos1[n] = i; });
                }
            }
        }
        // geometry
        const GAP_X = 56, GAP_Y = 64, PAD = 40; const BW = depth === 'inspect' ? BOX_W_INSPECT : BOX_W;
        let x0 = PAD; layout.areas = {};
        const rowPitch = {};   // depth → pitch across all columns (rows align across areas)
        for (const c of cols) for (const [d, row] of Object.entries(c.rows)) rowPitch[d] = Math.max(rowPitch[d] || 0, ...row.map(n => boxHeight(model.entities[n])));
        const depthsAll = Object.keys(rowPitch).map(Number).sort((a, b) => a - b);
        // FIT IS MANDATORY: everything the story shows must be on screen. Two candidate
        // arrangements — subject areas side by side (rows unwrapped) and stacked (one area under
        // the other, long rows wrapped) — and the one that fits the stage at the LARGER zoom wins.
        const vwEff = Math.max(320, layout.w - railReserve()), vhEff = Math.max(200, layout.h - 110);
        const placeSide = () => {
            const pos = {}, areas = {}; let x = PAD;
            const colWs = cols.map(c => Math.max(Math.max(1, ...Object.values(c.rows).map(r => r.length)) * (BW + GAP_X) - GAP_X + 2 * PAD, 300));
            const yOf = {}; let yy = PAD + 62;   // room above the first row for the question threads' labels, inside the frame
            for (const d of depthsAll) { yOf[d] = yy; yy += rowPitch[d] + GAP_Y; }
            cols.forEach((c, ci) => {
                const colW = colWs[ci];
                for (const [d, row] of Object.entries(c.rows)) {
                    const total = row.length * (BW + GAP_X) - GAP_X, startX = x + (colW - total) / 2;
                    row.forEach((n, k) => { pos[n] = { x: startX + k * (BW + GAP_X), y: yOf[d], w: BW, h: boxHeight(model.entities[n]) }; });
                }
                areas[c.area == null ? '_' : c.area] = { x, w: colW, cx: x + colW / 2, y: PAD, h: yy - GAP_Y + 44 - PAD };
                x += colW + 24;
            });
            return { pos, areas, vw: x + PAD, vh: yy + PAD + 60, stacked: false };
        };
        const placeStack = (perLine) => {
            const pos = {}, areas = {}; let yBase = PAD;
            const colW = Math.max(300, Math.min(perLine, Math.max(1, ...cols.flatMap(c => Object.values(c.rows).map(r => r.length)))) * (BW + GAP_X) - GAP_X + 2 * PAD);
            for (const c of cols) {
                const depths = Object.keys(c.rows).map(Number).sort((a, b) => a - b);
                let yy = yBase + 62;
                for (const d of depths) {
                    const row = c.rows[d];
                    for (let k = 0; k < row.length; k += perLine) {
                        const line = row.slice(k, k + perLine); const pitch = Math.max(...line.map(n => boxHeight(model.entities[n])));
                        const total = line.length * (BW + GAP_X) - GAP_X, startX = PAD + (colW - total) / 2;
                        line.forEach((n, i2) => { pos[n] = { x: startX + i2 * (BW + GAP_X), y: yy, w: BW, h: boxHeight(model.entities[n]) }; });
                        yy += pitch + GAP_Y;
                    }
                }
                const h = yy - GAP_Y + 44 - yBase;
                areas[c.area == null ? '_' : c.area] = { x: PAD, w: colW, cx: PAD + colW / 2, y: yBase, h };
                yBase += h + 24;
            }
            return { pos, areas, vw: 2 * PAD + colW, vh: yBase + PAD + 60, stacked: true };
        };
        const fitOf = (L) => Math.min(1, vwEff / L.vw, vhEff / L.vh);
        const perLineFor = (k) => Math.max(1, Math.floor((vwEff / k - 2 * PAD + GAP_X) / (BW + GAP_X)));
        const side = placeSide();
        const candidates = [side];
        for (const k of [1, 0.85, 0.7]) { const L = placeStack(perLineFor(k)); if (!candidates.some(c => c.vw === L.vw && c.vh === L.vh)) candidates.push(L); }
        const best = candidates.reduce((a, b) => (fitOf(b) > fitOf(a) + 0.02 ? b : a), side);   // a clear win only: the side-by-side reading is the default
        layout.pos = best.pos; layout.areas = best.areas; layout.vw = best.vw; layout.vh = best.vh; layout.stacked = best.stacked;
        // anchor the arrangement on the field: the boxes gather around where the seeds were
        if (field.nodes.length && !layout.anchored) {
            // the arrangement takes the field's centre, so the gather is a short, radial journey
            // and the camera's fit of the whole arrangement stays where the viewer was looking
            const fx = field.nodes.reduce((a, n) => a + n.fx0, 0) / field.nodes.length, fy = field.nodes.reduce((a, n) => a + n.fy0, 0) / field.nodes.length;
            const ps = Object.values(layout.pos);
            const lx = ps.reduce((a, p) => a + p.x + p.w / 2, 0) / ps.length, ly = ps.reduce((a, p) => a + p.y + p.h / 2, 0) / ps.length;
            layout.dx = fx - lx; layout.dy = fy - ly;
            layout.anchored = true;
        }
        if (layout.dx || layout.dy) {
            for (const p of Object.values(layout.pos)) { p.x += layout.dx; p.y += layout.dy; }
            for (const a of Object.values(layout.areas)) { a.x += layout.dx; a.cx += layout.dx; a.y += layout.dy; }
            layout.vw += layout.dx; layout.vh += layout.dy;
        }
    }

    // ── edge routing: straight when clear, a smooth bend around any box in the way ──
    function anchor(a, b) {
        const ca = { x: a.x + a.w / 2, y: a.y + a.h / 2 }, cb = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
        const dx = cb.x - ca.x, dy = cb.y - ca.y;
        if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) return ca;
        const s = Math.min((a.w / 2) / Math.abs(dx || 1e-6), (a.h / 2) / Math.abs(dy || 1e-6));
        return { x: ca.x + dx * s, y: ca.y + dy * s };
    }
    function _segHitsRect(A, B, r, pad) {
        const x0 = r.x - pad, y0 = r.y - pad, x1 = r.x + r.w + pad, y1 = r.y + r.h + pad;
        // Liang–Barsky clipping
        let t0 = 0, t1 = 1; const dx = B.x - A.x, dy = B.y - A.y;
        const clip = (p, q) => { if (p === 0) return q >= 0; const t = q / p; if (p < 0) { if (t > t1) return false; if (t > t0) t0 = t; } else { if (t < t0) return false; if (t < t1) t1 = t; } return true; };
        return clip(-dx, A.x - x0) && clip(dx, x1 - A.x) && clip(-dy, A.y - y0) && clip(dy, y1 - A.y) && t0 < t1;
    }
    function route(aName, bName) {
        const _ext = (p) => (p && p.ext != null ? { ...p, h: p.ext } : p);
        const pa = _ext(layout.pos[aName]), pb = _ext(layout.pos[bName]);
        const A = anchor(pa, pb), B = anchor(pb, pa);
        const others = Object.entries(layout.pos).filter(([n]) => n !== aName && n !== bName).map(([, p]) => p);
        const blocked = others.filter(o => _segHitsRect(A, B, o, 6));
        if (!blocked.length) return { d: `M${A.x},${A.y} L${B.x},${B.y}`, A, B, tA: { x: B.x - A.x, y: B.y - A.y }, tB: { x: A.x - B.x, y: A.y - B.y }, mid: { x: (A.x + B.x) / 2, y: (A.y + B.y) / 2 }, bent: false };
        // bend to the side that clears the obstacles with the smaller detour
        const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1;
        const nx = -dy / len, ny = dx / len;
        const clearance = (sign) => Math.max(...blocked.map(o => {
            const cx = o.x + o.w / 2, cy = o.y + o.h / 2;
            const off = ((cx - A.x) * nx + (cy - A.y) * ny) * sign;   // how far the box centre sits on this side
            return Math.max(0, -off) + Math.hypot(o.w, o.h) / 2 + 30;
        }));
        const cPlus = clearance(1), cMinus = clearance(-1);
        const sign = cPlus <= cMinus ? 1 : -1, off = Math.min(cPlus <= cMinus ? cPlus : cMinus, 260);
        const c1 = { x: A.x + dx * 0.3 + nx * off * sign, y: A.y + dy * 0.3 + ny * off * sign };
        const c2 = { x: A.x + dx * 0.7 + nx * off * sign, y: A.y + dy * 0.7 + ny * off * sign };
        const mid = { x: 0.125 * A.x + 0.375 * c1.x + 0.375 * c2.x + 0.125 * B.x, y: 0.125 * A.y + 0.375 * c1.y + 0.375 * c2.y + 0.125 * B.y };
        return { d: `M${A.x},${A.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${B.x},${B.y}`, A, B, tA: { x: c1.x - A.x, y: c1.y - A.y }, tB: { x: c2.x - B.x, y: c2.y - B.y }, mid, bent: true };
    }

    // ── rendering primitives ──
    const rowsOf = (name) => ui.rowsByEntity[name] || [];
    function entityState(name) {
        const m = S.model; if (!m) return 'dim';
        if (ui.lit.has(name)) return 'lit';
        if (ui.solid.has(name)) return 'solid';
        if (ui.known.has(name)) return 'known';
        return 'dim';
    }
    ui.lit = new Set(); ui.solid = new Set(); ui.known = new Set(); ui.rings = new Set(); ui.cooling = new Set(); ui.drafts = new Map();   // PROP-008: name → 'draft' | 'proposed' (a dashed box)
    ui.used = new Set(); ui.executing = false;   // from the first statement on, the context the agent does not USE recedes
    ui.markRects = []; ui.labelRects = []; ui.threadRects = []; ui.markIntents = []; ui.markSig = null;   // what the placers have already put on the stage: marks + threads live for the turn, labels for this repaint
    function boxRects(pad) { return Object.values(layout.pos).map(p => ({ x: p.x - pad, y: p.y - pad, w: p.w + 2 * pad, h: p.h + 2 * pad })); }

    function renderRegions() {
        const m = S.model; if (!m || !gRegions) return;
        const areas = Object.entries(layout.areas);
        const sel = gRegions.selectAll('g.kgt-area').data(areas, d => d[0]);
        const ent = sel.enter().append('g').attr('class', 'kgt-area');
        ent.append('rect').attr('rx', 22).attr('fill', T.domain).attr('fill-opacity', 0.04).attr('stroke', T.domain).attr('stroke-opacity', 0.3);
        ent.append('text').attr('font-size', 9).attr('fill', T.domain).attr('letter-spacing', '.12em').attr('font-weight', 600);
        const all = ent.merge(sel);
        all.select('rect').attr('x', d => d[1].x).attr('y', d => d[1].y).attr('width', d => d[1].w).attr('height', d => d[1].h);
        all.select('text').attr('x', d => d[1].x + 16).attr('y', d => d[1].y + d[1].h - 12).text(d => d[0] === '_' ? (m.kg && m.kg.name ? String(m.kg.name).toUpperCase() : 'MODEL') : `${String(d[0]).toUpperCase()} · SUBJECT AREA`);
        // a subject area the question reached by name (a seeded domain) is marked on its frame —
        // the top seed of a turn must not light nothing
        const seededAreas = new Map();
        if (ui.performed && ui.performed.has('seed')) for (const s of (S.seeds || [])) if (s.type === 'domain') seededAreas.set(_leaf(s.name), s);
        all.select('rect').attr('stroke', d => seededAreas.has(String(d[0]).toLowerCase()) ? T.orange : T.domain).attr('stroke-opacity', d => seededAreas.has(String(d[0]).toLowerCase()) ? 0.85 : 0.3);
        all.select('text').attr('fill', d => seededAreas.has(String(d[0]).toLowerCase()) ? T.orange : T.domain)
            .text(d => { const sd = seededAreas.get(String(d[0]).toLowerCase()); const base = d[0] === '_' ? (m.kg && m.kg.name ? String(m.kg.name).toUpperCase() : 'MODEL') : `${String(d[0]).toUpperCase()} \u00b7 SUBJECT AREA`; return sd ? `${base} \u00b7 SEEDED BY \u201c${(sd.terms || []).join(', ')}\u201d` : base; });
        all.attr('opacity', d => (ui.regionsSolid || seededAreas.has(String(d[0]).toLowerCase())) ? 1 : 0.5);
        sel.exit().remove();
    }

    function anchor(a, b) {
        // point on box a's border toward box b's centre
        const ca = { x: a.x + a.w / 2, y: a.y + a.h / 2 }, cb = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
        const dx = cb.x - ca.x, dy = cb.y - ca.y;
        if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) return ca;
        const sx = (a.w / 2) / Math.abs(dx || 1e-6), sy = (a.h / 2) / Math.abs(dy || 1e-6);
        const s = Math.min(sx, sy);
        return { x: ca.x + dx * s, y: ca.y + dy * s };
    }
    function relState(rel) { return ui.relStates.get(rel) || 'dim'; }
    ui.relStates = new Map(); ui.routes = new Map();

    function renderLinks() {
        const m = S.model; if (!m || !gLinks) return;
        const data = [...m.rels.map(r => ({ r, bridge: false })), ...m.bridges.filter(b => b.source && b.target && m.entities[b.source] && m.entities[b.target]).map(r => ({ r, bridge: true }))]
            .filter(d => layout.pos[d.r.source] && layout.pos[d.r.target]);
        const sel = gLinks.selectAll('g.kgt-rel').data(data, d => `${d.r.source}|${d.r.target}|${d.r.type}`);
        const ent = sel.enter().append('g').attr('class', 'kgt-rel').attr('opacity', 0);
        ent.append('path').attr('class', 'halo').attr('fill', 'none').attr('stroke-width', 8).attr('stroke-opacity', 0);
        ent.append('path').attr('class', 'main').attr('fill', 'none');
        ent.append('path').attr('class', 'foot').attr('fill', 'none');
        ent.append('line').attr('class', 'bar');
        const all = ent.merge(sel);
        all.each(function (d) {
            const g = d3.select(this);
            const rt = route(d.r.source, d.r.target);
            ui.routes.set(d.r, rt);
            const st = relState(d.r);
            const col = { dim: T.muted, known: T.muted, solid: T.fk, lit: T.fk, pass: T.pass, danger: T.danger }[st] || T.muted;
            const op = (ui.executing && (st === 'solid' || st === 'known')) ? 0.3 : ({ dim: 0.18, known: 0.45, solid: 0.9, lit: 1, pass: 1, danger: 1 }[st] || 0.2);   // an unused relationship recedes with its tables
            const strong = st === 'lit' || st === 'pass' || st === 'danger';
            const sw = strong ? 2.2 : 1.4;
            g.transition().duration(rm ? 0 : 320).attr('opacity', op);
            g.select('path.halo').attr('d', rt.d).attr('stroke', col).attr('stroke-opacity', st === 'lit' ? 0.18 : 0);
            const main = g.select('path.main').attr('d', rt.d).attr('stroke', d.bridge ? (st === 'danger' ? T.danger : st === 'pass' ? T.pass : T.muted) : col)
                .attr('stroke-width', d.bridge ? 1.3 : sw)
                .attr('stroke-dasharray', d.bridge ? '3 5' : (d.r.declared ? null : '5 4'))
                .classed('kgt-flow', !d.bridge && st === 'lit');
            if (!d.bridge && d.r.declared && st === 'lit') main.attr('stroke-dasharray', '8 6');
            if (d.bridge) { g.select('path.foot').attr('d', ''); g.select('line.bar').attr('opacity', 0); return; }
            g.select('line.bar').attr('opacity', 1);
            // crow's foot at the MANY end (target of a 1:N, source of an N:1), along the curve's tangent
            const manyAtTarget = String(d.r.cardinality || '1:N') !== 'N:1';
            const P = manyAtTarget ? rt.B : rt.A, tP = manyAtTarget ? rt.tB : rt.tA;
            const Q = manyAtTarget ? rt.A : rt.B, tQ = manyAtTarget ? rt.tA : rt.tB;
            const ang = Math.atan2(tP.y, tP.x), s9 = 10;
            // the foot's vertex sits on the entity's border (1px out) and its tines run along the
            // edge, AWAY from the box — a crow's foot that reaches into the entity is a bug
            const V = { x: P.x + Math.cos(ang) * 1, y: P.y + Math.sin(ang) * 1 };
            const l1 = { x: V.x + Math.cos(ang + 0.6) * s9, y: V.y + Math.sin(ang + 0.6) * s9 }, l2 = { x: V.x + Math.cos(ang - 0.6) * s9, y: V.y + Math.sin(ang - 0.6) * s9 };
            g.select('path.foot').attr('d', d.r.type === 'is_a' ? '' : `M${l1.x},${l1.y} L${V.x},${V.y} L${l2.x},${l2.y}`).attr('stroke', col).attr('stroke-width', sw);
            const angQ = Math.atan2(tQ.y, tQ.x); const bx = Q.x + Math.cos(angQ) * 6, by = Q.y + Math.sin(angQ) * 6;
            g.select('line.bar').attr('x1', bx + Math.cos(angQ + Math.PI / 2) * 7).attr('y1', by + Math.sin(angQ + Math.PI / 2) * 7).attr('x2', bx - Math.cos(angQ + Math.PI / 2) * 7).attr('y2', by - Math.sin(angQ + Math.PI / 2) * 7).attr('stroke', col).attr('stroke-width', sw);
        });
        sel.exit().remove();
    }

    function renderEntities() {
        const m = S.model; if (!m || !gEntities) return;
        const data = Object.values(m.entities).filter(e => layout.pos[e.name]);
        const sel = gEntities.selectAll('g.kgt-ent').data(data, d => d.name);
        const ent = sel.enter().append('g').attr('class', 'kgt-ent').style('cursor', 'default').attr('transform', d => { const p = layout.pos[d.name]; return `translate(${p.x},${p.y})`; }).attr('opacity', 0);
        ent.append('rect').attr('class', 'glow').attr('rx', 12).attr('fill', T.ent).attr('opacity', 0);
        ent.append('rect').attr('class', 'ring').attr('rx', 15).attr('fill', 'none').attr('stroke', T.orange).attr('stroke-width', 1.5).attr('stroke-dasharray', '3 4').attr('opacity', 0);
        ent.append('rect').attr('class', 'box').attr('rx', 8).attr('stroke-width', 1.5);
        ent.append('circle').attr('class', 'dot').attr('r', 4.5);
        ent.append('text').attr('class', 'name').attr('font-size', 12).attr('font-weight', 600).attr('fill', T.txt);
        ent.append('text').attr('class', 'kind').attr('font-size', 8).attr('letter-spacing', '.06em').attr('text-anchor', 'end');
        ent.append('text').attr('class', 'facts').attr('font-size', 9).attr('fill', T.muted).attr('font-family', mono);
        ent.append('line').attr('class', 'sep').attr('stroke', T.border);
        ent.append('g').attr('class', 'rows');
        ent.append('text').attr('class', 'folded').attr('font-size', 9.5).attr('fill', T.muted);
        ent.append('g').attr('class', 'shadow');
        const all = ent.merge(sel);
        all.each(function (e) {
            const g = d3.select(this); const p = layout.pos[e.name]; const st = entityState(e.name);
            const col = e.type === 'data_product' ? '#eab308' : e.type === 'product_bridge' ? '#f43f5e' : e.type === 'metric' ? '#06b6d4' : T.ent;
            // From Execute on, the frame's subject is the STATEMENT: the injected context it does not
            // read recedes to a background reading, the tables it reads (and their joins) stay forward.
            // Inject's frame — everything solid — is the context as the agent received it.
            const op = (ui.executing && st === 'solid' && !ui.used.has(e.name)) ? 0.45 : { dim: 0.22, known: 0.55, solid: 1, lit: 1 }[st];
            const hasFacts = depth === 'inspect' && (e.rows != null || e.keyUnique != null);
            const hdr = HDR + (hasFacts ? 14 : 0);
            const rows = rowsOf(e.name);
            const h = hdr + (rows.length ? rows.length * ROW + 8 : 0);
            p.h = h;
            // what a relationship line attaches to: the box as DRAWN, plus the deployed-table chip
            // only while that chip is shown. The layout reserves the chip's room for every box;
            // anchored on that reservation, lines ended in mid-air under a box that shows none.
            p.ext = h + ((depth === 'inspect' && e.deployed && (st === 'lit' || st === 'solid')) ? 48 + (foldedCount(e) ? 14 : 0) : 0);
            g.transition().duration(rm ? 0 : 320).attr('transform', `translate(${p.x},${p.y})`).attr('opacity', op);
            g.select('rect.glow').attr('x', -6).attr('y', -6).attr('width', p.w + 12).attr('height', h + 12).attr('opacity', st === 'lit' ? 0.10 : 0);
            g.select('rect.ring').attr('x', -11).attr('y', -11).attr('width', p.w + 22).attr('height', h + 22).attr('opacity', ui.rings.has(e.name) ? 1 : 0).classed('kgt-breathe', ui.rings.has(e.name));
            const draftKind = ui.drafts.get(e.name) || null;
            g.select('rect.box').attr('width', p.w).attr('height', h).attr('fill', ui.cooling.has(e.name) ? 'transparent' : (draftKind ? 'transparent' : col + '30')).attr('stroke', ui.cooling.has(e.name) ? col + '55' : col).attr('stroke-dasharray', draftKind ? '6 4' : (ui.cooling.has(e.name) ? '3 3' : null));
            g.select('circle.dot').attr('cx', 14).attr('cy', 15).attr('fill', col);
            g.select('text.name').attr('x', 26).attr('y', 19).text(ellipsize(e.label, 12, p.w - 26 - 58));
            g.select('text.kind').attr('x', p.w - 10).attr('y', 19).attr('fill', col).text(draftKind ? draftKind.toUpperCase() : e.type === 'data_product' ? 'PRODUCT' : e.type === 'product_bridge' ? 'BRIDGE' : e.type === 'metric' ? 'METRIC' : 'ENTITY');
            g.select('text.facts').attr('x', 26).attr('y', 35).text(hasFacts ? ellipsize([e.rows != null ? `${Number(e.rows).toLocaleString()} ${Number(e.rows) === 1 ? 'row' : 'rows'}` : null, e.keyUnique === true ? 'key verified unique' : e.keyUnique === false ? 'key NOT unique' : null].filter(Boolean).join(' · '), 9, p.w - 36, true) : '');
            g.select('line.sep').attr('x1', 8).attr('y1', hdr).attr('x2', p.w - 8).attr('y2', hdr).attr('opacity', rows.length ? 1 : 0);
            // rows
            const rs = g.select('g.rows').selectAll('g.row').data(rows, r => r.name);
            const re = rs.enter().append('g').attr('class', 'row').attr('opacity', 0);
            re.append('rect').attr('rx', 4).attr('height', ROW - 2).attr('x', 6);
            re.append('g').attr('class', 'ic');
            re.append('text').attr('class', 'nm').attr('font-size', 11).attr('font-family', mono);
            re.append('text').attr('class', 'dt').attr('font-size', 9.5).attr('font-family', mono).attr('text-anchor', 'end');
            const ra = re.merge(rs);
            ra.each(function (r, i) {
                const rg = d3.select(this); const ry = hdr + 4 + ROW * i;
                const rc = { seed: T.orange, key: T.pk, filter: T.attr, dimension: T.attr, measure: T.attr, projected: T.muted, absent: T.danger, danger: T.danger }[r.state === 'danger' ? 'danger' : r.role] || T.muted;
                const strong = r.role !== 'projected' || r.state === 'danger';
                rg.attr('transform', `translate(0,${ry})`);
                rg.select('rect').attr('y', 1).attr('width', p.w - 12).attr('fill', rc).attr('opacity', strong ? 0.10 : 0);
                rg.select('g.ic').html(_svgIcon(ROLE_ICON[r.role] || 'projected', 11, 4, ROW_ICON, strong ? rc : rc + '99'));
                const detail = depth === 'inspect' ? (r.detailInspect || r.detail || '') : (r.detail || '');
                const rowW = p.w - 40, nameMax = Math.min(textW(r.name, 11, true), rowW * 0.72);
                const detW = detail ? Math.min(textW(detail, 9.5, true), rowW - nameMax - 10) : 0;
                rg.select('text.nm').attr('x', 30).attr('y', 15).attr('fill', strong ? T.txt2 : T.muted).attr('text-decoration', r.role === 'absent' ? 'line-through' : null).text(ellipsize(r.name, 11, nameMax, true));
                rg.select('text.dt').attr('x', p.w - 10).attr('y', 15).attr('fill', rc).text(detail ? ellipsize(detail, 9.5, detW, true) : '');
                rg.style('cursor', 'pointer').on('mousemove', (ev) => showTip(ev.clientX, ev.clientY, r.tip || `${r.name}${detail ? '\n' + detail : ''}`)).on('mouseleave', hideTip).on('click', (ev) => { ev.stopPropagation(); pinTip(ev.clientX, ev.clientY, rowWhy(e, r)); });
            });
            ra.transition().duration(rm ? 0 : 240).attr('opacity', 1);
            rs.exit().transition().duration(rm ? 0 : 160).attr('opacity', 0).remove();
            // folded count
            const folded = foldedCount(e);
            g.select('text.folded').attr('x', 6).attr('y', h + 14).text(folded ? `▸ ${folded} attributes folded` : '').attr('opacity', folded ? 0.85 : 0);
            // physical plane (inspect) — BELOW the folded line when there is one: at h+17 the
            // IMPLEMENTS label sat on the folded line (they only ever met on a replay, because
            // live playback had skipped the inject op's flags — see the 'inject' op).
            const sh = g.select('g.shadow');
            if (depth === 'inspect' && e.deployed && (st === 'lit' || st === 'solid')) {
                const dy = h + 20 + (folded ? 14 : 0);
                sh.html(`<line x1="30" y1="${h}" x2="30" y2="${dy - 2}" stroke="${T.table}" stroke-dasharray="2 3"/><text x="38" y="${dy - 3}" font-size="8" fill="${T.table}" letter-spacing=".06em">IMPLEMENTS</text><rect x="18" y="${dy}" width="${p.w - 36}" height="24" rx="6" fill="${T.table}22" stroke="${T.table}" stroke-dasharray="4 3"/><circle cx="31" cy="${dy + 12}" r="3.5" fill="${T.table}"/><text x="42" y="${dy + 16}" font-size="10" fill="${T.col}" font-family="${mono}">${_esc(ellipsize(e.deployed, 10, p.w - 70, true))}</text>`).attr('opacity', 0.85);
            } else sh.html('');
            g.on('mousemove', (ev) => { if (ev.target.closest('g.row')) return; showTip(ev.clientX, ev.clientY, [e.name, e.deployed ? `deployed: ${e.deployed}` : null, e.area ? `subject area: ${e.area}` : null, e.keys.length ? `key: ${e.keys.join(', ')}` : null, e.provenance ? `reached by: ${e.provenance.replace('_', ' ')}` : null, e.description ? e.description.slice(0, 220) : null].filter(Boolean).join('\n')); }).on('mouseleave', hideTip);
        });
        sel.exit().remove();
    }

    // relationship labels through the label layer (cardinality + key), never overlapping
    function renderLabels() {
        const m = S.model; if (!m || !gLabels) return;
        gLabels.selectAll('*').remove();
        ui.labelRects = [];
        const occupied = boxRects(4).concat(ui.markRects.filter(r => !r.frame), ui.threadRects);
        const labels = [];
        for (const r of m.rels) {
            const pa = layout.pos[r.source], pb = layout.pos[r.target]; if (!pa || !pb) continue;
            const st = relState(r); if (st === 'dim') continue;
            const rt = ui.routes.get(r) || route(r.source, r.target); const A = rt.A, B = rt.B;
            const card = r.cardinality ? String(r.cardinality).replace(':', ' : ') : null;
            const key = (r.fromCols.length ? r.fromCols : r.toCols).join(' · ');
            const text = [card, (st === 'lit' || st === 'pass' || st === 'danger') && key ? key : null].filter(Boolean);
            if (!text.length) continue;
            const w = Math.max(...text.map(t => textW(t, 9.5, true))) + 8, h = text.length * 12 + 4;
            const cands = [0.32, 0.5, 0.68, 0.2, 0.8].flatMap(t => {
                const x = rt.bent ? rt.mid.x + (t - 0.5) * (B.x - A.x) * 0.6 : A.x + t * (B.x - A.x), y = rt.bent ? rt.mid.y + (t - 0.5) * (B.y - A.y) * 0.6 : A.y + t * (B.y - A.y);
                const nx = -(B.y - A.y), ny = (B.x - A.x); const len = Math.hypot(nx, ny) || 1;
                return [{ x: x + (nx / len) * 10 - (nx < 0 ? w : 0), y: y + (ny / len) * 10 - h / 2 }, { x: x - (nx / len) * 10 - (nx > 0 ? w : 0), y: y - (ny / len) * 10 - h / 2 }];
            });
            labels.push({ id: `${r.source}|${r.target}`, w, h, candidates: cands, priority: st === 'lit' ? 3 : st === 'danger' ? 4 : 1, text, color: st === 'danger' ? T.danger : st === 'pass' ? T.pass : T.fk, state: st });
        }
        const placed = placeLabels(labels, occupied, null);
        for (const l of labels) {
            const p = placed[l.id]; if (!p) continue;
            ui.labelRects.push({ x: p.x, y: p.y, w: l.w, h: l.h });
            const g = gLabels.append('g').attr('transform', `translate(${p.x},${p.y})`).attr('opacity', l.state === 'known' ? 0.55 : 1);
            l.text.forEach((t, i) => g.append('text').attr('x', 4).attr('y', 11 + i * 12).attr('font-size', 9.5).attr('font-family', mono).attr('fill', i === 0 ? l.color : T.pk).text(t));
        }
    }

    // question band + threads
    function renderQuestion(threads) {
        if (!gQuestion) return;
        gQuestion.selectAll('*').remove();
        const q = S.question; const terms = new Set((S.terms || []).map(t => String(t).toLowerCase()));
        if (!q) return;
        const words = String(q).split(/\s+/).filter(Boolean);
        let x = 24, y = 26; const positions = [];
        gQuestion.append('text').attr('x', 24).attr('y', 14).attr('font-size', 9).attr('fill', T.muted).attr('letter-spacing', '.12em').text('THE QUESTION');
        const maxW = layout.w - 48;
        for (const wd of words) {
            const clean = wd.toLowerCase().replace(/[^a-z0-9_]/g, '');
            const hits = threads ? (threads.all ? threads.all(clean) : [threads.lookup ? threads.lookup(clean) : threads.byTerm[clean]].filter(Boolean)) : [];
            const hit = hits[0] || null;
            const w = textW(wd, 14) + (hit ? 16 : 4);
            if (x + w > maxW && x > 24) { x = 24; y += 32; }   // wrap: every word of the question stays on stage
            if (hit) {
                gQuestion.append('rect').attr('x', x - 6).attr('y', y).attr('width', w).attr('height', 26).attr('rx', 13).attr('fill', hit.channel === 'semantic' ? T.accent : T.orange).attr('opacity', 0.16).attr('data-term', terms.has(clean) ? 1 : 0);
                gQuestion.append('rect').attr('x', x - 6).attr('y', y).attr('width', w).attr('height', 26).attr('rx', 13).attr('fill', 'none').attr('stroke', hit.channel === 'semantic' ? T.accent : T.orange).attr('stroke-opacity', 0.6).attr('data-term', terms.has(clean) ? 1 : 0);
            }
            gQuestion.append('text').attr('x', x + (hit ? 2 : 0)).attr('y', y + 18).attr('font-size', 14).attr('font-weight', hit ? 500 : 400).attr('fill', hit ? T.txt : T.muted).attr('opacity', hit ? 1 : 0.6).attr('data-term', terms.has(clean) ? 1 : 0).text(wd);
            positions.push({ term: clean, x: x + w / 2 - 3, y: y + 26, hit, hits });
            x += w + 8;
        }
        return positions;
    }

    function drawThreads(positions) {
        if (!gThreads || !positions) return;
        ui.threadPositions = positions;                       // redrawn on every repaint and zoom tick: the ends never drift
        const animate = !rm && !ui.threadsDrawn; ui.threadsDrawn = true;
        gThreads.selectAll('*').remove();
        ui.threadRects = [];
        const tr = d3.zoomTransform(svgEl);
        const labels = []; const seen = new Map();
        for (const pos0 of positions.flatMap(p => (p.hits && p.hits.length ? p.hits : [p.hit]).filter(Boolean).map(h => ({ ...p, hit: h })))) {
            const pos = pos0;
            const target = layout.pos[pos.hit.name]; const fn = !target && !field.gathered ? field.byName[pos.hit.name] : null;
            if (!target && !fn) continue;
            // question lives in screen space (gQuestion is outside gMain); convert to main coords
            const sx = (pos.x - tr.x) / tr.k, sy = (pos.y - tr.y) / tr.k;
            const tx = target ? target.x + 24 : fn.fx0, ty = target ? target.y : fn.fy0 - 8;
            const col = pos.hit.channel === 'semantic' ? T.accent : T.orange;
            const path = gThreads.append('path').attr('d', `M${sx},${sy} C${sx},${(sy + ty) / 2} ${tx},${(sy + ty) / 2} ${tx},${ty}`).attr('fill', 'none').attr('stroke', col).attr('stroke-width', 1.5).attr('opacity', 0.9);
            const _back = ui.executing && !!target && !ui.used.has(pos.hit.name);
            if (_back) path.attr('opacity', 0.16);
            if (pos.hit.channel === 'semantic') path.attr('stroke-dasharray', '4 4');
            if (animate) { const L = path.node().getTotalLength(); path.attr('stroke-dasharray', pos.hit.channel === 'semantic' ? '4 4' : `${L}`).attr('stroke-dashoffset', L).transition().duration(700).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0).on('end', function () { if (pos.hit.channel !== 'semantic') d3.select(this).attr('stroke-dasharray', null); }); }
            gThreads.append('circle').attr('cx', tx).attr('cy', ty).attr('r', 3.5).attr('fill', col).attr('opacity', _back ? 0.25 : 1);
            for (let t = 0.1; t < 0.6; t += 0.12) { const u = 1 - t; const bx = u * u * u * sx + 3 * u * u * t * sx + 3 * u * t * t * tx + t * t * t * tx, by = u * u * u * sy + 3 * u * u * t * (sy + ty) / 2 + 3 * u * t * t * (sy + ty) / 2 + t * t * t * ty; ui.threadRects.push({ x: bx - 6, y: by - 6, w: 12, h: 12 }); }
            const key = `${pos.hit.name}|${pos.hit.attr || ''}`;
            // ONE label per reached attribute, naming EVERY word that reached it
            const prior = seen.get(key);
            if (prior) { if (!prior.terms.includes(pos.term)) prior.terms.push(pos.term); }
            else seen.set(key, { terms: [pos.term], hit: pos.hit, tx, ty, col, back: _back });
        }
        for (const [key, v] of seen) {
            const h = v.hit;
            const text = [h.attr ? `${v.terms.join(', ')} ${h.channel === 'semantic' ? '≈' : '→'} ${h.attr}` : null, h.channel === 'semantic' ? `by meaning${h.score ? ` · ${Number(h.score).toFixed(2)}` : ''}` : null].filter(Boolean).join('  ');
            if (text) { const w = textW(text, 9.5, true) + 6; labels.push({ id: key, w, h: 13, col: v.col, text, seed: h.seedName, candidates: [{ x: v.tx + 8, y: v.ty - 28 }, { x: v.tx + 8, y: v.ty - 42 }, { x: v.tx - w - 8, y: v.ty - 28 }, { x: v.tx - w - 8, y: v.ty - 42 }, { x: v.tx + 8, y: v.ty - 56 }], priority: h.channel === 'semantic' ? 1 : 2 }); }
        }
        const occupied = boxRects(12).concat(ui.markRects.filter(r => !r.frame), ui.labelRects, ui.threadRects);
        const placed = placeLabels(labels, occupied, null);
        for (const l of labels) { const p = placed[l.id]; if (!p) continue; ui.threadRects.push({ x: p.x, y: p.y, w: l.w, h: l.h, label: true }); gThreads.append('text').attr('x', p.x).attr('y', p.y + 10).attr('font-size', 9.5).attr('font-family', mono).attr('fill', l.col).attr('opacity', (seen.get(l.id) || {}).back ? 0.3 : 1).style('cursor', 'pointer').style('pointer-events', 'all').text(l.text).on('click', (ev) => { ev.stopPropagation(); const t = seedWhy(l.seed); if (t) pinTip(ev.clientX, ev.clientY, t); }); }
    }

    // ── the whole graph as the opening field ──
    //   Every entity of the LOADED graph, dimmed, exactly where the viewer left it: the force
    //   graph's own coordinates AND its zoom transform when the panel had one (a graph they had
    //   panned, zoomed, filtered or focused opens from that very frame), a quick static layout
    //   otherwise. The seeds ignite in place; on expand the entities in scope GATHER into the
    //   layered ER arrangement while the rest recede to the margin — relevance emerging from
    //   the whole, continuous with what was on screen.
    function buildField(whole) {
        field.nodes = []; field.byName = {}; field.links = []; field.transform = null;
        if (!whole || !Array.isArray(whole.nodes)) return;
        const ents = whole.nodes.filter(n => n && ['entity', 'table', 'data_product', 'product_bridge', 'metric', 'domain', 'database'].includes(n.type));
        if (!ents.length) return;
        const havePos = ents.every(n => Number.isFinite(n.x) && Number.isFinite(n.y));
        size();
        const idx = new Map(ents.map((n, i) => [n.id, i]));
        const links = (whole.links || []).map(l => ({ s: idx.get(typeof l.source === 'object' ? l.source.id : l.source), t: idx.get(typeof l.target === 'object' ? l.target.id : l.target), type: l.type }))
            .filter(l => l.s !== undefined && l.t !== undefined);
        const nodes = ents.map((n, i) => ({ i, name: n.name, label: String(n.name).split('.').pop(), type: n.type, x: havePos ? n.x : 0, y: havePos ? n.y : 0 }));
        if (havePos && whole.transform && Number.isFinite(whole.transform.k)) {
            // 1:1 with the live graph: same coordinates, same zoom — the first frame IS the graph
            for (const n of nodes) { n.fx0 = n.x; n.fy0 = n.y; field.byName[n.name] = n; }
            field.transform = { k: whole.transform.k, x: whole.transform.x, y: whole.transform.y };
        } else {
            if (!havePos) {
                const sim = d3.forceSimulation(nodes).force('charge', d3.forceManyBody().strength(-90)).force('center', d3.forceCenter(0, 0))
                    .force('link', d3.forceLink(links.map(l => ({ source: l.s, target: l.t }))).distance(70).strength(0.3)).force('collide', d3.forceCollide(22)).stop();
                for (let k = 0; k < 200; k++) sim.tick();
            }
            const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
            const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
            const k = Math.min((layout.w - 120) / Math.max(1, x1 - x0), (layout.h - 160) / Math.max(1, y1 - y0));
            for (const n of nodes) { n.fx0 = 60 + (n.x - x0) * k; n.fy0 = 100 + (n.y - y0) * k; n.x = n.fx0; n.y = n.fy0; field.byName[n.name] = n; }
        }
        field.nodes = nodes; field.links = links;
    }
    function renderField(phase) {
        // phase: 'field' (everything, dim) · 'ignite' (seeds lit in place) · 'gather' (in-scope
        // nodes move into their boxes, the rest recede) · 'gone' (the margin haze)
        if (!gField) return;
        const m = S.model;
        const inScope = new Set(m ? Object.keys(m.entities) : []);
        const seeds = new Set(S.seeds.map(s => s.name).concat(S.seeds.map(s => s.name.split('.').slice(0, -1).join('.'))));
        const sel = gField.selectAll('g.kgt-fnode').data(field.nodes, d => d.name);
        const ent = sel.enter().append('g').attr('class', 'kgt-fnode').attr('transform', d => `translate(${d.fx0},${d.fy0})`).attr('opacity', 0);
        ent.append('circle').attr('class', 'core').attr('r', 4).attr('fill', T.ent);
        ent.append('circle').attr('class', 'halo').attr('r', 4).attr('fill', 'none').attr('stroke', T.orange).attr('stroke-width', 1.5).attr('opacity', 0);
        ent.append('text').attr('x', 8).attr('y', 4).attr('font-size', 9.5).attr('fill', T.muted).text(d => ellipsize(d.label, 9.5, 90));
        const all = ent.merge(sel);
        const dur = rm ? 0 : 900;
        const ls = gField.selectAll('line.kgt-flink').data(field.links, d => `${d.s}|${d.t}`);
        ls.enter().append('line').attr('class', 'kgt-flink').attr('stroke', T.muted).attr('stroke-width', 1).attr('opacity', 0)
            .attr('x1', d => field.nodes[d.s].fx0).attr('y1', d => field.nodes[d.s].fy0).attr('x2', d => field.nodes[d.t].fx0).attr('y2', d => field.nodes[d.t].fy0)
            .transition().duration(dur).attr('opacity', 0.12);
        if (phase === 'field') {
            all.transition().duration(dur).attr('opacity', 0.55);
            all.select('circle.halo').attr('opacity', 0);
        } else if (phase === 'ignite') {
            all.transition().duration(dur / 2).attr('opacity', d => seeds.has(d.name) ? 1 : 0.35);
            const lit = all.filter(d => seeds.has(d.name));
            lit.select('circle.core').transition().duration(dur / 2).attr('r', 7).attr('fill', T.orange);
            lit.select('circle.halo').attr('opacity', 0.9).attr('r', 7).transition().duration(rm ? 0 : 1100).ease(d3.easeCubicOut).attr('r', 26).attr('opacity', 0);
            lit.select('text').attr('fill', T.txt).attr('font-weight', 600);
            gField.selectAll('line.kgt-flink').transition().duration(dur / 2).attr('opacity', d => (seeds.has(field.nodes[d.s].name) || seeds.has(field.nodes[d.t].name)) ? 0.35 : 0.06);
        } else if (phase === 'gather') {
            // the rest recede to a ring that encloses the whole arrangement (never inside it),
            // each keeping its own bearing from the centre so the field's shape survives
            // The FIRST gather animates; every later re-application (the arrangement grew as rows
            // unfolded) settles the positions immediately, so nothing is caught mid-fade.
            const areas = Object.values(layout.areas);
            if (!areas.length) return;
            const bx0 = Math.min(...areas.map(a => a.x)), bx1 = Math.max(...areas.map(a => a.x + a.w)), by0 = Math.min(...areas.map(a => a.y)), by1 = Math.max(...areas.map(a => a.y + a.h));
            const cx = (bx0 + bx1) / 2, cy = (by0 + by1) / 2, rx = (bx1 - bx0) / 2 + 110, ry = (by1 - by0) / 2 + 90;
            const place = d => { const p = layout.pos[d.name]; if (p) return `translate(${p.x + 14},${p.y + 15})`; const ang = Math.atan2(d.fy0 - cy, d.fx0 - cx); return `translate(${cx + Math.cos(ang) * rx},${cy + Math.sin(ang) * ry})`; };
            const first = !field.gathered;
            const moving = first ? all.transition().duration(dur).ease(d3.easeCubicInOut) : all.interrupt();
            moving.attr('transform', place).attr('opacity', d => inScope.has(d.name) ? 0 : 0.10);
            all.filter(d => inScope.has(d.name)).style('pointer-events', 'none');
            const rest = all.filter(d => !inScope.has(d.name)).select('circle.core');
            (first ? rest.transition().duration(dur) : rest.interrupt()).attr('r', 3).attr('fill', T.muted);
            all.filter(d => !inScope.has(d.name)).select('circle.halo').attr('opacity', 0);
            gField.selectAll('line.kgt-flink').interrupt().transition().duration(first ? dur / 2 : 0).attr('opacity', 0);
            if (first) later(() => { all.filter(d => inScope.has(d.name)).interrupt().attr('opacity', 0); }, dur + 60);
            field.gathered = true;
        } else if (phase === 'gone') {
            all.transition().duration(dur / 2).attr('opacity', d => inScope.has(d.name) ? 0 : 0.06);
        }
        sel.exit().remove();
        field.shown = true;
    }
    function cameraField(ms) {
        if (!zoom || !field.nodes.length) return;
        if (field.transform && ms === 0) { svg.call(zoom.transform, d3.zoomIdentity.translate(field.transform.x, field.transform.y).scale(field.transform.k)); return; }
        if (ui.userMovedAt && Date.now() - ui.userMovedAt < 5000) return;
        const xs = field.nodes.map(n => n.fx0), ys = field.nodes.map(n => n.fy0);
        const x0 = Math.min(...xs) - 60, x1 = Math.max(...xs) + 120, y0 = Math.min(...ys) - 60, y1 = Math.max(...ys) + 60;
        const k = Math.max(0.2, Math.min(1.2, Math.min(layout.w / (x1 - x0), (layout.h - 60) / (y1 - y0))));
        svg.transition().duration(rm ? 0 : ms || 800).ease(d3.easeCubicInOut).call(zoom.transform, d3.zoomIdentity.translate(layout.w / 2 - k * (x0 + x1) / 2, (layout.h + 40) / 2 - k * (y0 + y1) / 2).scale(k));
    }

    // camera
    function cameraTo(names, pad, ms, extra) {
        if (!zoom || (ui.userMovedAt && Date.now() - ui.userMovedAt < 5000)) return;   // the viewer's hand wins for a while, then the story resumes
        const boxes = names.map(n => layout.pos[n]).filter(Boolean).concat(extra || []);
        if (!boxes.length) return;
        const x0 = Math.min(...boxes.map(b => b.x)) - pad, y0 = Math.min(...boxes.map(b => b.y)) - pad;
        const x1 = Math.max(...boxes.map(b => b.x + b.w)) + pad, y1 = Math.max(...boxes.map(b => b.y + b.h)) + pad;
        const vw = Math.max(320, layout.w - railReserve());   // the trail rail owns the right of the stage in Inspect — its measured extent
        // reserve exactly what the chrome occupies: the question above, the statement strip below (only while one is on stage)
        const qEl = root.querySelector('.kgt-question'), sEl = root.querySelector('.kgt-strip');
        const top = (S.question && qEl ? (qEl.getBoundingClientRect ? Math.max(44, qEl.getBoundingClientRect().height + 8) : 56) : 10);
        const bottom = layout.h - ((sEl && sEl.classList.contains('on')) ? sEl.offsetHeight + 14 : 10);
        const fit = Math.min(vw / (x1 - x0), (bottom - top) / (y1 - y0));
        const K_MAX = 1.2;    // a wide panel may enlarge the story a little; beyond this the boxes only enlarge their gaps
        const K_MIN = 0.2;    // fit is mandatory: whatever the story shows is on screen — the layout stacks and wraps before this is ever reached
        const k = Math.max(K_MIN, Math.min(K_MAX, fit));
        const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
        const tx = vw / 2 - k * cx, ty = (top + bottom) / 2 - k * cy;
        // one frame for the whole turn: move only when the story no longer fits the frame the viewer has
        const cur = d3.zoomTransform(svgEl);
        const shows = (r) => r.x * cur.k + cur.x >= 0 && (r.x + r.w) * cur.k + cur.x <= vw && r.y * cur.k + cur.y >= top - 2 && (r.y + r.h) * cur.k + cur.y <= bottom + 2;
        const contained = cur.k <= k * 1.04 && cur.k >= k * 0.7 && shows({ x: x0, y: y0, w: x1 - x0, h: y1 - y0 });
        const same = Math.abs(Math.log(cur.k / k)) < 0.05 && Math.abs(cur.x - tx) < 24 && Math.abs(cur.y - ty) < 24;
        if (ui.cameraSet && (contained || same)) return;
        ui.cameraSet = true;
        svg.transition().duration(rm ? 0 : ms || 800).ease(d3.easeCubicInOut).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(k));
    }
    function cameraAll(ms) { cameraTo(Object.keys(layout.pos), 10, ms, Object.values(layout.areas).concat(ui.markRects, ui.threadRects.filter(t => t.label))); }   // the whole story: areas, boxes and every mark

    // marks
    function barrierAt(a, b, text) {
        if (!ui.redrawing) ui.markIntents.push({ fn: 'barrierAt', args: [a, b, text] });
        const pa = layout.pos[a], pb = layout.pos[b]; if (!pa || !pb || !gMarks) return;
        const rt = route(a, b); const A = rt.A, B = rt.B; const mx = rt.mid.x, my = rt.mid.y;
        const ang = Math.atan2(B.y - A.y, B.x - A.x) * 180 / Math.PI;
        const g = gMarks.append('g').attr('class', 'kgt-barrier').attr('opacity', 0);
        g.append('path').attr('d', rt.d).attr('fill', 'none').attr('stroke', T.danger).attr('stroke-width', 2).attr('stroke-dasharray', '6 5').attr('opacity', 0.9);
        const bg = g.append('g').attr('transform', `translate(${mx},${my}) rotate(${ang})`);
        bg.append('rect').attr('x', -4).attr('y', -30).attr('width', 8).attr('height', 60).attr('rx', 3).attr('fill', T.danger);
        bg.append('rect').attr('x', -9).attr('y', -35).attr('width', 18).attr('height', 70).attr('rx', 6).attr('fill', 'none').attr('stroke', T.danger).attr('stroke-opacity', .45).classed('kgt-breathe', true);
        g.append('g').html(_svgIcon('stop', mx - 9, my - 9, 18, T.danger, 2.4));
        if (text) {
            const w = textW(text, 10) + 20;
            const routeRects = [{ x: mx - 42, y: my - 42, w: 84, h: 84 }]; for (let t = 0.05; t < 1; t += 0.08) routeRects.push({ x: A.x + t * (B.x - A.x) - 8, y: A.y + t * (B.y - A.y) - 8, w: 16, h: 16 });
            const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1, nx = -dy / len, ny = dx / len;   // the route's normal
            const cands = [];
            for (const along of [0.5, 0.36, 0.64, 0.24, 0.76]) for (const off of [52, 78, 108]) for (const side of [1, -1]) { const px = A.x + along * dx + side * nx * off, py = A.y + along * dy + side * ny * off; cands.push({ x: px - w / 2, y: py - 10 }); }
            const pl = placeLabels([{ id: 'b', w, h: 20, candidates: cands, priority: 9 }], boxRects(6).concat(ui.markRects.filter(r => !r.frame), ui.labelRects, ui.threadRects, routeRects), null).b || cands[0];
            if (pl) {
                ui.markRects.push({ x: pl.x, y: pl.y, w, h: 20 });
                g.append('rect').attr('x', pl.x).attr('y', pl.y).attr('width', w).attr('height', 20).attr('rx', 10).attr('fill', T.danger).attr('opacity', 0.14);
                g.append('rect').attr('x', pl.x).attr('y', pl.y).attr('width', w).attr('height', 20).attr('rx', 10).attr('fill', 'none').attr('stroke', T.danger).attr('stroke-opacity', .55);
                g.append('text').attr('x', pl.x + 10).attr('y', pl.y + 13.5).attr('font-size', 10).attr('font-weight', 600).attr('fill', T.danger).text(text);
            }
        }
        g.transition().duration((rm || ui.redrawing) ? 0 : 260).attr('opacity', 1);
        ui.marks.push(g);
    }
    function perimeter(label) {
        if (!ui.redrawing) ui.markIntents.push({ fn: 'perimeter', args: [label] });
        if (!gMarks) return;
        const boxes = Object.entries(layout.pos).filter(([n]) => { const e = S.model.entities[n]; return e && (ui.lit.has(n) || ui.solid.has(n) || ui.known.has(n)); }).map(([, p]) => p);
        if (!boxes.length) return;
        const pad = 18;
        const inside = (b, r) => b.x >= r.x && b.x + b.w <= r.x + r.w && b.y >= r.y && b.y + b.h <= r.y + r.h;
        const frames = Object.values(layout.areas).filter(r => boxes.some(b => inside(b, r)));   // the subject areas the scope holds
        const ext = (frames.length ? frames : boxes).concat(ui.labelRects.concat(ui.threadRects.filter(t => t.label)).filter(l => boxes.some(b => l.x < b.x + b.w + 60 && l.x + l.w > b.x - 60 && l.y < b.y + b.h + 60 && l.y + l.h > b.y - 60)));
        const x0 = Math.min(...ext.map(b => b.x)) - pad, y0 = Math.min(...ext.map(b => b.y)) - pad, x1 = Math.max(...ext.map(b => b.x + b.w)) + pad, y1 = Math.max(...ext.map(b => b.y + b.h)) + pad;
        const g = gMarks.append('g').attr('class', 'kgt-perimeter').attr('opacity', 0);
        const rect = g.append('rect').attr('x', x0).attr('y', y0).attr('width', x1 - x0).attr('height', y1 - y0).attr('rx', 26).attr('fill', 'none').attr('stroke', T.pass).attr('stroke-width', 1.5).attr('stroke-dasharray', '7 6');
        if (!rm && !ui.redrawing) { const L = 2 * ((x1 - x0) + (y1 - y0)); rect.attr('stroke-dasharray', `${L}`).attr('stroke-dashoffset', L).transition().duration(900).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0).on('end', () => rect.attr('stroke-dasharray', '7 6').attr('stroke-dashoffset', null)); }
        const w = textW(label, 10) + 16;
        const occ = boxRects(14).concat(ui.markRects.filter(r => !r.frame), ui.labelRects, ui.threadRects);
        const pl = placeLabels([{ id: 'p', w, h: 20, priority: 8, candidates: [{ x: x0 + 18, y: y1 - 10 }, { x: x1 - w - 18, y: y1 - 10 }, { x: x0 + 18, y: y0 - 10 }, { x: x1 - w - 18, y: y0 - 10 }] }], occ, null).p || { x: x0 + 18, y: y1 - 10 };
        ui.markRects.push({ x: pl.x, y: pl.y, w, h: 20 });
        ui.markRects.push({ x: x0, y: y0, w: x1 - x0, h: y1 - y0 + 12, frame: true });   // the camera fits the whole perimeter, label included
        g.append('rect').attr('x', pl.x).attr('y', pl.y).attr('width', w).attr('height', 20).attr('rx', 10).attr('fill', T.bg).attr('stroke', T.pass).attr('stroke-opacity', .6);
        g.append('text').attr('x', pl.x + 8).attr('y', pl.y + 14).attr('font-size', 10).attr('font-weight', 600).attr('fill', T.pass).text(label);
        g.transition().duration((rm || ui.redrawing) ? 0 : 400).attr('opacity', 0.8);
        ui.marks.push(g);
    }
    function ghosts(names) {
        if (!ui.redrawing) ui.markIntents.push({ fn: 'ghosts', args: [names] });
        if (!gMarks || !names.length) return;
        const boxes = Object.values(layout.pos); const x1 = Math.max(...boxes.map(b => b.x + b.w)) + 90;
        names.slice(0, 5).forEach((n, i) => {
            const y = 120 + i * 64;
            const g = gMarks.append('g').attr('class', 'kgt-ghost').attr('opacity', 0);
            g.append('circle').attr('cx', x1).attr('cy', y).attr('r', 22).attr('fill', 'none').attr('stroke', T.danger).attr('stroke-dasharray', '3 3').classed('kgt-breathe', true);
            g.append('text').attr('x', x1).attr('y', y + 4).attr('font-size', 9).attr('text-anchor', 'middle').attr('fill', T.danger).attr('font-family', mono).text(ellipsize(n, 9, 40, true));
            g.append('text').attr('x', x1).attr('y', y + 36).attr('font-size', 9).attr('text-anchor', 'middle').attr('fill', T.danger).text('out of scope');
            g.transition().duration((rm || ui.redrawing) ? 0 : 400).attr('opacity', 1);
            ui.marks.push(g);
        });
    }
    function receiptCard(r) {
        if (!ui.redrawing) ui.markIntents.push({ fn: 'receiptCard', args: [r] });
        if (!gMarks) return;
        const boxes = Object.values(layout.pos); if (!boxes.length) return;
        const areas = Object.values(layout.areas);
        const ax1 = Math.max(...areas.map(a => a.x + a.w)), ay0 = Math.min(...areas.map(a => a.y)), ay1 = Math.max(...areas.map(a => a.y + a.h)), ax0 = Math.min(...areas.map(a => a.x));
        // Inspect keeps the trail rail on the right, so the card prefers the left there
        const W = r.altered ? 390 : 280;   // one more fact on the last line when a check altered the answer
        const cands = depth === 'inspect'
            ? [{ x: ax1 + 24, y: ay0 }, { x: ax0, y: ay0 - 92 }, { x: ax1 - W, y: ay1 + 16 }, { x: ax1 - W, y: ay0 - 92 }]
            : [{ x: ax1 - W, y: ay0 - 92 }, { x: ax0, y: ay0 - 92 }, { x: ax1 + 24, y: ay0 }, { x: ax1 - W, y: ay1 + 16 }];
        const pl = placeLabels([{ id: 'r', w: W, h: 70, priority: 9, candidates: cands }], boxRects(6).concat(ui.markRects.filter(r => !r.frame), ui.labelRects, ui.threadRects), null).r || cands[0];
        ui.markRects.push({ x: pl.x, y: pl.y, w: W, h: 70 });
        const x = pl.x, y = pl.y;
        const g = gMarks.append('g').attr('class', 'kgt-receipt').attr('opacity', 0);
        g.append('rect').attr('x', x).attr('y', y).attr('width', W).attr('height', 70).attr('rx', 10).attr('fill', T.card).attr('stroke', T.border);
        g.append('g').html(_svgIcon(r.state === 'danger' ? 'stop' : 'check', x + 12, y + 12, 16, r.state === 'danger' ? T.danger : r.state === 'warn' ? T.warn : T.pass));
        const title = r.prevented && !r.applied ? `${r.prevented} statement${r.prevented === 1 ? '' : 's'} stopped` : r.wasted ? 'The graph went unused' : `${['No', 'One', 'Two', 'Three', 'Four', 'Five'][r.applied] || r.applied} fact${r.applied === 1 ? '' : 's'} applied`;
        g.append('text').attr('x', x + 36).attr('y', y + 24).attr('font-size', 11.5).attr('font-weight', 600).attr('fill', T.txt).text(title);
        g.append('text').attr('x', x + 36).attr('y', y + 41).attr('font-size', 10.5).attr('fill', T.txt2).text(ellipsize(r.facts.length ? r.facts.join(' · ') : (r.prevented ? 'nothing answered from a wrong join' : 'no model fact was needed'), 10.5, W - 46));
        g.append('text').attr('x', x + 36).attr('y', y + 57).attr('font-size', 10).attr('fill', T.muted).text(ellipsize([r.prevented ? `${r.prevented} stopped` : 'nothing stopped', r.inScope === true ? 'in scope' : r.inScope === false ? 'out of scope' : null, r.altered ? 'answer altered' : null, r.seeds ? `${r.seedsUsed} of ${r.seeds} seeds used` : null].filter(Boolean).join(' · '), 10, W - 46));
        g.transition().duration((rm || ui.redrawing) ? 0 : 400).attr('opacity', 1);
        ui.marks.push(g);
    }

    function redrawMarks() {
        if (!gMarks) return;
        gMarks.selectAll('*').remove(); ui.marks = []; ui.markRects = [];
        ui.redrawing = true;
        try { for (const it of ui.markIntents) ({ barrierAt, perimeter, ghosts, receiptCard })[it.fn](...it.args); } finally { ui.redrawing = false; }
    }
    // statement strip
    function strip(st, danger) {
        const el = root.querySelector('.kgt-strip'); if (!el) return;
        ui.stripShown = st ? { st, danger: !!danger } : null;   // remembered, so a scrub re-renders it once the whole turn is known
        if (!st) { el.classList.remove('on'); return; }
        const roles = st.roles || {}; const sql = String(st.sql || `${st.tool}(${JSON.stringify(st.args || {}).slice(0, 120)})`);
        const toks = sql.replace(/\s+/g, ' ').split(' ');
        const bad = new Set(); for (const s of (st.suspect || [])) { (s.used || []).forEach(u => bad.add(u.toLowerCase())); }
        const html = toks.map(t => {
            const low = t.toLowerCase().replace(/[(),]/g, '');
            let cls = '';
            if (/^(select|from|join|on|where|and|or|group|by|order|having|as|limit|top|left|right|inner|outer|count|sum|avg|min|max|distinct)$/i.test(low)) cls = 'kw';
            else if (/^'/.test(t)) cls = 'lit';
            else if (roles.tables && roles.tables.some(tb => low.endsWith(tb))) cls = 'ent';
            else if (low.includes('.')) { const [al, col] = low.split('.'); const table = (roles.aliases || {})[al]; const role = table ? (roles.roles || {})[`${table}.${col}`] : null; cls = role === 'key' ? 'key' : role ? 'attr' : ''; if (danger && bad.has(col)) cls = 'bad'; if (danger && st.suspect.some(s => s.kind === 'cross_area') && role === 'key') cls = 'bad'; }
            return cls ? `<span class="tk ${cls}">${_esc(t)}</span>` : _esc(t);
        }).join(' ');
        el.innerHTML = `<span class="lbl">STATEMENT${S.statements.length > 1 ? ` ${st.id} OF ${S.statements.length}` : ''} UNDER TEST${st.rewritten ? ` · REWRITTEN (${_esc(st.rewritten.label)})` : ''}</span>${statementChips(st)}<span class="chev">${_htmlIcon('chev', 12)}</span><span class="sql">${html}</span>`;
        el.classList.toggle('danger', !!danger); el.classList.add('on');
        el.onmousemove = (ev) => showTip(ev.clientX, ev.clientY, sql);
        el.onmouseleave = hideTip;
    }

    // caption + ribbon + trail
    function caption(text, stateWord, sub) {
        const main = root.querySelector('.kgt-cap-main'), subEl = root.querySelector('.kgt-cap-sub'), dot = root.querySelector('.kgt-dot');
        if (main) main.textContent = text || '';
        if (subEl) subEl.textContent = sub || '';
        if (dot) dot.className = `kgt-dot ${stateWord || 'live'}`;
    }
    // The beat the STAGE is showing — the beat of the op it last performed. The reducer runs
    // ahead of the stage whenever ops are queued: during a scrub (it consumes the whole turn
    // while the stage holds), and during the catch-up after a release (it sits at the receipt
    // while the stage still plays Seed → Expand → …), which left the receipt's dot breathing
    // for the whole catch-up. ONE rule for the ribbon and the trail: held wins, then the
    // stage's own beat, and the reducer's only before the stage has performed anything.
    function shownBeat() { return ui.held || ui.stageBeat || S.beat; }
    // ONE view per beat, read from the STAGE, for the trail, the ribbon and the explanation:
    //   known     the reducer has reduced it (it is in the turn)
    //   done      the stage performed it — or, while held, the whole known turn (a hold is the
    //             story so far; it never turns a finished beat back into a pending one)
    //   queued    known but not yet performed: the stage is behind the reducer (a replay, a
    //             burst, the catch-up after a release) — the old trail read these as DONE with
    //             their final facts the moment a replay started, which is the confusion
    //   absent    not in the turn and the turn is over: it DID NOT RUN (never "pending")
    //   ahead     after the hold: known, dimmed
    function beatView(b) {
        const idx = BEAT_IDX[b];
        const known = S.beatsSeen.includes(b);
        const finished = ui.ended || !!S.receipt;
        const trueHeld = ui.preview ? ui.previewFrom.held : ui.held;   // a preview borrows the hold machinery; the trail keeps the real hold marked
        const held = trueHeld === b, ahead = !!(ui.held && idx > BEAT_IDX[ui.held]);
        const preview = ui.preview === b && !held;
        const done = known && (ui.performed.has(b) || !!ui.held || (ui.ended && !ui.queue.length));
        const current = shownBeat() === b && !ui.ended;
        const queued = known && !done;
        const absent = !known && finished && !ui.queue.length;
        const danger = (b === 'execute' && S.consequences.some(c => c.kind === 'prevented')) || (b === 'verify' && S.verdict && S.verdict.decision === 'block');
        return { beat: b, known, done, current, held, preview, ahead, queued, absent, pending: !known && !absent, danger, heldSt: held ? (ui.preview ? ui.previewFrom.heldSt : ui.heldSt) : null };
    }
    // The trail's seconds are the live run's clock. They are shown only when that clock is
    // real: a reload rebuilds the turn from the persisted record (no times), and a burst
    // that lands every beat inside a quarter second is a replay's arrival, not the turn's.
    function timesCoherent() {
        if (!S.timesReal) return false;
        const ts = Object.values(S.stageTimes); if (ts.length < 3) return true;
        return Math.max(...ts) - Math.min(...ts) > 300;
    }
    function ribbon() {
        const el = root.querySelector('.kgt-ribbon'); if (!el) return;
        // "done" follows the REDUCER (what the turn has reached); "current" follows the STAGE
        // (the held beat while held). A hold dims the beats after it (ahead) — it never turns a
        // finished beat back into a pending one, which is what happened when the done cut-off
        // moved to the held beat.
        el.innerHTML = BEATS.map(b => {
            const v = beatView(b);
            return `<span class="kgt-beat ${v.current ? 'active' : ''} ${v.done ? 'done' : ''} ${v.queued ? 'queued' : ''} ${v.ahead ? 'ahead' : ''} ${v.danger ? 'danger' : ''}"><i></i>${BEAT_LABEL[b]}</span>`;
        }).join('');
    }
    function trail() {
        const el = root.querySelector('.kgt-trail'); if (!el) return;
        const t0 = S.startedAt || Date.now(); const coherent = timesCoherent();
        el.innerHTML = BEATS.map(b => {
            const v = beatView(b); const cur = v.current;
            const rel = S.stageTimes[b] ? (S.stageTimes[b] - t0) / 1000 : null;
            // 'now' is a LIVE word for the beat on stage; a finished beat shows when it began — only
            // on a clock that is real (never "0.00 s" for every beat of a replayed burst)
            const ts = (cur && !ui.held) ? 'now' : (v.done && coherent && rel != null && rel >= 0 && rel < 3600) ? `${rel.toFixed(2)} s` : '';
            const facts = ({
                understand: S.terms.length ? `${S.terms.length} terms` : '',
                seed: S.seeds.length ? `${S.seeds.filter(s => s.channel !== 'product').slice(0, 2).map(s => _leaf(s.name)).join(', ')} · ${S.seeds.length} seeds${S.seeds.some(s => s.channel === 'semantic') ? ' · by meaning' : ''}` : '',
                expand: S.model ? `${_plural(S.model.rels.length, 'relationship')} · ${_plural(correspondenceCount(S.model), 'correspondence')}` : '',
                inject: S.model ? `${nodesLabel(entityCount(S.model))} · ${Object.values(S.model.omitted).reduce((a, b) => a + (Number(b) || 0), 0)} folded` : '',
                execute: S.statements.length ? `${S.statements.length} statement${S.statements.length === 1 ? '' : 's'} · ${S.consequences.filter(c => c.kind === 'applied').length} applied · ${S.consequences.filter(c => c.kind === 'prevented').length} stopped${S.consequences.some(c => c.kind === 'drafted') ? ` · ${S.consequences.filter(c => c.kind === 'drafted' && c.drafted).length} drafted` : ''}` : (S.consequences.some(c => c.kind === 'drafted') ? `${S.consequences.filter(c => c.kind === 'drafted' && c.drafted).length} drafted · ${S.consequences.filter(c => c.kind === 'drafted' && !c.drafted).length} proposed` : ''),
                verify: S.verdict ? (S.verdict.oos.length ? `outside scope: ${S.verdict.oos.slice(0, 2).join(', ')}` : 'in scope') + (S.honesty && S.honesty.acted ? ' · answer altered' : S.verdict.decision === 'annotate' ? ' · annotated' : '') : '',
                receipt: S.receipt ? ((S.receipt.facts.length ? S.receipt.facts.join(' · ') : S.receipt.sentence) + (S.receipt.altered && S.receipt.facts.length ? ' · answer altered' : '')) : '',   // a receipt with no facts applied still has a verdict to state
            })[b] || '';
            // the facts of a beat appear once the stage performed it; before that the row says
            // what it IS — up next, waiting, or (after the receipt) that it did not run
            const sub = v.done ? (facts || (v.absent ? '' : 'performed')) : v.queued ? 'up next' : v.absent ? 'did not run this turn' : 'waiting';
            const { held, ahead, danger, preview } = v;
            const stRows = (b === 'execute' && S.statements.length > 1) ? S.statements.map(st => {
                const x = statementStatus(st); const perf = ui.performedSt.has(st.id) || !!ui.held || (ui.ended && !ui.queue.length);
                const stHeld = (ui.preview ? ui.previewFrom.heldSt : ui.heldSt) === st.id, stPrev = ui.preview && ui.previewSt === st.id && !stHeld;
                const ents = [...statementEntities(st)].map(n => _leaf(n)); const what = st.sql ? (ents.length ? ents.slice(0, 3).join(' · ') : 'statement') : st.tool;
                return `<div class="kgt-tr-st ${perf ? x.cls : 'queued'} ${stHeld ? 'held' : ''} ${stPrev ? 'preview' : ''} ${ahead || (ui.heldSt != null && st.id > ui.heldSt) ? 'ahead' : ''} u-tip" data-st="${st.id}" data-tip="Statement ${st.id} — hover to preview the story there · click to hold it"><span class="d"></span><span>#${st.id} · ${_esc(what)}</span><span class="w">${_esc(perf ? x.word : 'up next')}</span></div>`; }).join('') : '';
            const ex = explainBeat(S, b, v); const title = v.known ? `${held ? 'Held here — click to let the story continue' : v.done ? 'Hover to preview the story at this beat · click to hold it' : 'Click to jump the story here'}\n${ex.does}` : ex.status;
            return `<div class="kgt-tr ${v.done ? 'done' : v.queued ? 'queued' : v.absent ? 'absent' : 'pending'} ${v.known ? 'known' : ''} ${cur ? 'active' : ''} ${danger ? 'danger' : ''} ${held ? 'held' : ''} ${preview ? 'preview' : ''} ${ahead ? 'ahead' : ''} u-tip" data-beat="${b}" data-tip="${_esc(title)}"><span class="d"></span><span class="t">${BEAT_LABEL[b]}</span><span class="ts">${ts}</span><span class="s u-tip" data-tip="${_esc(sub)}">${_esc(sub)}</span></div>${stRows}`;
        }).join('');
    }
    // The explanation line: the beat on stage — or the one held by a click — in three parts:
    // what it does (constant), what it did on this turn (the state, once performed), what it
    // is doing now (the view). One reading, never a second vocabulary beside the trail's.
    function explain() {
        const el = root.querySelector('.kgt-explain'); if (!el) return;
        if (!ui.explain) { el.innerHTML = ''; return; }
        let ex;
        if (ui.held && ui.heldSt != null) { const st = S.statements.find(x => x.id === ui.heldSt); ex = explainStatement(S, st, { held: true }); }
        if (!ex) { const b = shownBeat(); ex = b ? explainBeat(S, b, beatView(b)) : { label: 'Theatre', does: 'The stage narrates one turn: how the knowledge graph shaped the agent\u2019s context and what that was worth. Click a beat in the trail (Inspect) to hold the story there.', did: '', status: '' }; }
        el.innerHTML = `<span class="lb">${_esc(ex.label.toUpperCase())}</span><span class="tx"><span class="does">${_esc(ex.does)}</span>${ex.did ? ` <span class="did">${_esc(ex.did)}</span>` : ''}${ex.status ? ` <span class="st">${_esc(ex.status)}</span>` : ''}</span>`;
    }
    // The key: one swatch per mark, drawn with the tokens the stage draws with.
    function swatch(sw, v) {
        const W = 26, H = 12;
        const rel = (col, dash, flow) => `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><path d="M1 6 H25" fill="none" stroke="${col}" stroke-width="1.6"${dash ? ` stroke-dasharray="${dash}"` : ''} class="${flow ? 'kgt-flow' : ''}"/></svg>`;
        switch (sw) {
            case 'dot': { const c = { pending: `fill="${T.muted}" opacity=".5"`, queued: `fill="none" stroke="${T.orange}" stroke-dasharray="2 2"`, active: `fill="${T.orange}"`, done: `fill="${T.pass}"`, danger: `fill="${T.danger}"` }[v]; return `<svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="4" ${c}/></svg>`; }
            case 'bar': return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect x="0" y="0" width="${W}" height="${H}" rx="2" fill="rgba(241,95,34,.12)"/><rect x="0" y="0" width="2" height="${H}" fill="${T.orange}"/></svg>`;
            case 'time': return `<span class="sv">${_esc(v)}</span>`;
            case 'text': return `<span class="sv">${_esc(v)}</span>`;
            case 'box': { const c = { dim: `fill="${T.ent}18" stroke="${T.ent}55" opacity=".4"`, known: `fill="${T.ent}18" stroke="${T.ent}"`, lit: `fill="${T.ent}30" stroke="${T.ent}" filter="url(#kgt-glow)"`, solid: `fill="${T.ent}30" stroke="${T.ent}" stroke-width="1.6"`, background: `fill="${T.ent}30" stroke="${T.ent}" opacity=".45"`, cooling: `fill="transparent" stroke="${T.ent}55" stroke-dasharray="3 3"` }[v]; return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect x="1" y="1" width="${W - 2}" height="${H - 2}" rx="3" ${c}/></svg>`; }
            case 'ring': return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect x="4" y="3" width="${W - 8}" height="${H - 6}" rx="2" fill="${T.ent}30" stroke="${T.ent}"/><rect x="0.8" y="0.8" width="${W - 1.6}" height="${H - 1.6}" rx="4" fill="none" stroke="${T.orange}" stroke-dasharray="3 3"/></svg>`;
            case 'thread': return rel(v === 'semantic' ? T.accent : T.orange, v === 'semantic' ? '4 4' : null, false);
            case 'rel': return v === 'bridge' ? rel(T.muted, '3 5') : v === 'known' ? rel(T.fk + '99', '5 4') : v === 'lit' ? rel(T.fk, '8 6', true) : v === 'pass' ? rel(T.pass) : rel(T.danger, '6 5');
            case 'perimeter': return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect x="1" y="1" width="${W - 2}" height="${H - 2}" rx="4" fill="none" stroke="${T.pass}" stroke-width="1.4" stroke-dasharray="4 3"/></svg>`;
            case 'ghost': return `<svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="none" stroke="${T.danger}" stroke-dasharray="3 3"/></svg>`;
            case 'barrier': return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><path d="M1 6 H25" fill="none" stroke="${T.danger}" stroke-width="1.8" stroke-dasharray="4 3"/><rect x="8" y="1" width="10" height="10" rx="5" fill="${T.danger}"/></svg>`;
            case 'receipt': return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect x="1" y="1" width="${W - 2}" height="${H - 2}" rx="2" fill="${T.card}" stroke="${T.border}"/>${_svgIcon('check', 3, 2, 8, T.pass, 2.5)}</svg>`;
            case 'icon': return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="${v === 'absent' ? T.danger : v === 'key' ? T.pk : T.attr}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[ROLE_ICON[v] || v] || ''}</svg>`;
            default: return '';
        }
    }
    function legend() {
        const el = root.querySelector('.kgt-legend'); if (!el) return;
        if (!ui.legend) { el.innerHTML = ''; delete el.dataset.built; return; }   // hiding empties the row, so 'built' must not survive it — the third click showed nothing (2026-09-17)
        if (el.dataset.built === '1') return;   // static: built once per showing
        el.innerHTML = LEGEND.map(g => `<div class="grp"><span class="gl">${_esc(g.group.toUpperCase())}</span>${g.items.map(it => `<span class="it" data-sw="${it.sw}" data-v="${_esc(it.v)}">${swatch(it.sw, it.v)}<span>${_esc(it.label)}</span></span>`).join('')}</div>`).join('');
        el.dataset.built = '1';
    }
    function setPref(key, on) {
        if (key === 'legend') ui.legend = !!on; else ui.explain = !!on;
        try { localStorage.setItem(key === 'legend' ? 'kg_theatre_legend' : 'kg_theatre_explain', on ? '1' : '0'); } catch (_) {}
        root.classList.toggle(key, !!on);
        const btn = root.querySelector(key === 'legend' ? '.kgt-key' : '.kgt-explain-btn'); if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        legend(); explain();
    }
    function refreshChrome() {
        const sk = reducer.skin();
        if (ui.held && ui.heldSt != null) { const st = S.statements.find(x => x.id === ui.heldSt); const x = st ? statementStatus(st) : null; caption(st ? `Statement ${st.id} of ${S.statements.length}${x ? ` — ${x.word}` : ''}${st.sql ? '' : ` · ${st.tool}`}` : sk.text, x && x.prevented ? 'danger' : 'live', ui.preview ? 'Execute · preview — click the statement to hold the story here' : 'Execute · held — click the statement again to continue'); }
        else if (ui.held) caption(S.beatSentences[ui.held] || sk.text, 'live', ui.preview ? `${BEAT_LABEL[ui.held]} · preview — click the beat to hold the story here` : `${BEAT_LABEL[ui.held]} · held — click the beat again to continue`);
        else {
            // The stage is behind the reducer while ops are queued (the catch-up after a release,
            // or a burst): the caption narrates the beat ON STAGE, not the one the reducer reached
            // — else the receipt's sentence shows while Seed → Expand are still playing.
            const behind = ui.queue.length && ui.stageBeat && ui.stageBeat !== S.beat && S.beatSentences[ui.stageBeat];
            if (behind) caption(S.beatSentences[ui.stageBeat], 'live', `${BEAT_LABEL[ui.stageBeat]} · replaying`);
            else caption(sk.text, sk.state, depth === 'inspect' && S.beat ? `${BEAT_LABEL[S.beat]} · ${S.synthetic ? 'stages reconstructed from the enrichment' : 'live'}` : '');
        }
        ribbon(); trail(); explain();
        root.querySelectorAll('.kgt-pill').forEach(p => p.classList.toggle('on', p.dataset.depth === depth));
        const idle = !(S.model || S.question || S.seeds.length);
        root.querySelector('.kgt-empty').style.display = idle ? 'flex' : 'none';
        // The Replay control is offered only when there is a story to replay (Rule 8.5: an act
        // that cannot happen is never offered): the armed last turn while idle, else the turn
        // on stage. A story never plays uninvited — the question is the only thing that starts one.
        const src = (ui.events && ui.events.length) ? ui.events : (_replaySource || []);
        const rb = root.querySelector('.kgt-replay');
        if (rb) { rb.style.display = src.length ? '' : 'none'; rb.title = idle ? 'Replay the last turn of this session' : 'Replay this turn'; rb.querySelector('.kgt-replay-l').textContent = idle ? 'Replay last turn' : 'Replay'; }
        const sub = root.querySelector('.kgt-empty-sub');
        // The idle sub-line says what the stage is waiting FOR: an armed story names the
        // replay; otherwise the host's note (a new session, an override active, a graph no
        // turn has used yet) — never the replay sentence over a story that is not there.
        if (sub) sub.textContent = !idle ? '' : (_replaySource && _replaySource.length) ? 'The last turn of this session is ready to replay — use the control below.' : (opts.idleNote || '');
    }
    function repaint() {
        if (S.model) computeLayout(S.model);
        renderRegions(); renderEntities(); renderLinks(); renderLabels();   // boxes first: a line attaches to the box as drawn
        if (ui.threadPositions) drawThreads(ui.threadPositions);
        // the marks follow the layout: a stage that changed shape redraws them for the shape it has now
        const sig = [layout.vw, layout.vh, layout.stacked ? 1 : 0, depth, ...Object.values(layout.areas).map(a => `${Math.round(a.x)},${Math.round(a.y)},${Math.round(a.w)},${Math.round(a.h)}`)].join('|');
        if (ui.markIntents.length && sig !== ui.markSig) redrawMarks();
        ui.markSig = sig;
        if (field.gathered) renderField('gather');
    }

    // ── the op player: ops in, choreography out. Never blocks an incoming event. ──
    const BEAT_IDX = Object.fromEntries(BEATS.map((b, i) => [b, i]));
    function enqueue(ops) { for (const op of ops) ui.queue.push(op); if (!ui.playing && !ui.held && !ui.deferPlay) playNext(); }
    function playNext() {
        if (ui.held) { ui.playing = false; return; }
        const next = ui.queue[0];
        if (!next) { ui.playing = false; return; }
        // scrubbing: hold before the first op of a beat past the target — the stage stays there
        if (ui.scrubTo && ((next.beat && BEAT_IDX[next.beat] > BEAT_IDX[ui.scrubTo]) || (ui.scrubSt != null && next.st != null && next.st > ui.scrubSt))) { ui.held = ui.scrubTo; ui.heldSt = ui.scrubSt; ui.playing = false; refreshChrome(); return; }
        const op = ui.queue.shift();
        ui.playing = true;
        const instant = !!ui.scrubTo;
        // compress the cascade when a burst is waiting (live catch-up), never below a beat floor
        const burst = ui.queue.length;
        const dur = (ms) => (rm || instant) ? 0 : Math.max(120, ms / (burst > 3 ? 3 : burst > 1 ? 1.6 : 1));
        try { perform(op, dur); } catch (e) { console.warn('[kgTheatre] op failed', op.op, e); }
        if (op.beat) { ui.stageBeat = op.beat; ui.performed.add(op.beat); }   // what the stage is showing now (shownBeat) and has shown
        if (op.statement && op.statement.id) ui.performedSt.add(op.statement.id);
        refreshChrome();
        if (instant) { playNext(); return; }
        const hold = ({ question: 700, seeds: 700, expand: 900, inject: 700, model: 80, statement: 500, applied: 600, prevented: 900, executed: 200, verdict: 800, honesty: 500, receipt: 100 })[op.op] || 200;
        later(playNext, dur(hold));
    }

    function perform(op, dur) {
        const m = S.model;
        switch (op.op) {
            case 'question': {
                ui.threadTargets = null;
                const pos = renderQuestion(threadMap());
                ui.qpos = pos;
                if (gQuestion) gQuestion.classed('kgt-q-focus', !!(S.terms || []).length);   // the terms are what this beat found
                if (field.nodes.length && !field.gathered) { renderField('field'); cameraField(dur(900)); } else cameraAll(dur(800));
                break;
            }
            case 'seeds': {
                const seeds = (op.seeds || []).filter(s => s.channel !== 'product');
                // an attribute seed (Offence.offence_code) lights the entity it belongs to — the
                // box the thread lands on; lighting only the attribute's name lit nothing on stage
                // every seed lights its box (live and replay alike — the model op lights them all),
                // and the box shows the seeded attribute and its words, not only its key
                for (const s of seeds) { ui.lit.add(s.name); const o = seedOwner(s.name); if (o) { ui.lit.add(o); if (m) setRows(o, seedRows(m.entities[o])); } }
                if (gQuestion) gQuestion.classed('kgt-q-focus', false);   // Understand's frame is over
                if (field.nodes.length && !field.gathered) renderField('ignite');
                if (m) { computeLayout(m); repaint(); const pos = renderQuestion(threadMap()); drawThreads(pos); cameraAll(dur(900)); }
                else { const pos = renderQuestion(threadMap()); drawThreads(pos); }
                break;
            }
            case 'model': {
                computeLayout(m);
                if (field.nodes.length && !field.gathered) renderField('gather');
                // everything the model holds is "known" — but knowing the chain is EXPAND's job: on a
                // reconstructed turn the model op performs at Seed, and marking the rest known there
                // made a held Seed read as Expand (the rest at 0.55, not dim)
                if (ui.performed.has('expand')) for (const n of Object.keys(m.entities)) ui.known.add(n);
                for (const s of m.seeds) if (s.channel !== 'product') { if (m.entities[s.name]) ui.lit.add(s.name); const o = seedOwner(s.name); if (o) ui.lit.add(o); }
                // a lit box shows WHY it is lit: the seeded attribute(s) and the word that reached
                // each, ahead of its key — a box lit by `offence_code` must say so, not show its key
                for (const n of ui.lit) setRows(n, seedRows(m.entities[n]));
                repaint();
                const pos = renderQuestion(threadMap()); drawThreads(pos);
                break;
            }
            case 'expand': {
                if (!m) break;
                // the chain draws outward from the seeds: fk_chain first, then joinable, in provenance order
                const order = ['fk_chain', 'bridge', 'joinable', 'database', 'semantic'];
                const lit = [...ui.lit];
                for (const r of m.rels) {
                    const touchesSeed = lit.includes(r.source) || lit.includes(r.target);
                    ui.relStates.set(r, touchesSeed ? 'lit' : 'known');
                }
                for (const b of m.bridges) ui.relStates.set(b, 'known');
                for (const tag of order) for (const [n, t] of Object.entries(m.provenance)) if (t === tag && m.entities[n]) ui.known.add(n);
                for (const n of Object.keys(m.entities)) { if (!ui.lit.has(n)) ui.known.add(n); if (ui.lit.has(n)) setRows(n, seedRows(m.entities[n])); }
                if (field.nodes.length && !field.gathered) renderField('gather');
                repaint();
                cameraAll(dur(900));
                break;
            }
            case 'inject': {
                // The flags are set whether or not the model has arrived: live, the inject stage
                // event precedes the enrichment that builds the model, so this op used to skip
                // entirely and the folded counts never showed on a live turn — while a replay
                // (model already reduced) showed them. One story, live and replayed.
                ui.regionsSolid = true; ui.showFolded = true;
                if (!m) break;
                for (const n of Object.keys(m.entities)) ui.solid.add(n);
                for (const r of m.rels) if (ui.relStates.get(r) !== 'lit') ui.relStates.set(r, 'solid');
                // keys on every injected entity in inspect; watch keeps keys on the lit ones only
                if (depth === 'inspect') for (const n of Object.keys(m.entities)) setRows(n, ui.lit.has(n) ? seedRows(m.entities[n]) : keyRows(m.entities[n]));
                repaint(); cameraAll(dur(800));
                break;
            }
            case 'statement': {
                const st = op.statement;
                if (!m) { caption(`Running ${st.tool}…`, 'live'); break; }
                // a new statement settles the previous one: lit → solid, keep rows
                for (const n of ui.lit) ui.solid.add(n);
                ui.lit.clear(); ui.rings.clear(); ui.executing = true;
                for (const r of m.rels) if (ui.relStates.get(r) === 'lit') ui.relStates.set(r, 'solid');
                const roles = st.roles;
                const touched = roles.tables.map(t => m.entities[m.byLeaf[t]]).filter(Boolean);
                for (const e of touched) { ui.lit.add(e.name); ui.used.add(e.name); setRows(e.name, statementRows(e, roles)); }
                // grain ring on the measure's entity
                const mm = roles.measures[0];
                const grainEnt = st.grain && m.entities[st.grain] ? m.entities[st.grain] : (mm && (mm.table ? m.entities[m.byLeaf[mm.table]] : touched[0]));
                if (grainEnt) ui.rings.add(grainEnt.name);
                // joins on declared keys flow
                for (const j of roles.joins) {
                    if (!j.a) continue;
                    const ea = m.entities[m.byLeaf[j.a.table]], eb = m.entities[m.byLeaf[j.b.table]]; if (!ea || !eb) continue;
                    const rel = m.rels.find(r => (r.source === ea.name && r.target === eb.name) || (r.source === eb.name && r.target === ea.name));
                    if (rel) ui.relStates.set(rel, 'lit');
                }
                repaint(); strip(st, false);
                cameraAll(dur(800));
                break;
            }
            case 'applied': {
                const c = op.consequence;
                if (c.fact === 'declared_key' && c.rel) ui.relStates.set(c.rel, 'lit');
                repaint();
                break;
            }
            case 'draft': {
                const c = op.consequence; if (!m) break;
                const parent = c.parent && (m.entities[c.parent] ? c.parent : m.byLeaf[_leaf(c.parent)] || null);
                if (c.what === 'entity') {
                    const name = c.name.includes('.') ? c.name : (parent && parent.includes('.') ? `${parent.split('.')[0]}.${c.name}` : c.name);
                    if (!m.entities[name]) {
                        const pe = parent ? m.entities[parent] : null;
                        m.entities[name] = { name, leaf: _leaf(name), label: String(name).split('.').pop(), type: 'entity', deployed: null, depLeaf: null,
                            area: pe ? pe.area : null, description: '', rows: null, keyUnique: null, keys: [], fks: [], attrs: {}, attrOrder: [],
                            provenance: 'draft', basis: null, mechanism: null, draft: c.drafted ? 'draft' : 'proposed' };
                        m.byLeaf[_leaf(name)] = name;
                        if (parent) m.rels.push({ source: name, target: parent, type: 'relates_to', cardinality: null, name: null, fromCols: [], toCols: [], declared: false, draft: true });
                        computeLayout(m);
                    }
                    ui.drafts.set(name, c.drafted ? 'draft' : 'proposed'); ui.known.add(name); ui.lit.add(name);
                } else if (parent) {
                    const rows = ui.rowsByEntity[parent] || [];
                    if (!rows.some(r => r.name === c.name)) rows.push({ name: c.name, role: 'seed', detail: c.drafted ? 'draft' : 'proposed', detailInspect: `${c.drafted ? 'draft' : 'proposed'}${c.rule ? ` · ${String(c.rule).replace(/_/g, ' ')}` : ''}` });
                    ui.rowsByEntity[parent] = rows; ui.lit.add(parent); ui.known.add(parent);
                    computeLayout(m);
                }
                repaint();
                break;
            }
            case 'prevented': {
                const c = op.consequence; const st = op.statement;
                ui.danger = true;
                if (st) strip(st, true);
                // draw the barrier on the offending relationship / boundary
                const s = (c.suspect || []).find(x => x.a && x.b) || null;
                if (s && m) {
                    const ea = m.entities[s.a], eb = m.entities[s.b];
                    if (ea && eb) {
                        const rel = m.rels.find(r => (r.source === ea.name && r.target === eb.name) || (r.source === eb.name && r.target === ea.name));
                        if (rel) ui.relStates.set(rel, 'danger');
                        if (s.kind === 'wrong_key') { for (const n of [ea.name, eb.name]) setRows(n, [...keyRows(m.entities[n]), ...(s.used || []).filter(u => !(m.entities[n].keys.map(k => k.toLowerCase())).includes(u)).map(u => ({ name: u, role: 'absent', state: 'danger', detail: 'not a key', tip: `${u} is not part of the declared key (${s.declared.join(', ')})` }))]); }
                        if (s.kind === 'cross_area') { ui.lit.add(eb.name); setRows(eb.name, keyRows(eb)); const bridge = s.bridge; if (bridge) ui.relStates.set(bridge, 'pass'); }
                        repaint();
                        barrierAt(ea.name, eb.name, 'stopped before the database');
                        cameraAll(dur(700));
                    }
                } else if (m) {
                    const last = [...ui.lit][0]; if (last) { setRows(last, [...rowsOf(last), { name: c.check, role: 'absent', state: 'danger', detail: 'refused', tip: c.message }]); repaint(); }
                }
                break;
            }
            case 'executed': {
                const st = op.statement;
                if (st && st.result && st.result.rows != null) caption(reducer.skin().text, 'live', `${st.result.rows} row${st.result.rows === 1 ? '' : 's'} returned`);
                break;
            }
            case 'verdict': {
                const v = op.verdict; if (!m) break;
                for (const r of m.rels) if (ui.relStates.get(r) === 'lit') ui.relStates.set(r, 'pass');
                repaint();
                perimeter(`Trusted scope · ${(v.scope && v.scope.length ? v.scope : m.scope).slice(0, 3).join(', ') || (m.kg && m.kg.name) || 'model'}`);
                if (v.oos && v.oos.length) ghosts(v.oos);
                if (svg) svg.classed('kgt-v-focus', true);   // the scope is what this beat is about
                cameraAll(dur(900));
                break;
            }
            case 'honesty': break;
            case 'receipt': {
                const r = op.receipt; ui.ended = true;
                if (svg) svg.classed('kgt-v-focus', false);
                if (!m) break;
                if (r.wasted) { for (const s of S.seeds) if (m.entities[s.name]) { ui.cooling.add(s.name); ui.lit.delete(s.name); } }
                repaint(); receiptCard(r); cameraAll(dur(900));
                break;
            }
            default: break;
        }
    }

    function seedOwner(name) {
        const m = S.model; if (!m) return null;
        if (m.entities[name]) return name;
        const parent = String(name).split('.').slice(0, -1).join('.');
        const e = m.entities[parent] || m.entities[m.byLeaf[_leaf(parent)]];
        return e ? e.name : null;
    }
    function threadMap() {
        // question word → the seed it reached. A seed names the TERMS that matched it (the scoring
        // walk's own record); a question word matches a term by its singular form as well, so
        // "robberies" reaches the seed matched by "robbery". A seed reached only by meaning keeps
        // its own terms and, when it has none, hangs off no word at all: it is shown at the
        // entity with its similarity instead of being pinned to an unrelated phrase.
        const byTerm = {};
        const sim = (name) => { const b = S.model && S.model.semantic && (S.model.semantic.bridged || []).find(x => x.name === name); return b ? b.score : null; };
        for (const s of S.seeds) {
            if (s.channel === 'product') continue;
            const parent = s.name.split('.').slice(0, -1).join('.');
            const ent = S.model && (S.model.entities[s.name] || S.model.entities[S.model.byLeaf[_leaf(parent)]]);
            const inField = (n) => !!field.byName[n];
            const target = ent ? ent.name : inField(s.name) ? s.name : inField(parent) ? parent : null;
            if (!target) continue;
            const attr = ent ? (!S.model.entities[s.name] ? _leaf(s.name) : null) : (target === parent ? _leaf(s.name) : null);
            // one thread for EVERY seed a word reached — a word that lit three boxes draws three
            // threads; drawing only its first match left the frame unable to explain its own lighting
            for (const t of (s.terms || [])) {
                const list = byTerm[t] = byTerm[t] || [];
                if (!list.some(h => h.name === target && h.attr === attr)) list.push({ name: target, attr, channel: s.channel, score: s.channel === 'semantic' ? sim(s.name) : null, seedName: s.name });
            }
        }
        const all = (word) => {
            const a = byTerm[word] || [], b = word !== _singular(word) ? (byTerm[_singular(word)] || []) : [];
            const out = a.slice(); for (const h of b) if (!out.some(x => x.name === h.name && x.attr === h.attr)) out.push(h);
            return out;
        };
        return { byTerm, all, lookup: (word) => all(word)[0] || null };
    }
    function _singular(w) { w = String(w || '').toLowerCase(); if (w.endsWith('ies') && w.length > 4) return w.slice(0, -3) + 'y'; if (w.endsWith('ses') || w.endsWith('xes')) return w.slice(0, -2); if (w.endsWith('s') && !w.endsWith('ss') && w.length > 3) return w.slice(0, -1); return w; }
    // The attributes of `e` the question seeded (the entity itself when it is the seed), each
    // with the words that reached it, then its key rows — the seed rows lead.
    function seedRows(e) {
        if (!e || !S.model) return e ? keyRows(e) : [];
        const rows = [];
        for (const sd of S.seeds) {
            if (sd.channel === 'product') continue;
            const owner = String(sd.name).split('.').slice(0, -1).join('.');
            if (owner !== e.name && S.model.entities[owner] !== e) continue;
            const attr = _leaf(sd.name);
            if (rows.some(r => r.name === attr)) continue;
            const words = (sd.terms || []).join(', ');
            rows.push({ name: attr, role: 'seed', detail: words || (sd.channel === 'semantic' ? 'by meaning' : ''),
                tip: `${attr} — seeded${words ? ` by ${words}` : ''}${(sd.reasons || []).length ? ` (${sd.reasons.join(' · ')})` : ''}` });
        }
        const seen = new Set(rows.map(r => r.name.toLowerCase()));
        return rows.concat(keyRows(e).filter(r => !seen.has(r.name.toLowerCase())));
    }
    function keyRows(e) {
        if (!e) return [];
        const keys = e.keys.length ? e.keys : e.fks;
        return keys.map(k => ({ name: k, role: 'key', detailInspect: (e.attrs[k] || {}).type || '', tip: `${k} — part of ${e.label}'s key` }));
    }
    function statementRows(e, roles) {
        const rows = keyRows(e);
        const seen = new Set(rows.map(r => r.name.toLowerCase()));
        const table = e.depLeaf || e.leaf;
        for (const [k, role] of Object.entries(roles.roles)) {
            const [t, col] = k.split('.'); if (t !== table && t !== e.leaf) continue;
            if (seen.has(col)) continue; seen.add(col);
            const fact = e.attrs[Object.keys(e.attrs).find(a => a.toLowerCase() === col)] || {};
            let detail = '', tip = '';
            if (role === 'filter') {
                const f = roles.filters.find(x => x.col === col && (x.table === t));
                const lit = f ? f.literal : '';
                const label = fact.coded && f ? decode(e, col, lit) : null;
                detail = `${f ? f.op : ''} ${lit}${label ? `  ${label}` : ''}`.trim();
                tip = fact.coded ? `${col} is a coded attribute — compared as ${/^'/.test(lit) ? 'a quoted code' : 'a bare literal'}${label ? `; ${lit} = ${label}` : ''}` : `${col} ${f ? f.op : ''} ${lit}`;
            } else if (role === 'dimension') { detail = fact.semantic === 'date' ? (depth === 'inspect' && fact.shape ? `${fact.shape}` : 'date') : ''; tip = `grouped by ${col}${fact.shape ? ` — a date stored as text in the shape ${fact.shape}` : ''}`; }
            else if (role === 'measure') { const mm = roles.measures.find(x => x.col === col); detail = mm ? `${mm.fn.toLowerCase()} · measure` : 'measure'; tip = `the measure — the grain of the answer is ${e.label}`; }
            else if (role === 'key') { detail = 'join'; tip = `${col} joins ${e.label} across a relationship`; }
            rows.push({ name: col, role, detail, detailInspect: (role === 'filter' && /[A-Za-z]/.test(detail.replace(/^[=<>!]+\s*'[^']*'/, ''))) ? detail : [detail, fact.type].filter(Boolean).join(' · '), tip });
        }
        return rows;
    }
    function decode(e, col, lit) {
        // the value domain the enrichment declared for this column: '120' → Robbery
        const v = String(lit || '').replace(/^'|'$/g, '');
        const doms = (S.model && S.model.valueDomains) || {};
        const d = (doms[e.depLeaf] || doms[e.leaf] || {})[col];
        if (!d || !d.values) return null;
        const hit = d.values[v] ?? d.values[String(Number(v))];
        return hit != null ? String(hit) : null;
    }
    function setRows(name, rows) { ui.rowsByEntity[name] = rows; }

    // ── Inspect interactions: scrub the story, preview a beat, ask why ──
    // A click on a beat, a statement or Replay is the VIEWER's act: the "viewer's hand wins for
    // a while" guard (cameraTo / cameraField) exists so the story never yanks the camera away
    // from someone zooming — it must not block the re-fit their own click asks for. Left in
    // place, a scrub rebuilt the whole arrangement under the old zoom: boxes off screen,
    // threads and labels placed against a frame that no longer existed.
    function yieldCamera() { ui.userMovedAt = null; ui.cameraSet = false; }
    // A held frame is THE STORY SO FAR, ON THE MAP. The reducer knows the whole turn by the
    // time the stage holds, so the arrangement is known even at Understand or Seed — the
    // live opening (the whole-graph field, seeds igniting, the gather at the model) is a
    // choreography for a turn unfolding in time, not a frame to inspect. Holding on the raw
    // field read as a broken layout beside Inject's boxes.
    function holdFrame() {
        if (!S.model) { cameraAll(0); return; }
        computeLayout(S.model);
        if (field.nodes.length && !field.gathered) renderField('gather');
        repaint();
        // threads belong to Seed and later: a hold at Understand has seeded nothing yet
        const pastSeed = ui.stageBeat && BEAT_IDX[ui.stageBeat] >= BEAT_IDX.seed;
        const pos = renderQuestion(pastSeed ? threadMap() : null);
        if (pastSeed) drawThreads(pos); else { if (gThreads) gThreads.selectAll('*').remove(); ui.threadPositions = null; ui.threadRects = []; }
        if (gQuestion) gQuestion.classed('kgt-q-focus', ui.stageBeat === 'understand' && !!(S.terms || []).length);
        if (svg) svg.classed('kgt-v-focus', ui.stageBeat === 'verify');
        cameraAll(0);
    }
    function scrubTo(beat, stId) {
        const src = ((ui.events && ui.events.length) ? ui.events : (_replaySource || [])).slice(); const endMeta = ui.endMeta; if (!src.length) return;
        unpinTip(); yieldCamera(); keepClock();
        ui.scrubbing = true; api.reset(); ui.scrubTo = beat; ui.scrubSt = stId != null ? stId : null;
        // Dispatch the WHOLE turn to the reducer first, then perform the ops up to the target:
        // performed as they arrived, the Seed/Expand ops ran before the model existed and took
        // their model-less branches (dots ignited on the field; Expand a no-op). Deferred, they
        // perform as they do on a replay — with the model known — so the held frame is the map.
        ui.deferPlay = true;
        try { for (const [t, p] of src) api.dispatch(t, p); restoreClock(); if (endMeta) api.end(endMeta); ui.deferPlay = false; playNext(); }
        finally { ui.deferPlay = false; ui.scrubTo = null; ui.scrubSt = null; ui.scrubbing = false; ui.events = src; ui.endMeta = endMeta; }
        if (!ui.held) { ui.held = beat; ui.heldSt = stId != null ? stId : null; }   // the target was the last beat: nothing waits, but the frame is held all the same
        holdFrame();
        if (ui.stripShown) strip(ui.stripShown.st, ui.stripShown.danger);   // a statement performed mid-scrub knew only the statements before it; now the whole turn is known
        cameraAll(0); refreshChrome();
    }
    function scrubToStatement(id) { scrubTo('execute', id); }
    function release() { ui.held = null; ui.heldSt = null; yieldCamera(); refreshChrome(); if (ui.queue.length) playNext(); else cameraAll(rm ? 0 : 300); }
    function statementEntities(st) { const m = S.model; const set = new Set(); if (!m || !st) return set; for (const t of (((st.roles || {}).tables) || [])) { const e = m.entities[m.byLeaf[t]]; if (e) set.add(e.name); } return set; }
    function statementStatus(st) {
        const applied = S.consequences.filter(c => c.kind === 'applied' && c.statement === st.id).length;
        const prevented = S.consequences.some(c => c.kind === 'prevented' && c.statement === st.id);
        return { applied, prevented, cls: prevented ? 'bad' : (st.result && st.result.ok === false) ? 'bad' : applied ? 'ok' : '' , word: prevented ? 'stopped' : applied ? `${applied} applied` : st.result ? (st.result.ok ? 'ran' : 'failed') : 'running' };
    }
    function statementChips(current) {
        if (S.statements.length < 2) return '';
        return `<span class="stmts">${S.statements.map(st => { const x = statementStatus(st); return `<button type="button" class="st ${x.cls} ${current && current.id === st.id ? 'on' : ''} u-tip" data-st="${st.id}" data-tip="Statement ${st.id} of ${S.statements.length} · ${x.word} — click to hold the story there">${st.id}</button>`; }).join('')}</span>`;
    }
    // ── Hover = PREVIEW the frame a click would hold; click = hold it; move away = return. ──
    // ONE mechanism for both gestures. The old hover laid a spotlight (what the beat touched)
    // over whatever frame was on stage: clicking a row with the cursor on it showed the held
    // frame PLUS the spotlight, so the picture changed the moment the mouse left the row —
    // "if I select a topic and no longer hover it, the state is changing" (2026-09-17). Now
    // hovering the held beat is a no-op, hovering another beat shows that beat's own frame,
    // and mouse-out restores exactly the frame that was held (or the finished story).
    // A live turn still playing is never previewed: a hover must not fight the animation.
    function canPreview() { return !!ui.held || (ui.ended && !ui.queue.length); }   // a hold leaves the rest of the turn queued by design; a finished story has nothing queued
    function preview(beat, stId) {
        if (!canPreview() || !S.beatsSeen.includes(beat)) return;
        const from = ui.preview ? ui.previewFrom : { held: ui.held, heldSt: ui.heldSt };
        if (from.held === beat && (stId == null ? from.heldSt == null : from.heldSt === stId)) { unpreview(); return; }   // hovering what is held: nothing to preview
        if (ui.preview === beat && (stId == null ? ui.previewSt == null : ui.previewSt === stId)) return;
        scrubTo(beat, stId);
        ui.preview = beat; ui.previewSt = stId != null ? stId : null; ui.previewFrom = from;
        refreshChrome();
    }
    function unpreview() {
        if (!ui.preview) return;
        const from = ui.previewFrom; ui.preview = null; ui.previewSt = null; ui.previewFrom = null;
        if (from.held) scrubTo(from.held, from.heldSt);
        else { const last = [...S.beatsSeen].pop(); if (last) scrubTo(last); ui.held = null; ui.heldSt = null; }   // nothing was held: the finished story, un-held
        refreshChrome();
    }
    function pinTip(x, y, text) { ui.tipPinned = false; showTip(x, y, text); ui.tipPinned = true; tip.classList.add('pinned'); }
    function unpinTip() { if (!ui.tipPinned) return; ui.tipPinned = false; tip.classList.remove('pinned'); tip.classList.remove('on'); }
    function seedWhy(seedName) {
        const s = S.seeds.find(x => x.name === seedName) || (S.model && S.model.seeds.find(x => x.name === seedName)); if (!s) return null;
        const sim = S.model && S.model.semantic && (S.model.semantic.bridged || []).find(x => x.name === s.name);
        return [s.name,
            `channel: ${s.channel === 'semantic' ? 'by meaning' : s.channel === 'product' ? 'data product' : 'by the words'}`,
            s.score != null ? `score: ${s.score}` : null,
            (s.terms || []).length ? `matched: ${s.terms.join(', ')}` : null,
            (s.reasons || []).length ? `reasons: ${s.reasons.join(' · ')}` : null,
            sim ? `similarity: ${Number(sim.score).toFixed(2)}` : null,
            s.channel === 'semantic' ? 'Reached by meaning: no question word names it, the semantic index found it near the question.' : 'The scoring walk records why the question reached this seed; the reasons are its own.',
            'click anywhere to close'].filter(Boolean).join('\n');
    }
    function rowWhy(e, r) {
        const a = (e.attrs || {})[r.name] || (e.attrs || {})[Object.keys(e.attrs || {}).find(k => k.toLowerCase() === String(r.name).toLowerCase())] || {};
        const doms = (S.model && S.model.valueDomains && (S.model.valueDomains[_leaf(e.deployed || '')] || S.model.valueDomains[_leaf(e.name)])) || {};
        const dom = doms[String(r.name).toLowerCase()]; const vals = dom && dom.values ? Object.entries(dom.values) : [];
        return [`${e.name}.${r.name}`,
            `role: ${r.role}${r.detail ? ' — ' + r.detail : ''}`,
            a.type ? `type: ${a.type}` : null, a.semantic ? `meaning: ${a.semantic}` : null, a.shape ? `shape: ${a.shape}` : null,
            a.decoded_from ? `decodes: ${a.decoded_from}` : null, a.decoded_as ? `decoded by: ${a.decoded_as}` : null, a.pii ? 'sensitivity: personal data' : null,
            vals.length ? `values: ${vals.slice(0, 8).map(([k, v]) => `'${k}' ${v}`).join(' · ')}${vals.length > 8 ? ` · +${vals.length - 8} more` : ''}` : null,
            r.tip || null, 'click anywhere to close'].filter(Boolean).join('\n');
    }
    const trailEl = root.querySelector('.kgt-trail');
    if (trailEl) {
        trailEl.addEventListener('click', (ev) => {
            const wasHeld = ui.preview ? ui.previewFrom.held : ui.held, wasHeldSt = ui.preview ? ui.previewFrom.heldSt : ui.heldSt;
            ui.preview = null; ui.previewSt = null; ui.previewFrom = null;   // the click commits: whatever is on stage is now the hold
            const srow = ev.target.closest('.kgt-tr-st'); if (srow) { const id = Number(srow.dataset.st); if (wasHeldSt === id) release(); else if (!(ui.held === 'execute' && ui.heldSt === id)) scrubToStatement(id); else refreshChrome(); return; }
            const row = ev.target.closest('.kgt-tr'); if (!row || !row.classList.contains('known')) return; const b = row.dataset.beat;
            if (wasHeld === b && wasHeldSt == null) release(); else if (!(ui.held === b && ui.heldSt == null)) scrubTo(b); else refreshChrome();
        });
        trailEl.addEventListener('mouseover', (ev) => { const srow = ev.target.closest('.kgt-tr-st'); if (srow) { preview('execute', Number(srow.dataset.st)); return; } const row = ev.target.closest('.kgt-tr'); if (row && row.classList.contains('done')) preview(row.dataset.beat); });
        trailEl.addEventListener('mouseout', (ev) => { const row = ev.target.closest('.kgt-tr-st, .kgt-tr'); if (row && !row.contains(ev.relatedTarget)) unpreview(); });
    }
    const _docClick = (ev) => { if (ui.tipPinned && !tip.contains(ev.target)) unpinTip(); };
    const _docKey = (ev) => { if (ev.key === 'Escape') unpinTip(); };
    document.addEventListener('click', _docClick); document.addEventListener('keydown', _docKey);

    // ── controls ──
    root.querySelectorAll('.kgt-pill').forEach(p => p.onclick = () => setDepth(p.dataset.depth));
    root.querySelector('.kgt-graph').onclick = () => { if (opts.onExit) opts.onExit(); };
    root.querySelector('.kgt-replay').onclick = () => replay();
    root.querySelector('.kgt-key').onclick = () => setPref('legend', !ui.legend);
    root.querySelector('.kgt-explain-btn').onclick = () => setPref('explain', !ui.explain);
    root.classList.toggle('legend', ui.legend); root.classList.toggle('explain', ui.explain);
    root.querySelector('.kgt-key').setAttribute('aria-pressed', ui.legend ? 'true' : 'false'); root.querySelector('.kgt-explain-btn').setAttribute('aria-pressed', ui.explain ? 'true' : 'false');
    legend();
    root.querySelector('.kgt-strip').onclick = (ev) => { const chip = ev.target.closest('.st'); if (chip) { const id = Number(chip.dataset.st); if (ui.heldSt === id) release(); else scrubToStatement(id); return; } const el = root.querySelector('.kgt-strip'); el.classList.toggle('open'); ui.cameraSet = false; cameraAll(rm ? 0 : 300); };
    function setDepth(d) {
        depth = d === 'inspect' ? 'inspect' : 'watch';
        root.classList.toggle('inspect', depth === 'inspect'); root.classList.toggle('watch', depth === 'watch');
        try { localStorage.setItem('kg_theatre_depth', depth); } catch (_) {}
        if (S.question) ui.threadPositions = renderQuestion(threadMap()) || ui.threadPositions;
        if (S.model) { computeLayout(S.model); repaint(); ui.cameraSet = false; cameraAll(rm ? 0 : 400); }
        refreshChrome();
        if (opts.onDepth) opts.onDepth(depth);
    }

    // A replay or a scrub re-reduces the same turn: the stage re-performs it, but the
    // seconds stay the LIVE run's — re-stamping them at the burst read "0.00 s" on every beat.
    function keepClock() { if (!ui.keepTimes && S.timesReal && Object.keys(S.stageTimes).length && timesCoherent()) ui.keepTimes = { times: { ...S.stageTimes }, t0: S.startedAt }; }
    function restoreClock() { if (ui.keepTimes) { S.stageTimes = { ...ui.keepTimes.times }; S.startedAt = ui.keepTimes.t0; S.timesReal = true; } else S.timesReal = false; }
    function replay() {
        const src = (ui.events && ui.events.length) ? ui.events.slice() : _replaySource; if (!src || !src.length) return;
        const endMeta = ui.endMeta;
        _replaySource = src;
        yieldCamera(); keepClock();
        ui.replaying = true;
        try { api.reset(); for (const [t, p] of src) api.dispatch(t, p); restoreClock(); api.end(endMeta || { replay: true }); }
        finally { ui.replaying = false; }
        refreshChrome();
    }

    let _roLast = null;
    const ro = (typeof ResizeObserver !== 'undefined') ? new ResizeObserver(() => { const r = root.querySelector('.kgt-stage').getBoundingClientRect(); const key = `${Math.round(r.width)}x${Math.round(r.height)}`; if (key === _roLast) return; _roLast = key; size(); if (S.question) ui.threadPositions = renderQuestion(threadMap()) || ui.threadPositions; if (S.model) { computeLayout(S.model); repaint(); ui.cameraSet = false; cameraAll(rm ? 0 : 400); } else if (field.nodes.length && !field.gathered) { if (ui.threadPositions) drawThreads(ui.threadPositions); cameraField(rm ? 0 : 400); } }) : null;
    if (ro) ro.observe(root.querySelector('.kgt-stage'));

    const api = {
        dispatch(type, payload) { if (!ui.scrubbing) (ui.events = ui.events || []).push([type, payload]); const ops = reducer.apply(type, payload); if (ops.length) enqueue(ops); else refreshChrome(); return ops; },
        end(meta) { if (!ui.scrubbing) ui.endMeta = meta; const ops = reducer.end(meta); enqueue(ops); return ops; },
        replayFrom(events) { _replaySource = events; api.reset(); for (const [t, p] of events) api.dispatch(t, p); api.end({ replay: true }); },
        // A reloaded turn is ARMED, not played: the stage keeps its idle frame ("Waiting for
        // the question…") and offers the last turn behind the Replay control. Auto-playing a
        // historical story on every session switch displaced the idle frame entirely — and
        // narrated a session primer's briefing as if it were the question.
        arm(events) { _replaySource = (events || []).slice(); api.reset(); },
        setIdleNote(text) { opts.idleNote = text || ''; refreshChrome(); },
        reset(next) {
            if (next && next.wholeGraph !== undefined) opts.wholeGraph = next.wholeGraph;   // the field follows the graph as it is NOW
            if (next && next.live) _replaySource = null;                                   // a new turn on stage: the armed story is no longer the one to replay
            ui.timers.forEach(clearTimeout); ui.timers = []; ui.queue = []; ui.playing = false; ui.ended = false; ui.danger = false; ui.userMoved = false;
            ui.lit = new Set(); ui.solid = new Set(); ui.known = new Set(); ui.rings = new Set(); ui.cooling = new Set(); ui.drafts = new Map();   // PROP-008: name → 'draft' | 'proposed' (a dashed box) ui.relStates = new Map(); ui.rowsByEntity = {}; ui.regionsSolid = false; ui.showFolded = false; ui.marks = []; ui.used = new Set(); ui.executing = false;
            ui.markRects = []; ui.labelRects = []; ui.threadRects = []; ui.threadPositions = null; ui.threadsDrawn = false;
            ui.held = null; ui.heldSt = null; ui.scrubTo = null; ui.scrubSt = null; ui.stageBeat = null; ui.deferPlay = false; ui.preview = null; ui.previewSt = null; ui.previewFrom = null; ui.stripShown = null; ui.markIntents = []; ui.markSig = null; if (!ui.scrubbing) { ui.events = []; ui.endMeta = null; } unpinTip();
            ui.performed = new Set(); ui.performedSt = new Set(); if (!ui.scrubbing && !ui.replaying) ui.keepTimes = null;   // a new turn (or an armed reload) has its own clock
            reducer = createReducer(); S = reducer.state;
            layout.pos = {}; layout.areas = {}; layout.anchored = false; layout.dx = 0; layout.dy = 0; ui.cameraSet = false;
            initStage(); field.gathered = false; field.shown = false; buildField(opts.wholeGraph); if (field.nodes.length) { renderField('field'); cameraField(0); } strip(null); refreshChrome();
        },
        setDepth, get depth() { return depth; }, get state() { return S; },
        get stageBeat() { return ui.stageBeat || null; }, get pending() { return ui.queue.length; },
        beatView, get prefs() { return { legend: ui.legend, explain: ui.explain }; },   // what the stage shows vs what is still queued (the gate reads these)
        destroy() { ui.timers.forEach(clearTimeout); if (ro) ro.disconnect(); document.removeEventListener('click', _docClick); document.removeEventListener('keydown', _docKey); tip.remove(); root.remove(); },
        root,
    };
    initStage(); buildField(opts.wholeGraph); if (field.nodes.length) { renderField('field'); cameraField(0); } refreshChrome();
    return api;
}
