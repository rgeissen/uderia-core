// Helper for test_pack_subscription_sync.py (section C): execute createKnowledgeRepositoryCard + createCollectionCard from ui.js under a DOM stub;
// print, per function × case, the action labels rendered in the destructive slot.
import fs from 'node:fs';
const src = fs.readFileSync(process.argv[2], 'utf8');
function fnSrc(name){ const i = src.indexOf(`\nfunction ${name}(`); if (i<0) throw new Error('no '+name);
  const rest = src.slice(i+1); const j = rest.search(/\nfunction |\nexport /); return j<0?rest:rest.slice(0,j); }
const el = (tag) => ({ tagName: tag, className:'', textContent:'', innerHTML:'', title:'', disabled:false, type:'', href:'',
  dataset:{}, style:{cssText:''}, children:[], appendChild(c){ this.children.push(c); return c; }, append(...c){ this.children.push(...c); },
  insertBefore(c){ this.children.push(c); return c; }, prepend(c){ this.children.unshift(c); }, addEventListener(){}, setAttribute(k,v){ this[k]=v; },
  getAttribute(){ return null; }, removeAttribute(){}, querySelector(){ return null; }, querySelectorAll(){ return []; }, remove(){},
  classList:{ add(){}, remove(){}, toggle(){}, contains(){ return false; } }, closest(){ return null; } });
const document = { createElement: el, createTextNode: t => ({ textContent: t, children: [] }), createDocumentFragment: () => el('frag'), querySelector(){return null;}, getElementById(){return null;}, body: el('body') };
const window = { showAppBanner(){}, showConfirmBanner(){}, marketplaceHandler:{}, ragCollectionManagement:{}, TrustBadge:null, uTip:null, state:{} };
const escapeHtml = s => String(s ?? '');
const state = { authToken:'', currentProfile:{}, profiles:[] };
const stubs = 'const formatTimeAgo=()=>"";const formatDate=()=>"";const DOM={};';
const walk = (n, out=[]) => { for (const c of (n.children||[])) { if (c.textContent) out.push(String(c.textContent).trim()); walk(c,out); } return out; };
const cases = { packRef: {id:8,name:'P',is_subscribed:true,is_owned:false,agent_packs:[{id:92,name:'Policing Data Products'}],repository_type:'planner',enabled:true,document_count:0,chunk_count:14,embedding_model:'m',subscription_id:'s1'},
                plainSub: {id:9,name:'Q',is_subscribed:true,is_owned:false,agent_packs:[],repository_type:'planner',enabled:true,document_count:0,chunk_count:1,embedding_model:'m',subscription_id:'s2'} };
const out = {};
for (const name of ['createKnowledgeRepositoryCard','createCollectionCard']) {
  out[name] = {};
  for (const [k, col] of Object.entries(cases)) {
    col.repository_type = name === 'createKnowledgeRepositoryCard' ? 'knowledge' : 'planner';
    // module-level helpers the card calls (badge builders, formatters) are stubbed as
    // element factories, discovered from the ReferenceError until the function runs
    let extra = ''; let res = null;
    for (let round = 0; round < 40; round++) {
      try {
        const f = new Function('document','window','escapeHtml','state','el', stubs + extra + fnSrc(name).replace(/^export /,'') + `\nreturn ${name};`)(document, window, escapeHtml, state, el);
        const card = f(col); const labels = walk(card);
        res = { managed: labels.includes('Removed with the pack'), unsubscribe: labels.includes('Unsubscribe') }; break;
      } catch (e) {
        const m = /^(\w+) is not defined/.exec(e.message);
        if (!m) { res = { error: String(e.message).slice(0,160) }; break; }
        extra += `const ${m[1]} = (...a) => el('span');`;
      }
    }
    out[name][k] = res || { error: 'no result' };
  }
}
console.log(JSON.stringify(out));
