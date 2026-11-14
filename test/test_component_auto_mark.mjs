// The component vocabulary, made consistent (Nov 2026): available · enabled · active · Auto —
// ONE rule for components as for companion skills.
//   available — the admin's (Administration → Components) + the manifest's class list
//   enabled   — the agent's (Capabilities); an absent entry follows the manifest default
//   active    — in effect every turn without being called: the manifest's `acts_every_turn`,
//               a declaration, never a switch; always a subset of enabled
//   Auto      — the PLATFORM holds the switch (no stored entry); a hand on the switch owns it;
//               Reset hands it back. A mark, never a state.
// Run: node test/test_component_auto_mark.mjs
import fs from 'node:fs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

// ── the declaration ────────────────────────────────────────────────────────────────
const SCHEMA = JSON.parse(read('components/schemas/component_manifest_v1.schema.json'));
check('the manifest schema declares acts_every_turn as an optional boolean', SCHEMA.properties.acts_every_turn?.type === 'boolean' && !SCHEMA.required.includes('acts_every_turn'));
const manifests = fs.readdirSync(new URL('../components/builtin/', import.meta.url))
  .map((d) => { try { return JSON.parse(read(`components/builtin/${d}/manifest.json`)); } catch { return null; } }).filter(Boolean);
const everyTurn = manifests.filter((m) => m.acts_every_turn === true).map((m) => m.component_id).sort();
check('exactly the three per-turn components declare it: context_window · memory · needs_input', JSON.stringify(everyTurn) === JSON.stringify(['context_window', 'memory', 'needs_input']), everyTurn.join(','));
check('no component declares it as anything but a boolean', manifests.every((m) => !('acts_every_turn' in m) || typeof m.acts_every_turn === 'boolean'));
// the platform's set for a NEW agent (user decision 2026-11-02): Context Window Manager, Compute,
// Data Visualization, Needs Input — plus Files, Tool Finder and Skill Library, which cost nothing
// (the agent tool is a separate opt-in; the two are relevance-gated). Everything else declares the
// creation exception and starts off; a user's own "On for my new agents" beats it.
const NEW_AGENT_OFF = ['canvas', 'dashboard', 'database_navigator', 'explain_plan', 'flow', 'knowledge_graph', 'memory', 'scheduler'];
check('exactly the components new agents start without declare on_for_new_agents: false',
  JSON.stringify(manifests.filter((m) => (m.profile_defaults || {}).on_for_new_agents === false).map((m) => m.component_id).sort()) === JSON.stringify(NEW_AGENT_OFF));
check('no component declares on_for_new_agents: true (absent = the class default)', manifests.every((m) => (m.profile_defaults || {}).on_for_new_agents !== true));
const ON_FOR_NEW = manifests.filter((m) => (m.profile_defaults || {}).enabled_for?.length && (m.profile_defaults || {}).on_for_new_agents !== false).map((m) => m.component_id).sort();
check('…so a new agent of any class starts with: compute · chart · context_window · files · find_tools · needs_input · skill_library',
  JSON.stringify(ON_FOR_NEW) === JSON.stringify(['chart', 'compute', 'context_window', 'files', 'find_tools', 'needs_input', 'skill_library']), ON_FOR_NEW.join(','));
const RR2 = read('src/trusted_data_agent/api/rest_routes.py');
check('the creation default per class: the user\'s choice, else on_for_new_agents, else the class default; unavailable never on',
  /elif comp\.component_id in user_comp_defaults:\s*d\["profile_enabled_default"\] = bool\(user_comp_defaults\[comp\.component_id\]\)/.test(RR2)
  && /elif "on_for_new_agents" in \(comp\.profile_defaults or \{\}\):/.test(RR2) && /if d\["globally_disabled"\]:\s*d\["profile_enabled_default"\] = False/.test(RR2));

// ── the backend serves the marks from ONE rule ─────────────────────────────────────
const EN = read('src/trusted_data_agent/components/enablement.py');
check('enablement.is_auto: no stored switch = the platform holds it', /def is_auto\(comp: Any, profile_config[\s\S]*?return not \(isinstance\(prof_cfg, dict\) and "enabled" in prof_cfg\)/.test(EN));
check('enablement.acts_every_turn reads the manifest declaration', /def acts_every_turn\(comp: Any\) -> bool:[\s\S]*?manifest\.get\("acts_every_turn", False\)/.test(EN));
const RR = read('src/trusted_data_agent/api/rest_routes.py');
check('the per-profile components view serves auto · acts_every_turn · active (= enabled and every turn)',
  /d\["auto"\] = enablement\.is_auto\(comp, profile\)/.test(RR) && /d\["acts_every_turn"\] = enablement\.acts_every_turn\(comp\)/.test(RR)
  && /d\["active"\] = bool\(profile_enabled and d\["acts_every_turn"\]\)/.test(RR));
check('the components list, scoped to a class, serves the pure manifest default an Auto entry follows', /d\["manifest_default"\] = enablement\.manifest_default\(comp, req_profile_type\)/.test(RR));
const MD = read('src/trusted_data_agent/components/models.py');
check('the API dict carries the declaration', /"acts_every_turn": bool\(self\.manifest\.get\("acts_every_turn", False\)\)/.test(MD));

// ── the editor: Auto, ownership, Reset, the save rule ─────────────────────────────
const CH = read('static/js/handlers/configurationHandler.js');
check('the editor fetches the components scoped to the agent\'s class', /fetch\(`\/api\/v1\/components\?profile_type=\$\{encodeURIComponent\(ccType\)\}`/.test(CH));
check('a row is owned only with a stored switch (or a creation default that differs from the class default)',
  /const owned = compSettings\.enabled !== undefined \|\| \(!profile && creationDefault !== manifestDefault\)/.test(CH));
check('an existing agent\'s Auto row shows the pure manifest default; a new agent\'s the creation default', /const followsDefault = profile \? manifestDefault : creationDefault/.test(CH));
check('the row carries ownership and the default it follows, the Auto badge and the Reset control',
  /data-comp-row="\$\{comp\.component_id\}" data-owned="\$\{owned \? '1' : '0'\}" data-manifest-default=/.test(CH)
  && /data-comp-auto-badge class="[^"]*\$\{owned \? 'hidden' : ''\}/.test(CH) && /data-comp-reset class="[^"]*\$\{owned \? '' : 'hidden'\}/.test(CH));
