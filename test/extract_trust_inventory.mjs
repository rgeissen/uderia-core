#!/usr/bin/env node
/**
 * Trust-surface inventory extractor (migration P4).
 *
 * WHY THIS EXISTS
 * ---------------
 * The migration's first imperative is that no information previously provisioned is
 * lost. A hand-written inventory cannot enforce that: the first draft of the migration
 * ledger listed 6 rows for `agentAnatomy.js` where extraction found 19 panel slots, and
 * missed a search-field handler entirely. Extraction alone is no better — the first
 * extractor reported zero tooltips for two files because they use a different
 * construction. So neither method is trusted alone, and the rule is:
 *
 *   the inventory is EXTRACTED, reconciled against a committed baseline,
 *   and a mismatch in either direction is a build failure.
 *
 * THE CLAUSE THAT MATTERS
 * -----------------------
 * `fail loudly, never skip`. Every checker written for this design passed while the
 * picture was visibly broken, because each silently ignored what it could not see. An
 * extractor that shrugs is worse than none: it launders a gap into a green tick. So an
 * unclassifiable construct is a FAILURE, not a warning and not a silent omission.
 *
 * USAGE
 *   node test/extract_trust_inventory.mjs             # verify against the baseline
 *   node test/extract_trust_inventory.mjs --write     # (re)write the baseline
 *   node test/extract_trust_inventory.mjs --json      # emit the raw inventory
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASELINE = path.join(ROOT, 'test', 'trust_inventory.baseline.json');

// The seven surfaces the design language reaches. A file added to the trust surface
// without being added here is invisible to this gate — so the list is asserted against
// the directory below, and an unlisted trust-looking file fails.
const SURFACES = [
    // OBDA-ACT (PROP-008 §5.6) — the Actions Designer: one row per signed action read as a journey
    // (validated · signed · granted · in use), the A1–A6 checklist with the evidence behind
    // each check, the trust chip, and the grant per agent. A trust surface: each step is a
    // fact about whether a write contract may be used, and losing one silently would let an
    // unsigned or ungranted contract read as ready.
    'static/js/handlers/actionDesigner.js',
    'static/js/handlers/trustJourney.js',
    'static/js/handlers/answerCapsule.js',
    'static/js/handlers/trustCircle.js',
    'static/js/handlers/agentPerformanceHandler.js',
    'static/js/handlers/agentAnatomy.js',
    'static/js/handlers/trustBadge.js',
    'static/js/handlers/qualityDimensions.js',
    'static/js/handlers/agentKpiCharts.js',   // the Agents lens's KPI charts
    'static/js/handlers/assuranceDial.js',    // R-2: the dial (strip + capsule)
    // PROP-003 — the binding studio. A real surface, not a config form: every cell is a
    // trust reading (what assesses this property · nothing bound · absent by design ·
    // bound but unconsulted · this module has drifted), and each of those sentences is a
    // fact that must not be lost silently.
    'static/js/handlers/assuranceBindings.js',
    // S3 — Compute with Lineage: the figure-lineage dialog. A trust surface, not a viewer:
    // every line in it is a provenance fact about a number the reader is acting on (which
    // operation, over how many rows, from which statement, the receipt hash, the rows
    // themselves), and losing one silently would be losing the lineage the mark promises.
    'static/js/handlers/figureLineage.js',
    // S8 — Regenerate as an Experiment: the experiment card + the branch switch. A trust
    // surface: every sibling row carries the Scorecard's verdict strip (the ONE band grammar,
    // drawn by the Agents lens's own mark builder) and the words that count what needs you —
    // the reason to prefer one answer over another. Losing a mark or its word silently would
    // be losing the comparison the card exists for.
    'static/js/handlers/turnLineage.js',
    // S9 — Grounded Multimodal: what the platform READ from an attachment, on every surface
    // the attachment appears (composer chip, user bubble, the capsule's Attachments row, the
    // lineage dialog). A trust surface: the badge is the verified / unverified / unread state
    // in words, the crop is the cited region, the alt text is the extraction itself — losing
    // one silently would let a model transcription read as a platform-verified source.
    'static/js/handlers/attachmentSources.js',
    // S1.1 — the Living Answer: the streaming bubble's lifecycle, live and reloaded. A trust
    // surface: each check the platform ran is a ROW of the receipt in words (Checked against its
    // sources · in scope), and the ONE verdict chip on the action row (Verified · N findings ·
    // Not checked — never "clean", never "verified" with no check run) carries every check it
    // rests on in its tooltip — losing one silently would let a flagged turn read as a quiet one.
    'static/js/handlers/livingAnswer.js',
    // The KG execution theatre: the context engine narrating, per turn, what it did and what
    // it was worth. A trust surface, not a visualisation — its consequence layer is read from
    // the trace and never inferred, and it speaks in verdicts: which facts from the model were
    // APPLIED, which statement a control STOPPED (carrying the refusal's own sentence), which
    // seeds were WASTED, and a receipt beat that states the turn's verdict (what was applied,
    // what was stopped, whether the seeds were used and the scope held). Losing one of those
    // sentences silently would let a turn a control acted on narrate as a quiet one.
    'static/js/handlers/kgTheatre.js',
    // PROP-009 — Evidence: one answer's signed parts, each read "when answered · now". A trust
    // surface: every pair is a fact about whether a part was signed and intact when the answer
    // was given and whether it still is, and the summary names the parts that need a look —
    // losing one silently would let an answer produced with a changed or unsigned part read
    // as fully evidenced.
    'static/js/handlers/evidenceView.js',
];

// Files that LOOK like trust surfaces but are deliberately out of scope. Recorded so
// the omission is a decision, not a gap — the same rule the assurance registry follows.
const NOT_A_SURFACE = {
    // PROP-002 — the ONE trust vocabulary (words, ranks, band/severity aliases).
    // It renders nothing itself: every word it owns is inventoried on the surface
    // that prints it (ring legend, dial chips, inbox rows). Listing it as a
    // surface would double-count every word on every consumer.
    'static/js/handlers/trustGrammar.js':
        'the shared vocabulary module — renders no DOM of its own; its words are '
        + 'inventoried where they print.',
    // The component library (Components view): its cards' facts are component metadata. The one
    // `u-tip` it renders is the instances pill's sentence (whose the things a component keeps
    // are) — configuration, not a trust reading.
    'static/js/handlers/componentHandler.js':
        'the component library cards — component metadata; its only tip is the instances pill '
        + '(whose the things a component keeps are), not a trust reading.',
    'static/js/handlers/agentPackHandler.js':
        'a marketplace surface. It HOSTS a trust badge, but the badge is rendered by '
        + 'trustBadge.js (already a surface); its own facts are pack metadata.',
    // S9 — the composer's attachment chips HOST the read badge + thumbnail, both rendered by
    // attachmentSources.js (a surface); its own tip is the filename.
    // S10 — steer pins + the reconnect note are conversation controls, not trust readings; the
    // one recorded fact (where a steer was considered) renders as the Live Status step in ui.js.
    'static/js/handlers/steerReattach.js':
        'conversation controls (steer pins, the reconnect note); the considered-where fact is '
        + 'the Live Status step, inventoried in ui.js.',
    'static/js/handlers/chatDocumentUpload.js':
        'hosts the attachment-source badge and thumbnail (rendered by attachmentSources.js); '
        + 'its own facts are file name and size.',
    // PROP-006 — configuration editors for an agent's data sources and the deployment's
    // combining rules. Their facts are configuration (names, endpoints, rules); the one
    // trust-adjacent line — re-attest after a change — is a pointer to the badge, whose
    // readings are inventoried in trustBadge.js. The combine's READINGS (sources per
    // statement, the second hop) print in ui.js and figureLineage.js, both surfaces.
    'static/js/handlers/dataSources.js':
        'the Data sources configuration section — configuration facts only; the re-attest note '
        + 'points to the trust badge (trustBadge.js), the combine\'s readings print in ui.js / figureLineage.js.',
    // PROP-010 — Files. Keep acts, the From Files picker and the attachment chips' Keep carry file
    // facts (names, sizes, kept or not); the admin tabs carry configuration (stores, policy,
    // capacity). The file's provenance — including the evidence of its answer — renders through
    // EvidenceView (a surface) inside the Files panel (a component renderer).
    'static/js/handlers/filesKeep.js':
        'the Keep acts and the From Files picker — file facts (name, kept or not); no trust reading.',
    'static/js/handlers/fileStoreAdmin.js':
        'the Files admin tabs (stores, policy, capacity) — configuration facts, no trust reading.',
    'static/js/handlers/combineRulesAdmin.js':
        'the administrator\'s combining-rules editor — configuration (endpoints, rules), no trust reading.',
    'static/js/handlers/agentPackGrouping.js':
        'pure grouping/layout for the pack list — renders no trust fact of its own.',
    // The platform tooltip singleton. It renders a tip's DOM, but every WORD in it
    // comes from the `data-tip` of the surface that raised it — already inventoried
    // there. Listing it would double-count every tip on every consumer, which is the
    // same reasoning that keeps trustGrammar.js off the list.
    'static/js/handlers/uTip.js':
        'the shared tooltip renderer — its content is each surface\'s own data-tip, '
        + 'inventoried where it is written.',
    // Same call as the pack surfaces: each MOUNTS a badge whose every fact comes from
    // trustBadge.js. Listing them as surfaces would double-count those facts and make a
    // badge change look like nine independent losses.
    'static/js/handlers/skillHandler.js': 'hosts a trust badge; its own facts are skill metadata.',
    'static/js/handlers/extensionHandler.js': 'hosts a trust badge; own facts are extension metadata.',
    'static/js/handlers/knowledgeGraphPanelHandler.js': 'hosts a trust badge; own facts are KG metadata.',
    'static/js/handlers/knowledgeRepositoryHandler.js': 'hosts a trust badge; own facts are repo metadata.',
    'static/js/handlers/plannerHealth.js':
        'Planner Health: every reading it draws is the planner gate\'s own manifest '
        + '(validators.planner_scan_manifest — findings, counts, verdict), inventoried where it is written.',
    'static/js/handlers/marketplaceHandler.js': 'hosts a read-only trust seal on listing cards.',
    'static/js/handlers/workspacePanelHandler.js': 'hosts a trust badge on instance rows.',
    'static/js/handlers/ontologyPanelHandler.js':
        'the estate map. It carries a trust LENS, but every reading it draws comes from '
        + 'the evals payload already covered by agentPerformanceHandler.js.',
    'static/js/handlers/configurationHandler.js':
        'the configuration UI. It holds POSTURE_MODES — the ladder mirror the contract '
        + 'names in §6 — but that ONE construct is already gated by the grammar gate '
        + '(TC-50/pickMode). Listing the whole file would bury the inventory in profile, '
        + 'MCP and LLM configuration facts, and impose the trust-tooltip rule on 25 '
        + 'general-config tooltips that correctly use the platform convention. A gate '
        + 'that drowns its signal is a gate nobody reads.',
    'static/js/handlers/thresholdSliders.js':
        'the admin settings page. Matched only on a tooltip class; its numbers already '
        + 'have a one-source rule with QualityDimensions.THRESHOLD_SPEC (contract §6).',
};

const failures = [];
const fail = (msg) => failures.push(msg);

// ── classification ────────────────────────────────────────────────────────────
// Each rule names a construct we UNDERSTAND. Anything matching the catch-all
// `data-*` / listener shapes but no rule lands in `unclassified` and fails the run.
const RULES = [
    { kind: 'handler',   re: /addEventListener\(\s*'([a-z]+)'/g,          cap: 1 },
    { kind: 'route',     re: /data-(?:agent|tc|ap|an|qd|ac)-(?:action|nav|act|link|open-session|tune|scope)\s*=/g },
    { kind: 'route',     re: /\bdata-(?:tab|stage|session|turn|event-id|review-id|dim-guide)\s*=/g },
    { kind: 'route',     re: /\bdata-trust-artifact-(?:kind|id)\s*=/g },
    // Gesture targets found by extraction, each read at its site before classifying.
    { kind: 'route',     re: /\bdata-(?:tc-ch|circle-key|roster-filter|config-tab)\s*=/g },
    { kind: 'route',     re: /\bdata-ap-(?:runfilter|runkey|cmpmetric)\s*=/g },
    { kind: 'route',     re: /\bdata-an-(?:child|pill|row)\s*=/g },
    { kind: 'route',     re: /\bdata-action\s*=/g },
    // A KPI tile that navigates to its section (delegated in agentPerformanceHandler).
    { kind: 'route',     re: /\bdata-goto-section\s*=/g },
    // R-2 dial: a station is a gesture target; its section is the thing revealed.
    { kind: 'route',     re: /\bdata-ad-(?:station|gate)\s*=/g },
    // The intervention card's identity: it names WHICH mechanism the card attests
    // (preflight / retrieval firewall / review parking) and is the idempotence key —
    // a fact-carrying attribute, not chrome.
    { kind: 'fact-attr', re: /\bdata-ad-intervention\s*=/g },
    { kind: 'state-key', re: /\bdata-ad-(?:section|read|dial)\s*=?/g },
    // Assurance Guide facet filters (kind / measured-by / this-turn status).
    { kind: 'route',     re: /\bdata-qd-facet(?:-v)?\s*=/g },
    // The Guide's id mark: copies an entry's wire key (the key stays in the DOM for search).
    { kind: 'route',     re: /\bdata-qd-copy\s*=/g },
    { kind: 'state-key', re: /\bdata-dim-filter\s*=/g },
    { kind: 'state-key', re: /\bdata-(?:tc-station|an-key|qd-key|ap-turn)\s*=/g },
    // Which subject a route acts on — losing one silently rewires a gesture.
    { kind: 'state-key', re: /\bdata-(?:run-id|doc-id|profile-id|dim)\s*=/g },
    { kind: 'state-key', re: /\bdata-trust-agent-(?:id|tag)\s*=/g },
    // Rendered data. `data-dim-context` carries the scoped-guide JSON payload;
    // `data-scroll` is the stage's scroll-to-card text; `data-dim-note` the runtime note.
    { kind: 'fact-attr', re: /\bdata-dim-context(?:-label|-cls)?\s*=/g },
    { kind: 'fact-attr', re: /\bdata-(?:scroll|dim-note)\s*=/g },
    // JUDGED's members on the dial: while the stage vocabulary loads, the measures' verdicts
    // ride the list as JSON (`data-verdicts`) and are redrawn once the stage names arrive —
    // rendered data in transit, the same category as `data-dim-context`.
    { kind: 'fact-attr', re: /\bdata-verdicts\s*=/g },
    // S8 — the experiment card: which answer a row / an Adopt / a Show acts on, which branch
    // a switch activates, which profile a Regenerate menu item runs under — each is a route
    // (losing one silently rewires a gesture); `data-band` on the lens-less fallback glyph
    // carries the band a mark would have drawn (a fact); `data-score-host` is only the slot
    // a strip is written into (benign, listed below).
    { kind: 'route',     re: /\bdata-(?:head|pid)\s*=/g },
    { kind: 'fact-attr', re: /\bdata-band\s*=/g },
    // S9 — `data-state` on the read badge is the verified/unverified/unread fact in machine
    // form; `data-region` on a crop is the cited region; `data-as-used` on a chip records
    // that the turn used it. `data-as-thumb` / `data-as-crop` / `data-as-local` / `data-w` /
    // `data-h` / `data-as-done` are hydration plumbing (benign, listed below).
    { kind: 'fact-attr', re: /\bdata-(?:state|region|as-used)\s*=/g },
    // `data-file-id` names WHICH upload a thumbnail, crop or source entry belongs to — the
    // key the content endpoint is asked with and the key `markUsed` decorates by (a route).
    { kind: 'route',     re: /\bdata-file-id\s*=/g },
    // S1.1 depth 3 — `data-la` is the key a Live Status card wears for the receipt row that
    // produced it (written by the receipt's reducer onto the event, stamped at the card's
    // creation): the route a row takes to its card, by identity rather than by words.
    { kind: 'route',     re: /\bdata-la\s*=/g },
    { kind: 'tip-trust', re: /\bdata-tip\s*=/g },
    { kind: 'tip-platform', re: /\bdata-tooltip\s*=/g },
    { kind: 'aria',      re: /\baria-(?:label|pressed|expanded|hidden|live|current)\s*=/g },
    { kind: 'native-title', re: /\btitle\s*=\s*"/g },
    { kind: 'fact-text', re: /\.textContent\s*=/g },
    { kind: 'fact-html', re: /\.innerHTML\s*=/g },
    { kind: 'delegate',  re: /\.closest\(\s*'([^']+)'/g,                  cap: 1 },
];

// Constructs that exist but carry no user-visible fact or route. Named explicitly so a
// NEW unnamed construct still fails — an allow-list, never a wildcard.
const BENIGN_DATA_ATTRS = new Set([
    'data-theme', 'data-ac-sid', 'data-ac-turn', 'data-agent-tag', 'data-key',
    'data-open', 'data-idx', 'data-state', 'data-role', 'data-kind', 'data-id',
    'data-qd-thresh', 'data-qd-search', 'data-an-mode', 'data-tc-pin',
    'data-session-id', 'data-turn-id',
    // PROP-011 Needs input: the Inbox's question rows are the needs_input component's own
    // cards — `data-question` is the record the card is built FROM (the card renders every
    // fact of it, in the component's renderer, which the inventory reads as a component,
    // not a trust surface) and `data-count` is the block's row count. Request plumbing.
    'data-question', 'data-count',
    // PROP-009 Evidence: the artefact's label handed to the trust record it opens — the
    // same label already printed on the row, so it carries no fact of its own.
    'data-name',
    // The inbox's batch action payload: WHICH items a click acts on (a stored id, or
    // the (session, turn, event_type) key of a derived observation). Same category as
    // the derived-ack key below — request plumbing, not a rendered fact. Every item it
    // names is already inventoried as the row that offers the button.
    'data-batch',
    // OBDA-ACT — a selector on a parked-write Inbox row (`data-review-source="action"`), naming
    // which renderer drew it; the row's facts (the diff, the act) are inventoried as text.
    'data-review-source',
    // …and the Actions Designer's form plumbing: which agent a grant toggle edits
    // (`data-profile`, the agent's tag is the visible label beside it) and which column a
    // try-it field fills (`data-col`, named in the field's own label).
    'data-profile', 'data-col',
    // …and its Granted-to filter: WHICH agent class a pill, a group or an agent row belongs to
    // (`data-cls`, an IFOC key; the class's label and colour are what renders).
    'data-cls',
    // …and its access view: WHICH action an access block belongs to (`data-access-for`, the
    // action's name is printed on its row) and the copy payload of "Copy the grants"
    // (`data-grants`, the same GRANT statements printed in the block above the button).
    'data-access-for', 'data-grants',
    // S1.1 — the Living Answer's keys: WHICH rail segment (`data-phase`, its reading is the
    // data-tip beside it), WHICH step a chip row is (`data-chip`, its words are fact-text),
    // and a counter's target for the count-up (`data-n`, the same number the span prints).
    // Keys, not facts; every fact they key is inventoried where it renders.
    'data-phase', 'data-chip', 'data-n',
    // The module a badge or a module name belongs to. It carries neither a fact nor a
    // route — the route is `data-dim` beside it, and the module's NAME is inventoried as
    // fact-text where it renders. This is only the key that lets the two ends of a shared
    // assessment find each other when one is hovered.
    'data-mod',
    // The answer capsule's Drafts tab list: which draft a tab selects (an array index, the
    // tab's aria-controls names the panel). The draft's TEXT, its findings and the verdict
    // chip are inventoried where they render; this is only the roving-tabindex key.
    'data-i',
    // The Live Status filter pill's own category key, read by the Living Answer's receipt
    // when a row's card is hidden behind a filter: it looks the pill up and CLICKS it, so
    // the chip stays in step with what is shown. A selector, not a reading — the category's
    // word is the pill's own text, inventoried where the pill renders.
    'data-filter',
    // The KG theatre's keys. Same family as `data-phase`/`data-chip` above: WHICH beat a
    // trail row is (`data-beat`), WHICH statement a row or strip chip refers to (`data-st`),
    // WHICH depth the pill selects (`data-depth`), which words of the question are the
    // matched scoring terms (`data-term`, driving the spotlight — the words themselves are
    // fact-text where they render), and the legend swatch's own shape + state so the key is
    // drawn by the same builder as the marks it explains (`data-sw`, `data-v`, beside the
    // label, which IS fact-text). Keys, not readings; every fact they key is inventoried
    // where it renders.
    'data-beat', 'data-st', 'data-depth', 'data-term', 'data-sw', 'data-v',
    // S8 — the slot a sibling's verdict strip is written into once its score arrives; the
    // strip itself (marks + words) is inventoried where it renders.
    'data-score-host',
    // S9 — the thumbnail / crop hydration hooks and the image size the crop scales by; the
    // FACT (the region, the state) is inventoried above.
    'data-as-thumb', 'data-as-crop', 'data-as-local', 'data-as-done', 'data-w', 'data-h',
    // The judge-queue buttons' POST payload: the ruling being agreed with (data-passed,
    // frozen so a later re-score can't rewrite what the human saw) and the session it
    // came from (data-sid). Request plumbing of the `data-batch` kind — the ruling FACT
    // is inventoried as the upheld/flagged chip beside the buttons, and the session
    // ROUTE is the row's own `data-ap-open-session`.
    'data-passed', 'data-sid',
    // WHICH control a row, a select or a confirm button addresses. Request plumbing of
    // exactly the `data-batch` kind: the control's label, its act and its posture are all
    // inventoried where they render, and this only lets the select, the restore button and
    // the Apply button in one row agree about which control they are talking about.
    'data-ctl', 'data-ctl-go',
    // WHICH staged change an undo button reverts. Request plumbing of the `data-batch`
    // kind: the change itself, what it will become and any refusal are all inventoried
    // where they render; this is only the key that ties the button to its row.
    'data-undo', 'data-undo-post',
    // The studio's own view state: which guarantee is selected, which axis is being read,
    // which detail tab is open, and whether a segment is the selected one. Presentation
    // state, not a reading — every fact these reveal is inventoried where it renders, and
    // selecting a segment changes what is SHOWN, never what is true. `data-sel` follows
    // the `data-state` / `data-open` precedent above.
    'data-station', 'data-view', 'data-dtab', 'data-sel',
    // PROP-002 derived-ack button: (session, turn, event_type) is the ack KEY the
    // click posts — request plumbing, not a rendered fact (the event type the row
    // is about renders in its summary text, inventoried as fact-text).
    'data-event-type',
    // PROP-003 · the binding studio's plumbing. Each names WHICH cell or WHICH act a
    // control targets — the (property, lane) a picker opens on, the act a confirm button
    // performs, the pack a Sign button signs. None of them is a rendered fact: the
    // property's label, the module's name, the trust state and every honest-absence
    // sentence are inventoried as fact-text/fact-html where they print, and the studio
    // deliberately routes nowhere (it acts in place — a cell is not a link).
    'data-prop', 'data-lane', 'data-act', 'data-pack',
    // The trust STATE of a third-party module, as a machine key beside the word. The word
    // itself ("unsigned", "signed, but changed since") is the fact, inventoried where it
    // renders; this is what the CSS keys on.
    'data-seal',
    // The band-strip legend toggle. It routes nowhere and states nothing — it reveals a
    // KEY whose every sample is drawn by the shipped mark builder, and each of those
    // marks is already inventoried where it renders. Presentation state, not a fact.
    'data-ap-stripkey',
    // PROP-002b severity view-filter pills: the pill's own value — which severity
    // the click narrows the view to. A view lens, not a fact or a route (the
    // severities themselves render as the dot + word chip on every row).
    'data-sev',
    // …and the agent view-filter pills' tag value — the same lens, by agent (the
    // tag itself renders IFOC-coloured on every row, inventoried as fact-html).
    'data-tag',
    // Trust-trip lens controls (TRUST_TRIP_RESET.md): view-lens toggles — they
    // carry neither a fact nor a route, only which window the SAME facts are
    // read through. The facts they scope are inventoried where they render
    // (fact-html/fact-text on the ring panels and the roster detail).
    'data-tc-lens', 'data-ap-runleg',
]);

function extractFile(rel) {
    const abs = path.join(ROOT, rel);
    if (!fs.existsSync(abs)) {
        fail(`surface missing from disk: ${rel}`);
        return null;
    }
    const src = fs.readFileSync(abs, 'utf8');
    const rec = { file: rel, counts: {}, detail: {}, unclassified: [] };

    for (const rule of RULES) {
        const hits = [...src.matchAll(rule.re)];
        if (!hits.length) continue;
        rec.counts[rule.kind] = (rec.counts[rule.kind] || 0) + hits.length;
        if (rule.cap != null) {
            const vals = {};
            for (const h of hits) vals[h[rule.cap]] = (vals[h[rule.cap]] || 0) + 1;
            rec.detail[rule.kind] = Object.fromEntries(
                Object.entries(vals).sort(([a], [b]) => a.localeCompare(b)));
        }
    }

    // The fail-loudly clause: every `data-*` attribute must be explained by a rule or
    // named benign. An unknown one is a route or a fact nobody classified.
    const known = new Set();
    for (const rule of RULES) {
        if (!String(rule.re).includes('data-')) continue;
        for (const h of src.matchAll(rule.re)) known.add(h[0].replace(/\s*=$/, ''));
    }
    for (const h of src.matchAll(/\bdata-[a-z][a-z0-9-]*(?=\s*=)/g)) {
        const attr = h[0];
        if (known.has(attr) || BENIGN_DATA_ATTRS.has(attr)) continue;
        const line = src.slice(0, h.index).split('\n').length;
        rec.unclassified.push({ attr, line });
    }
    return rec;
}

// ── contract checks the extractor can see ─────────────────────────────────────
function contractChecks(inv) {
    const notes = [];
    // A standing platform rule, now at zero across every surface — so it is a FAILURE,
    // not a note. A rule that reports without failing is a rule that drifts back.
    for (const rec of inv) {
        if (rec.counts['native-title']) {
            fail(`${rec.file}: ${rec.counts['native-title']} native \`title=\` attribute(s) `
               + 'on a trust surface. Native title renders an unthemed grey box that cannot '
               + 'be styled and ignores the theme toggle.\n      → use `class="u-tip" '
               + 'data-tip="…"` (trust.css). Keep any `aria-label` beside it.');
        }
    }
    // Two tooltip systems both exist and both are legitimate — the platform's
    // glass-panel `data-tooltip` (main.css) and the trust `u-tip`/`data-tip`
    // (trust.css). This is REPORTED, never normalised: which surface belongs to which
    // system is a design decision, and an extractor that silently rewrote it would be
    // making that decision by accident.
    const trust = inv.filter((r) => r.counts['tip-trust']).map((r) => r.file);
    const plat = inv.filter((r) => r.counts['tip-platform']).map((r) => r.file);
    if (trust.length && plat.length) {
        notes.push({
            rule: 'tooltip system split (informational)',
            trustSurfaces: trust, platformSurfaces: plat,
            why: 'both systems are real; recorded so the migration decides deliberately',
        });
    }
    return notes;
}

// ── run ───────────────────────────────────────────────────────────────────────
const inventory = SURFACES.map(extractFile).filter(Boolean);

for (const rec of inventory) {
    if (rec.unclassified.length) {
        fail(`${rec.file}: ${rec.unclassified.length} unclassified construct(s) — `
           + rec.unclassified.map((u) => `${u.attr}@${u.line}`).join(', ')
           + '\n      → add a RULE (it carries a fact or a route) or list it in '
           + 'BENIGN_DATA_ATTRS (it carries neither). Never leave it unlisted: an '
           + 'extractor that shrugs launders a gap into a green tick.');
    }
}

// A trust-looking file outside SURFACES is invisible to this gate.
const dir = path.join(ROOT, 'static/js/handlers');
// Detect by CONTENT, not by name. The first version keyed on filename prefixes and
// promptly missed `assuranceDial.js` — a file that renders trust readings, added by the
// very migration this gate exists to police. A name is a convention; a `--trust-*` token
// or a trust-state class is evidence.
const TRUST_EVIDENCE = /--trust-[a-z]|\btrust-chip\b|\bu-tip\b|TrustCircle\.|TrustBadge\.|assurance/;
for (const f of fs.readdirSync(dir)) {
    const rel = `static/js/handlers/${f}`;
    if (!f.endsWith('.js')) continue;
    if (SURFACES.includes(rel) || NOT_A_SURFACE[rel]) continue;
    const body = fs.readFileSync(path.join(dir, f), 'utf8');
    if (!TRUST_EVIDENCE.test(body)) continue;
    const why = (body.match(TRUST_EVIDENCE) || [''])[0];
    fail(`${rel} renders trust readings (matched \`${why}\`) but is in neither SURFACES `
       + 'nor NOT_A_SURFACE — classify it, so its facts cannot be lost unnoticed');
}

// Every trust-surface script tag must be well-formed and resolvable. A doubled brace
// (`}}}}`) renders as literal text inside the src, the file 404s, and the module that
// depended on it silently degrades to its fallback — which is indistinguishable from
// "the feature does not work". It cost a full round trip to find by hand.
{
    const tpl = fs.readFileSync(path.join(ROOT, 'templates', 'index.html'), 'utf8');
    for (const rel of SURFACES) {
        const base = path.basename(rel);
        const tag = new RegExp(`filename='js/handlers/${base.replace('.', '\\.')}'\\)\\s*\\}\\}[^"]*"`);
        const m = tpl.match(tag);
        if (!m) continue;                     // not script-tagged (e.g. an ES module)
        if (/\}\}\s*\}\}/.test(m[0]) || /\}\}[^?"]/.test(m[0].replace(/\}\}\?/, ''))) {
            fail(`${base}: malformed script tag — ${m[0].slice(0, 70)}\n`
               + '      → a stray brace renders as literal text in the src, the file 404s, '
               + 'and every dependent feature quietly falls back');
        }
    }
}

const notes = contractChecks(inventory);
const report = { surfaces: inventory, contractNotes: notes };

const args = new Set(process.argv.slice(2));

if (args.has('--json')) {
    console.log(JSON.stringify(report, null, 2));
    process.exit(failures.length ? 1 : 0);
}

// Human table.
const W = 34;
console.log('\nTrust-surface inventory\n');
const kinds = [...new Set(inventory.flatMap((r) => Object.keys(r.counts)))].sort();
console.log('  ' + 'surface'.padEnd(W) + kinds.map((k) => k.slice(0, 9).padStart(11)).join(''));
for (const r of inventory) {
    console.log('  ' + r.file.replace('static/js/handlers/', '').padEnd(W)
        + kinds.map((k) => String(r.counts[k] || '·').padStart(11)).join(''));
}
const tot = (k) => inventory.reduce((a, r) => a + (r.counts[k] || 0), 0);
console.log('  ' + 'TOTAL'.padEnd(W) + kinds.map((k) => String(tot(k)).padStart(11)).join(''));

if (notes.length) {
    console.log('\nContract notes');
    for (const n of notes) {
        console.log(`  · ${n.rule}`);
        for (const [k, v] of Object.entries(n)) {
            if (k === 'rule') continue;
            console.log(`      ${k}: ${Array.isArray(v) ? v.join(', ') : v}`);
        }
    }
}

// ── baseline reconciliation ───────────────────────────────────────────────────
// The baseline is a DRIFT gate, not a substitute for the migration ledger: it makes any
// change to the fact/route surface visible as a diff that someone must classify. It
// cannot tell you a row was right — only that it changed.
//
// LEDGER — drops recorded at the baseline of Aug 2026 (Assurance Inbox rework).
// A DROP is potential reach loss and must be justified here before re-baselining:
//
//   qualityDimensions.js · delegate 15 → 16  (GROWTH, no justification owed)
//     Assurance Guide, Oct 2026: one tooltip at a time — the hover handler looks for a mark
//     with its own tooltip inside the hovered entry (the S on a chip) and lets it speak
//     alone. One more delegated selector; nothing moved or dropped.
//
//   qualityDimensions.js · aria 16 → 15  (DROP, justified)
//     Assurance Guide, Oct 2026: the member glyph (an orange ring segment) is gone from the
//     members group, the colour rule and the dial's members header — it read as a loading
//     spinner and wrapped onto a line of its own. It was decorative and `aria-hidden`, so a
//     screen reader never received it; the link it drew is carried by the words ("members
//     · N" on both the rule and the group) and the group's orange edge. No fact, no reach lost.
//
//   qualityDimensions.js · handler 19 → 20 · delegate 9 → 15  (GROWTH, no justification owed)
//     Assurance Guide, Oct 2026: S and M are one link. Hover lights the partner (or the header
//     of the fold that hides it); a click on the S or M letter opens that fold and lights it.
//     One delegated click handler and its selectors; nothing moved or dropped.
//
//   qualityDimensions.js · tip-trust 15 → 16  (GROWTH, no justification owed)
//     Assurance Guide, Oct 2026: a control names its properties and folds the modules it
//     consults during the turn; each fold row carries its module's mode (can act on the
//     answer · can only escalate to a judge) as a themed tooltip. The `reads` row it
//     replaced on a control moved, it did not drop: the modules are in the fold.
//
//   assuranceDial.js · tip-trust 4 → 5 · aria 13 → 15  (GROWTH, no justification owed)
//     Live Status dial, Oct 2026: the role mark on a module the gate consults in flight — a
//     themed tooltip carrying the words, an aria-label beside it, the glyph aria-hidden. A
//     mark, not a sentence (prod 510f7011). Nothing moved or dropped.
//
//   assuranceDial.js · state-key 10 → 11 · aria 12 → 13  (GROWTH, no justification owed)
//     Live Status dial, Oct 2026: the station NAME in each section head is a Guide route
//     (`data-dim="station_<key>"`, the state-key row) with an aria-label naming where it goes.
//     Nothing moved or dropped.
//
//   assuranceDial.js · aria 11 → 12  (GROWTH, no justification owed)
//     Live Status dial, Oct 2026: a station's members are shelved by class as the Guide shelves
//     them (upheld by · controls …), folded by state; the fold line's dot is decoration (the
//     words carry the state) and is aria-hidden — that is the extra aria row. Every member row
//     is still rendered inside its group; nothing moved off the surface.
//
//   qualityDimensions.js · tip-trust 11 → 13  (GROWTH, no justification owed)
//     Assurance Guide, Oct 2026: the kind tags beside each lead (controls · 6, measures · 2,
//     the title's CONTROL / GUARANTEE tag) carry their definition on hover — the word a
//     reader had to decode now explains itself. Nothing moved or dropped.
//
//   qualityDimensions.js · fact-html 11 → 8  (DROP — justified: a consolidation, not a loss)
//     Assurance Guide, Oct 2026: the four controls that each rebuilt the entry list on their
//     own (search · scope · facets · thresholds; `list.innerHTML = bodyHtml(…)` at four call
//     sites) now share ONE `repaint()` that renders the same list AND re-applies the chosen
//     entry's mark — the mark used to vanish on the first facet click. Four renders became
//     one render called four times; every fact the list carried is still rendered at every
//     one of those moments. (`test_assurance_dial` pins the shared repaint.)
//
//   qualityDimensions.js · fact-html 10 → 11  (GROWTH, no justification owed)
//     Assurance Guide terms, Oct 2026: the glossary gains "Terms used on the rows", the served
//     definitions of the technical words a row may still use (judge · chunk · grain · window ·
//     Assurance Inbox …) — one more rendered definition list. Nothing moved or dropped.
//
//   qualityDimensions.js · route 12 → 13 · tip-trust 10 → 11 · aria 14 → 15 · delegate 8 → 9
//   (GROWTH, no justification owed)
//     Assurance Guide text structure, Oct 2026: the wire key leaves the reading line for an
//     "id" mark (`data-qd-copy`, a route that copies the key; its tooltip is the tip-trust
//     row; the copy icon's aria-hidden is the aria row; the click handler is the delegate).
//     The key itself stays in the DOM. Every fact the row carried is still rendered — the
//     description, fires-when and settings segments are rows now instead of paragraphs.
//
//   qualityDimensions.js · handler 18 → 19 · route 9 → 12 · tip-trust 4 → 10 · aria 12 → 14
//   (GROWTH, no justification owed)
//     Assurance Guide anatomy, Oct 2026: every entry renders through ONE layout (title ·
//     "can" line · description · relations shelved in the turn / after the turn · meta);
//     every lead word carries its definition as a `u-tip` (the tip-trust rows) and the
//     glossary button + facet-group buttons add their routes, aria-pressed/aria-expanded
//     and the one glossary toggle handler. Every fact, link, badge and dot the row carried
//     before is still rendered; nothing moved off the surface.
//
//   trustCircle.js · aria 16 → 17  (GROWTH, no justification owed)
//     Trust Circle pane, Oct 2026 (8602bfe3): each reading group in the right pane leads with
//     a colour swatch (`.tc-group-sw`, the state's colour) beside the group's word; the swatch
//     is decoration — the word carries the meaning — so it is `aria-hidden="true"`. The extra
//     aria row is that swatch. No fact moved, no tooltip or handler changed.
//
//   actionDesigner.js · fact-text 5 → 7 · fact-html 6 → 10 · delegate 17 → 18  (GROWTH, no justification owed)
//     ACT, Oct 2026: "Access in the warehouse" — what an action needs of its write login and
//     role (and of the model login for its ledger), each requirement held or missing with why,
//     the exact GRANTs and "Copy the grants"; in the Define form and in each action's details.
//     Nothing moved or dropped.
//
//   actionDesigner.js · aria 22 → 23 · delegate 15 → 17  (GROWTH, no justification owed)
//     ACT, Oct 2026: an action's row offers "Edit contract" (its own aria-label) — the form
//     opens on the signed contract; the edit click and the focus-open are delegated. Nothing
//     moved or dropped.
//
//   actionDesigner.js · aria 20 → 22 · delegate 12 → 15 · fact-html 5 → 6  (GROWTH, no justification owed)
//     ACT, Oct 2026: Granted to takes the platform's capability-to-agents pattern (class
//     pills + a search + the agents grouped by class) — the search and the pills carry their
//     own aria; the pill clicks and the search are delegated on the dialog. The owed act sits
//     in a Next box (as in Govern); the row's meta names the target table as code (fact-html). Nothing moved or dropped: every grant toggle, check, and
//     try-it field is as it was, now also filtered in place.
//
//   actionDesigner.js · handler 4 → 5 · aria 7 → 20 · fact-text 3 → 5 · delegate 7 → 12 ·
//   tip-trust 0 → 3  (GROWTH, no justification owed)
//     ACT, Oct 2026: "Define an action" — the form that DEFINES a write contract in the
//     Designer (connection first, the concepts its warehouse holds, columns with their types,
//     role, posture). Its fields carry their own aria-labels; the key chip, the column type
//     and the locked "may write" checkbox explain themselves through the themed tooltip
//     (u-tip — the first cut used native title=, which this gate refused). Nothing moved or
//     dropped: the journey, the checklist, the grants and the try-it are as they were.
//
//   actionDesigner.js · aria 6 → 7  (GROWTH, no justification owed)
//     ACT B4c-s (Oct 2026): the try-it's connection picker (.ad-try-conn) carries its
//     own aria-label — the try-it names its connection instead of leaving it to the
//     server to guess among several. Nothing moved or dropped.
//
//   figureLineage.js · handler 0 → 5 · aria 0 → 2 · fact-html 0 → 4 · delegate 0 → 1
//   (GROWTH, a new surface — no justification owed)
//     S3 Compute with Lineage (Sep 2026): the figure-lineage dialog opened from a
//     receipted/quoted figure in the answer — the receipt (operation · rows used ·
//     statement · value · hash) and the rows it was computed over, or the quoted row.
//     ONE delegated click + one keydown; Escape/backdrop close; focus returns to the
//     figure. The kgTheatre.js finding beside this one predates S3 and is not this
//     increment's to classify.
//
//   attachmentSources.js · (a NEW surface — no justification owed)
//     S9 Grounded Multimodal (Sep 2026): the read badge (verified / unverified / unread,
//     a word on every chip), the image thumbnail with alt text from the extraction, the
//     capsule's Attachments row with a crop of the cited region, and the lineage dialog's
//     attachment statement (file · method · region · the row). Bytes come from the
//     session-scoped content endpoint.
//
//   figureLineage.js · fact-html 4 → 5  (GROWTH, no justification owed)
//     S9: a figure quoted from an attachment table opens on the file, the method and the
//     region (attachmentSources.statementHtml) before its rows. Nothing moved or dropped.
//
//   turnLineage.js · (a NEW surface — no justification owed)
//     S8 Regenerate as an Experiment (Sep 2026): the experiment card under an adopted
//     answer — one row per sibling answer to the same question, each carrying the
//     Scorecard's verdict strip (marks drawn by the Agents lens's own builder) and the
//     words that count what needs you, one Adopt per non-adopted row, a two-column
//     compare; the branch switch on an edited question; the Regenerate ▾ menu and the
//     inline editor. Every act re-reads the session (the one source of lineage).
//
//   answerCapsule.js · handler 3 → 5 · aria 1 → 2  (GROWTH, no justification owed)
//     S7 Outcome Loop (Sep 2026): the Drafts disclosure — a tab list (role=tablist /
//     tab / tabpanel, roving tabindex) over the first draft and each revision the
//     loop produced, with the graded spans marked in place. One delegated click +
//     one keydown (Arrow/Home/End) wire the tabs; the extra aria row is the tab
//     list's own naming. Nothing moved or dropped.
//
//   answerCapsule.js · tip-trust 6 → 7  (GROWTH, no justification owed)
//     OBDA-READ O2 (Aug 2026): the Data-products consumption-receipt chip. One tooltip
//     per signed data product the answer consumed, carrying the sealed facts —
//     request hash, compiled-SQL hash, trust state at consumption — and stating
//     that the receipt is sealed into the turn's Ed25519 chain (step type
//     obda.consumption) and offline-verifiable via the receipt export.
//
//   livingAnswer.js · tip-trust 2 → 0 · aria 7 → 6 · handler 4 → 5 · fact-text 11 → 14 ·
//   fact-html 5 → 7  (the Sep-2026 reshape: one line · one receipt · one verdict)
//     The two static trust tooltips were the six rail segments' and the seal's — seven
//     unlabelled marks each restating one gate's reading beside a status line and a summary
//     pill that restated it twice more ("clean" ×2). They are REPLACED, not lost: each gate's
//     reading is now a receipt ROW in words (fact-text +3, fact-html +2 — more reach, in
//     words instead of colour), and the ONE verdict chip's tooltip is built at runtime by
//     `verdictOf()` (so the extractor's static scan sees 0 literal trust tips) and names every
//     check the verdict rests on. The dropped aria attribute was `aria-hidden` on the seal
//     glyph (decoration, no fact); the chip gained `aria-label`. The extra handler is the
//     chip's click (opens the trust record). Reach: the same facts, more of them in words,
//     one honest verdict instead of four restatements.
//
//   livingAnswer.js · handler 5 → 6 · aria 6 → 7  (GROWTH, no justification owed)
//     The verdict chip became the ONE trust control (Sep 2026): the capsule's "Why trust
//     this answer?" toggle is folded behind it (kept for its listener + aria-expanded
//     state, clipped, out of the tab order). The extra handler mirrors the record's open
//     state onto the chip's chevron; the extra aria row is the chip's own aria-expanded.
//
//   figureLineage.js · delegate 1 → 4  (GROWTH, no justification owed)
//     The compute receipt card gained its own "View the rows it was computed over" action
//     (Sep 2026), dispatched through the SAME delegated click as the figure marks and
//     resolving the turn from the message the card sits in — three more `closest()` reads
//     on the one listener, the same dialog, the same receipt rows.
//
//   figureLineage.js · delegate 4 → 5  (GROWTH, no justification owed)
//     The compute receipt became an Answer-Surface evidence row (Sep 2026): the rows
//     action now resolves its row through `.ans-ev` (the row) as well as the legacy
//     `.compute-card`, one more `closest()` read on the same listener; the receipt hash
//     rides the button itself so the row's own frame need not carry it.
//
//   livingAnswer.js · handler 6 → 7  (GROWTH, no justification owed)
//     The follow lock (Sep 2026): one scroll listener on the chat scroller so the reader's
//     own upward scroll — never content growth — releases the view from following the
//     forming answer.
//
//   livingAnswer.js · route 0 → 1  (GROWTH, no justification owed)
//     A receipt row finds its Live Status card by IDENTITY (Sep 2026): the reducer writes
//     the row's key onto the event that produced it, the card wears it as `data-la`, and
//     the row opens `.status-step[data-la=key]` — never a regex over the card's body text,
//     which landed a query row on the engine's start card (it lists every available tool)
//     and the KG row on the context card (it names the Knowledge Graph module).
//
//   livingAnswer.js · fact-text 14 → 15  (GROWTH, no justification owed)
//     A REFUSED component call (Sep 2026) is no longer "done": its row reads
//     "refused — <the reason's first clause>" with the failed mark, and the full reason
//     rides the row's label + platform tooltip — the "3 tool calls, 2 rows" question
//     answered on the receipt itself.
//
//   agentPerformanceHandler.js · delegate 23 → 20
//     Three `.closest('div.flex.items-start')` calls removed. They WERE the defect:
//     the ack/quarantine handlers selected their row by its layout classes, the
//     redesign renamed the row, and every button silently stopped updating the view
//     while still POSTing. Replaced by one `refreshInbox()` that re-renders from the
//     source. Reach is not lost — it is restored.
//
//   agentPerformanceHandler.js · tip-trust 35 → 36  (GROWTH, no justification owed)
//     The `judge-cleared` mark on a run row: a coarse check flagged the turn and the
//     judge that refines it cleared it (Rule 3.0c), which is WHY the row is not counted
//     as a failure. A reader owed that reason — a correction hidden is a correction
//     nobody can check.
//
//   agentPerformanceHandler.js · state-key 8 → 11 · tip-trust 41 → 45 ·
//   fact-html 45 → 46  (GROWTH, no justification owed)
//     Judge Calibration made real (Aug 2026): the κ machinery existed with zero
//     callers, so "judges stay advisory until their calibration clears" was an
//     unmeasurable rule. State-keys = the frozen (dimension, ruling, turn) pair
//     each queue button carries — freezing at annotation time is what stops a
//     later re-score rewriting what the human agreed with. Tips = the κ-floor
//     explainer (κ ≥ 0.6 over ≥ 10 pairs, and that clearing means ELIGIBLE, not
//     promoted), the insufficient-pairs chip, and the agree/disagree buttons.
//     Fact = the per-dimension κ report table.
//
//   agentPerformanceHandler.js · tip-trust 36 → 38  (GROWTH, no justification owed)
//     The judge channel. A run row is ~24 identical squares, two of which are a MODEL'S
//     OPINION — nothing said which, nothing said a judge had run at all, and the ruling
//     was readable only after expanding. Two tooltips: the row's `judged · N of M
//     upheld` chip (naming each judge's ruling, the abstentions, and an in-path gate
//     escalation, which produces no mark at all) and the key explaining the new glyph.
//     A new mark with no key is a guess.
//
//   agentPerformanceHandler.js · route 37 → 38 · tip-trust 37 → 39  (GROWTH)
//     The intervention axis on the row: a mechanism ACTED. Route = the `act:` run
//     filter (which turns were altered is a different question from which scored
//     badly, and without it the answer is a scan of every row). Tips = the acted
//     chip (naming the act, and what is owed when it is parked) and the filter's own
//     explanation that it is independent of the verdict beside it.
//
//   assuranceBindings.js · NEW SURFACE (PROP-003, all GROWTH — no justification owed)
//     handler 10 · tip-trust 6 · aria 1 · fact-text 4 · fact-html 6. The binding studio
//     is a trust surface, not a config form: every cell states a reading (what assesses
//     this property in this lane · nothing bound, and never assessed-clean · absent by
//     design, in the property's own words · bound but no gate consults it · this module
//     has drifted and the act owed is re-sign-or-unbind), and the picker quotes
//     `conformance`'s refusals verbatim. Those are facts, so they are inventoried; if one
//     ever disappears, this gate says so.
//
//   assuranceBindings.js · tip-trust 6 → 7  (GROWTH, no justification owed)
//     The in-flight seam gave a cell a THIRD reading, and the two it already had could
//     not express it: a gate may RESOLVE a property itself, may consult it through the
//     seam (its own assessment plus a plugin's), or may not consult it at all. The new
//     tip names the gate that acts and states the union rule — "it can never make it pass
//     where it would have acted" — because a reader who thought a plugin REPLACED the
//     platform's own detection would draw exactly the wrong conclusion about what
//     installing one costs them.
//
//   qualityDimensions.js · tip-trust 5 → 4, fact-html 9 → 10  (a DROP, justified)
//     The M badge's own `u-tip` is gone and the module's tooltip moved into the shared
//     `[data-dim]` renderer (+1 fact-html). NOT a loss of reach: the explanation is now
//     richer and fires on the whole module button rather than on the 12px badge, and two
//     tooltip systems on one hover rendered two boxes. The move also FIXED the reading —
//     the shared renderer builds from `data-dim`, so hovering "Data scope adherence"
//     described "Answer scope", the property it routes to.
//
//   qualityDimensions.js · aria 14 → 12  (a DROP, justified)
//     The two arrow glyphs are gone, and with them their `aria-hidden="true"`. No fact
//     and no reach is lost: the attribute existed only to HIDE a decorative character
//     from a screen reader, and the character no longer renders. The route it decorated
//     survives as the module button itself; the arrows were removed because, once
//     modules and siblings moved into separate rows, a glyph between two chips read as
//     "points at the next one" — a chain that does not exist.
//
//   qualityDimensions.js · handler 14→18, state-key 4→5, tip-trust 4→5, aria 10→14,
//   delegate 7→8  (GROWTH, no justification owed)
//     Modules marked as modules, and named on BOTH sides of a shared assessment. They
//     were bare text — indistinguishable from an entry a reader could open — and on a
//     measurement or a control they were not rendered at all, so the S badge asserted a
//     shared assessment the entry never named. Adds the M badge (+tip, +aria), the
//     `reads` row routing each module to the property it assesses, and one delegated
//     hover/focus pair that lights both ends of the link (4 handlers, 1 delegation).
//
//   qualityDimensions.js · tip-trust 3 → 4, aria 8 → 10  (GROWTH, no justification owed)
//     The same-assessment badge. `graded by` says a measurement grades this property; it
//     does NOT say the two are the SAME computation, which is what the assurance
//     substrate buys — so the pair sharing one module is marked at BOTH ends. The letter
//     is the channel (never colour alone), the tooltip names the module, the aria label
//     names it for a screen reader, and the legend carries the key: one tip + two aria
//     (chip + legend sample).
//
//   agentPerformanceHandler.js · delegate 20 → 21  (GROWTH, no justification owed)
//     `data-ap-stripkey` — the band strip's legend. The strip carries three channels
//     now (fill = what it found · shape = who measured · size = did it decide), and a
//     mark with no key is a guess. Toggled in place rather than by re-rendering the
//     ledger, so explaining a legend cannot collapse the rows being explained.
//
//   assuranceBindings.js · handler 19 → 23 · aria 8 → 9  (GROWTH ONLY)
//     Staging. The card applied on confirm while it sat below the panel's Save row; once
//     it moved into the stack, two save models shared one column. Changes are now chosen
//     here and applied by the panel's Save. The four handlers are per-change undo (two
//     kinds), discard-all, and the dirty banner; the aria is the banner's `role="status"`,
//     so the count is announced rather than only drawn.
//   assuranceDial.js · aria 9 → 11  (GROWTH, no justification owed)
//     The station-control mark legend (da8cf969c, 3 Sep 2026): a key for the dots the
//     dial draws beside each control, drawn by the same mark builder so it cannot drift
//     from the marks it explains. The two aria attributes are the legend's key group
//     labelled for a screen reader ("What the marks beside each control mean") and its
//     sample mark hidden from one (decorative, the label carries the meaning). Recorded
//     here after the battery flagged it — the commit landed without re-baselining.
//
//   assuranceBindings.js · fact-html 7 → 5  (DROP — justified)
//     Two `.innerHTML =` writes are gone, and NO reading is lost. They were imperative
//     pokes into the open picker at the moment a write was refused. Confirm no longer
//     writes, so there is nothing to refuse there; a refusal now comes from apply and is
//     rendered DECLARATIVELY on the staged row inside the normal render pass
//     (`.ab-err--row`). That is strictly more reach: the old one required the picker to
//     still be open to be seen, the new one survives every re-render, sits beside the
//     change that caused it, and shows all of them rather than the one the Save status
//     line can carry. Fewer innerHTML writes, more of the fact reaching the operator.
//
//   assuranceBindings.js · aria 2 → 8  (GROWTH ONLY)
//     The guarantee ring. Eight segments, each a real button with its own accessible
//     name (the label alone is a 7.5px SVG text node), plus the two tablists and the
//     attention marks on the tabs. A ring drawn as decoration would have added none of
//     these and been unreachable by keyboard — which on a governance surface is the same
//     as not shipping it.
//
//   assuranceBindings.js · handler 10→14 · tip-trust 7→10 · aria 1→2 · fact-html 6→7
//     (GROWTH ONLY, no justification owed — recorded because the surface is new.)
//
//   agentPerformanceHandler.js · fact-html 46 → 42  (DROP — justified, Sep 2026)
//     Four `.innerHTML =` writes are gone, and NO reading is lost — every one of them was
//     a hand-maintained COPY of the inbox re-render (the bulk-decision tail, the two view
//     filters, the debounced trust-event listener), each writing the list and the badges
//     and NOTHING ELSE. That copy was the defect the operator reported: an acknowledgment
//     repainted the inbox and left the Trust Circle station of the associated agent on the
//     pre-ack state until a page refresh. All of them now route through the ONE seam
//     (`refreshInbox`), which writes the same list once and then repaints every reader of
//     the fact — the ring in place and every mini ring. Fewer innerHTML writes, more of
//     the fact reaching the operator, and the single review decision (which removed its
//     row by hand and repainted nothing else) joins the seam too.
//     PROP-003 · the studio's second half. It showed what a guarantee SEES and said
//     nothing about whether anything ACTS on it, so a dormant third-party control and a
//     working one were indistinguishable: both simply absent. The four handlers are the
//     posture select, restore, and the armed confirm's cancel/apply; the three tooltips
//     are the column heads (what an act is, why `fires_on` is the signing gate seen from
//     the control side, and that raising a control moves the attestation); the aria label
//     names which control a select belongs to, because "Posture" repeated down a column
//     is not a label. `data-ctl`/`data-ctl-go` are classified BENIGN above.
//
//   agentPerformanceHandler.js · tip-trust 45 → 48  (GROWTH ONLY, no justification owed)
//     The PREFERENCE axis. A user's upvote/downvote was stored and fed exactly one
//     store; it is now a platform-wide signal, and the roster had to say so WITHOUT
//     letting it be mistaken for a measurement (Rule 3bb — intervention, quality and
//     preference are three axes). Three tooltips, each carrying a fact the surface
//     cannot show: the run row's vote chip states that a rating never changes the
//     verdict beside it; the roster's user-feedback line states it is not folded into
//     the pass rate above it; and the disagreement count names why that line exists at
//     all — a turn the scorers passed that the user rejected is the one place a human
//     says the quality bands are wrong for this deployment, and it routes the reader to
//     where they are tuned. All three are word-bearing chips, never colour alone.
//
//   agentPerformanceHandler.js · route 38 → 37
//     `data-event-id` removed from the quarantine button. Its job passed to
//     `data-batch`, which names EVERY occurrence the row collapsed rather than one —
//     strictly more reach, counted under a different (benign) attribute.
//   evidenceView.js · handler 0 → 1 · tip-trust 0 → 1 · fact-text 0 → 1 · fact-html 0 → 2
//   evidenceView.js · handler 1 → 7 · fact-text 1 → 4 · fact-html 2 → 10 · route 0 → 1
//   · aria 0 → 5   (GROWTH) PROP-009 increment 4: the Evidence tab — every signature read
//     now, worst first, its filters, search and Verify all shown, each row opening the
//     answers that used it and each answer its own evidence tree.
//   evidenceView.js · handler 7 → 10 · aria 5 → 6 · fact-text 4 → 8 · fact-html 10 → 12
//     (GROWTH) PROP-009 increment 5: portable bundles. "Verify in this browser" on an
//     answer's evidence, and on the Evidence tab Verify all shown (now checked in the
//     browser against an exported bundle), Export shown and Export for audit — each verdict
//     a word-bearing chip that names what failed and how it was checked.
//   evidenceView.js · tip-trust 1 → 2   (GROWTH) PROP-009 increment 6: the evidence ledger's
//     reading on the Evidence tab, with its limit in the tooltip (the host holds the key and
//     could rebuild the chain; an exported checkpoint proves it was not).
//   trustBadge.js · fact-html 22 → 23   (GROWTH) the popover's usage line — which answers
//     used this artefact, and how many with the version it has now.
//     (GROWTH, a new surface) Oct 2026, PROP-009 increment 3: one answer's signed parts,
//     each read "when answered · now", the summary naming the parts that need a look, and
//     the record's own verification. Classified a SURFACE on arrival.
//   kgTheatre.js · handler 0 → 5 · tip-trust 0 → 8 · aria 0 → 3 · fact-text 0 → 5
//   · fact-html 0 → 10 · delegate 0 → 7   (GROWTH, a newly CLASSIFIED surface)
//     Sep 2026. The KG execution theatre had rendered trust readings since it shipped and
//     sat in neither list — the gate's own finding, and the reason the gate exists: an
//     extractor that shrugs launders a gap into a green tick. It is a SURFACE, not an
//     exemption: its consequence layer is read from the trace and never inferred, and it
//     speaks in verdicts — which facts from the model were APPLIED, which statement a
//     control STOPPED (carrying the refusal's own sentence), which seeds were WASTED, and
//     a receipt beat stating the turn's verdict. Losing one of those sentences silently
//     would let a turn a control acted on narrate as a quiet one.
//
//     Classifying it immediately found a real defect: 8 native `title=` attributes on a
//     trust surface — the unthemed grey box the platform standard forbids, which ignores
//     the theme toggle. Converted to the platform tooltip (`class="u-tip" data-tip`), so
//     tip-trust 0 → 8 is those same eight facts moving to a channel that renders in both
//     themes and can be clamped to the viewport. No fact and no reach was lost; the aria
//     labels beside them are untouched.
//
//   answerCapsule.js · tip-trust 9 → 10   (GROWTH, no justification owed)
//     Oct 2026: "Made from" is a fold (closed, like the sealed chain inside it) whose line
//     names what it holds, structured into Drew on · Did · To verify; each group label
//     carries what the group is in the platform tooltip (one template, +1). The sentence
//     "what this answer was made from — each one you can open or verify" leaves: the group
//     labels say it. Every row it held is still in the fold.
//
//   answerCapsule.js · tip-trust 8 → 9   (GROWTH, no justification owed)
//     Oct 2026: the capsule's Sources row is grouped by REPOSITORY (one signature pill) with
//     its documents listed once each; each document carries its title, file name and
//     freshness in the platform tooltip (+1). Where the dial renders, the Grounding,
//     Guardrail and Provenance READINGS leave the capsule: the dial's station members state
//     the same facts in the same words (verified on a real turn — "confirmed drift — answer
//     withheld", "screened — clean", "turn sealed … tip …"), so no fact is lost; the rest
//     sits under "Made from". Without the dial (the legacy strip) the readings stay.
//
//   qualityDimensions.js · tip-trust 14 → 15 · assuranceDial.js · tip-trust 3 → 4
//     (GROWTH, no justification owed) Oct 2026: JUDGED gets the same members group — its
//     members are the measures it counts, shelved by moment — on the Guide entry and on the
//     dial's JUDGED panel, each header carrying the member definition in the platform
//     tooltip. The adopted scorecard's own "· N measures" is hidden inside the dial only
//     (`.qc-total`), because the header states the same count; on the Quality card it stays.
//
//   qualityDimensions.js · tip-trust 13 → 14 · aria 15 → 16   (GROWTH, no justification owed)
//     Oct 2026: a guarantee's entry is grouped by MEMBERSHIP. The members group's header
//     carries the definition of a member in the platform tooltip (+1 tip-trust), and the
//     member mark beside it and beside the colour rule is `aria-hidden` (+1 aria) — the
//     words "members · N — the worst of these colours X" carry the meaning in text. Every
//     row the entry showed is still shown; the measures graded about it moved into their
//     own folded "not members" group, which names JUDGED as where they colour.
//
//   answerCapsule.js · tip-trust 7 → 8 · agentPerformanceHandler.js · route 43 → 47 ·
//   tip-trust 48 → 50   (GROWTH, no justification owed)
//     ACT surfaces (PROP-008 §5.6): the capsule's Actions row (one tooltip per act, naming
//     how it ended and where its receipt is), the Inbox's parked-write row (Approve — perform
//     it · Reject, each with the tooltip that says what the click does), and BOUNDED's act
//     line on the Trust Circle. More facts reach the reader; nothing moved or dropped.
//
//   turnLineage.js · tip-trust 3 → 4   (GROWTH, no justification owed)
//     The Experiment card's verdict strip became two lines so it could not run over its
//     neighbours (59bfadeb). The meta line is now the row's only shrinking member and is
//     ellipsized, so its full text moved into the platform tooltip beside it — a fact that
//     was being clipped is now readable, which is strictly more reach.
//
//   agentPerformanceHandler.js · state-key 7 → 8 (RESTORED) · tip-trust 40 → 41
//     SUPERSEDED, same session. The route is back, and pointing somewhere better: MOMENTS
//     are now first-class Guide entries (`stage_<key>`), so the header opens the entry
//     that actually describes how these checks are grouped. The tooltip stays as the
//     hover. Strictly more reach than before the rework — the entry below records why the
//     route was briefly absent, because the reason is the finding: the Guide was missing
//     an entry class, and a tooltip was a patch on a missing concept rather than a design.
//
//   [historical, superseded above] the header stopped being a Guide route and became a tooltip.
//     It used to be `data-dim="station_<key>"` — the header WAS the link from a chip to
//     the ring segment that counted it. Measurements no longer group by station (they
//     group by the STAGE of the turn they measure, eval/stages.py), and a stage has no
//     Guide entry to route to: it groups measurements, it is not a guarantee.
//
//     JUSTIFIED, not silent. The route delivered an EXPLANATION, and the explanation is
//     delivered in place instead — the header now carries the stage's own one-line
//     description as a tooltip (hence tip-trust +1, the same fact moving channels). The
//     per-chip `data-dim` routes are untouched, so every individual check is still one
//     click from its definition. Net reach: a reader loses one gesture to a page that
//     no longer describes how these checks are grouped, and gains the description
//     without leaving the row.
//   assuranceDial.js · fact-html 2 → 3 · fact-attr 6 → 7   (GROWTH, no justification owed)
//     Nov 2026 (9db532794): JUDGED's members always show, under their moments — when the stage
//     vocabulary is not loaded yet, the measures list is drawn with what is known and redrawn
//     (`innerHTML`) once the stage names arrive, its verdicts held meanwhile in `data-verdicts`.
//     The same measures reach the reader, now also before the vocabulary has loaded.
//
//   agentPerformanceHandler.js · route 47 → 51 · tip-trust 50 → 52   (GROWTH, no justification owed)
//     Nov 2026, TDA_Platform phase 3: the Inbox's platform-request row — "An agent asks to: …",
//     the operation and every argument, Approve — run it · Reject, each with the tooltip that
//     says what the click does (the same decision route as the parked-write row; its
//     `data-review-source="platform"` is the benign selector already listed). A decided request
//     reads "a platform change — …, approved — performed …" in the settled list. More facts
//     reach the reader; nothing moved or dropped.
//
const shape = JSON.stringify(
    inventory.map((r) => ({ file: r.file, counts: r.counts, detail: r.detail })), null, 2);

if (args.has('--write')) {
    fs.writeFileSync(BASELINE, shape + '\n');
    console.log(`\nBaseline written: ${path.relative(ROOT, BASELINE)}`);
} else if (fs.existsSync(BASELINE)) {
    const prev = fs.readFileSync(BASELINE, 'utf8').trim();
    if (prev !== shape.trim()) {
        const a = JSON.parse(prev), b = JSON.parse(shape);
        const byFile = (arr) => Object.fromEntries(arr.map((r) => [r.file, r]));
        const A = byFile(a), B = byFile(b);
        const lines = [];
        for (const f of new Set([...Object.keys(A), ...Object.keys(B)])) {
            const ca = (A[f] || {}).counts || {}, cb = (B[f] || {}).counts || {};
            for (const k of new Set([...Object.keys(ca), ...Object.keys(cb)])) {
                if ((ca[k] || 0) !== (cb[k] || 0)) {
                    lines.push(`      ${f.replace('static/js/handlers/', '')} · ${k}: `
                             + `${ca[k] || 0} → ${cb[k] || 0}`);
                }
            }
        }
        fail('the trust surface changed since the baseline:\n' + lines.join('\n')
           + '\n      → if the change is intended, classify it in the migration ledger '
           + 'and re-run with --write. A count that DROPS is potential fact or reach '
           + 'loss and must be justified in the ledger, never re-baselined silently.');
    }
} else {
    console.log('\n(no baseline yet — run with --write to create one)');
}

console.log();
if (failures.length) {
    console.log(`FAILED — ${failures.length} finding(s):\n`);
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}\n`));
    process.exit(1);
}
console.log(`OK — ${inventory.length} surfaces, `
    + `${kinds.reduce((a, k) => a + tot(k), 0)} classified constructs, 0 unclassified.`);