check('a hand on the switch (a trusted change) takes ownership; a Reset returns to the class default without it',
  /if \(e\.isTrusted\) _setOwned\(e\.target\.closest\('\[data-comp-row\]'\), true\)/.test(CH)
  && /toggle\.checked = row\.dataset\.manifestDefault === '1';\s*toggle\.dispatchEvent\(new Event\('change', \{ bubbles: true \}\)\);[^\n]*\n\s*_setOwned\(row, false\)/.test(CH));
check('the save writes the switch only where the owner holds it, and strips nothing a stored entry keeps',
  /const owned = compRow \? compRow\.dataset\.owned === '1' : true;/.test(CH)
  && /if \(owned\) \{\s*componentConfig\[compId\]\.enabled = toggle\.checked;/.test(CH)
  && /\} else \{\s*delete componentConfig\[compId\]\.enabled;\s*delete componentConfig\[compId\]\.intensity;\s*\}/.test(CH)
  && /if \(!Object\.keys\(componentConfig\[compId\]\)\.length\) delete componentConfig\[compId\];/.test(CH));
check('every-turn rows say so in the editor', /every turn<\/span>` : ''\}/.test(CH));

// ── companion skills carry the SAME control: Auto badge + Reset to default ─────────
// (Nov 2026) a hand on a companion skill's toggle owns it (auto:false on save); Reset hands it
// back — the row follows its component again and the save writes auto:true. One rule, both lanes.
check('a companion skill row renders the Auto badge and the Reset control (hidden by ownership, never removed)',
  /data-auto-badge class="[^"]*\$\{autoManaged \? '' : 'hidden'\}/.test(CH) && /data-skill-reset class="[^"]*\$\{autoManaged \? 'hidden' : ''\}/.test(CH)
  && !/\[data-auto-badge\]'\)\?\.remove\(\)/.test(CH));
check('ownership on a skill row flips badge and Reset together',
  /const _setSkillOwned = \(owned\) => \{[\s\S]*?row\.dataset\.autoManaged = owned \? '0' : '1';[\s\S]*?\[data-auto-badge\]'\)\?\.classList\.toggle\('hidden', owned\);[\s\S]*?\[data-skill-reset\]'\)\?\.classList\.toggle\('hidden', !owned\);/.test(CH));
check('a hand on either toggle takes ownership', /const _markUserOwned = \(\) => \{ if \(row\.dataset\.autoManaged === '1'\) _setSkillOwned\(true\); \};/.test(CH));
check('Reset follows the component again: active = component enabled, then Auto',
  /\[data-skill-reset\]'\)\?\.addEventListener\('click', \(\) => \{\s*const follows = _isComponentEnabledInModal\(modal, compId\);[\s\S]*?activeCb\.checked = follows;\s*if \(follows\) enabledCb\.checked = true;\s*_setSkillOwned\(false\);/.test(CH));
check('…and the save derives auto from exactly that: active equals the component\'s enablement', /const auto = \(isActive === compEnabled\);/.test(CH));

// ── the resource panel and the card speak the same words ──────────────────────────
const CP = read('static/js/handlers/componentsPanelHandler.js');
check('the Components tab reads active/enabled/available through the one rule', /tabLabel\('Components', components,\s*c => c\.profile_enabled && !c\.globally_disabled,\s*c => c\.active && !c\.globally_disabled\)/.test(CP));
check('a panel card marks "every turn" and "Auto"', /comp-every-turn/.test(CP) && /comp\.auto && !comp\.globally_disabled/.test(CP));
const CC = read('static/js/handlers/componentHandler.js');
check('the component card wears the every-turn pill and names the creation exception', /function _everyTurnPill\(comp\)/.test(CC) && /addRow\('New agents', 'start without it/.test(CC) && /addRow\('Every turn'/.test(CC));
check('no component is called "always enabled" any more (a system component can be switched off per agent)', !/Always enabled/.test(CC) && !/always auto-enabled/.test(CC));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
