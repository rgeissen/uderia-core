# Files and file stores — architecture

The live contract for the `files` component and the file store (PROP-010). The proposal
(PROP-010 (`docs/proposals/PROP-010-files-and-file-stores.md`)) records why; this document records
what runs.

## 1. What it is

Everything a session produces that is a **file** — a kept table, chart, answer or component
export, a dashboard snapshot, a flow's output, a file the agent saved itself, and **every chat
attachment** — is stored once, encrypted, in an admin-configured **file store**, and listed in
**Files**. Three bindings decide everything:

| Question | Bound to |
|---|---|
| who owns it (sees, downloads, deletes) | the **user** |
| where its bytes live | the **agent of the turn** (`fileStoreConfigId`), else the admin default, else platform local |
| where it came from | the **session and turn** — the turn's bill of materials is copied onto the file at save time — or the **flow run**, the **component and its instance**, the **scheduled run** |

Download is the only way out of the platform, through one governed route.

A file names its agent by **id**: `service.agent_label` reads the agent as it is now (a renamed tag reads renamed) and falls back to the tag and class stored with the file only when the agent is gone; `put_file` stores them whenever a writer passes the id alone. Attaching a kept file in another agent's conversation records a *use* (provenance), not a new agent.

## 2. Modules

```
src/trusted_data_agent/filestore/
  base.py            FileStoreBackend ABC, capabilities, opaque object keys (<owner>/<id>)
  catalog.py         THE backend declaration: class, SDK, required/secret fields, the admin form (§5a)
  local_backend.py   platform local (tda_files/, TDA_FILES_DIR) and any mounted path; atomic writes
  s3_backend.py      S3-compatible (boto3 optional; test_connection fails softly without it)
  azure_backend.py   Azure Blob Storage (azure-storage-blob; azure-identity for managed identity)
  gcs_backend.py     Google Cloud Storage (google-cloud-storage; service-account key or ADC)
  factory.py         backend cache + the resolution chain (explicit → agent → default → local)
  crypto.py          envelope encryption, keyed fingerprints, sealed records
  kms.py             external key managers for the data keys: AWS KMS · Azure Key Vault ·
                     Google Cloud KMS · HashiCorp Vault Transit (§5b)
  settings.py        governance (file_store_settings): tiers, downloads, quotas, capacity, encryption
  db.py              persistence (schema/45_file_stores.sql): store configs, files, file_uses
  service.py         THE write path (put_file), read path (read_bytes) and exit (download)
  session_files.py   THE accessor for chat attachments (manifest, bytes, purge, migration)
  formats.py         CSV (Excel-safe) · XLSX · Markdown → DOCX/PDF; tables/charts from a persisted answer
src/trusted_data_agent/api/files_routes.py     the REST face (/v1/files…, /v1/file-stores…)
components/builtin/files/                       manifest + renderer (the Files split panel)
static/js/handlers/filesKeep.js                 Keep on answers and attachments, the From Files picker
static/js/handlers/fileStoreAdmin.js            Administration → Components → Files: Stores · Policy · Capacity
components/builtin/files/handler.py             TDA_Files — the agent's own list · read · save (opt-in per agent)
skills/builtin/files-expert/                    its companion skill (follows the TOOL, not the component)
```

## 3. Invariants

1. **One write path.** Every file enters a store through `service.put_file`: size, quota, store
   capacity, tier gate, resolution, encryption, fingerprint, row, live announcement. Nothing else
   writes to a store.
2. **One read path.** `service.read_bytes` is the only reader of a store; attachment readers use
   `session_files.read_bytes` / `block_bytes`, which call it. **No reader receives a plaintext
   path** — extractors read uploads from memory (`DocumentUploadHandler.extract_text_from_bytes`,
   `image_sources.extract(..., data=)`); multimodal blocks carry `data`.
3. **One exit.** Every byte that leaves the platform passes `service.download`: ownership, the
   tier's download policy (`allowed` · `allowed_except_data_derived` · `disabled`), conversion,
   an audit row (`file_download`). The route answers `Content-Disposition: attachment`,
   `X-Content-Type-Options: nosniff` and a sandboxing CSP; HTML and SVG never go out with their
   own type. Previews are bounded representations from `/preview`, never the raw file inline.
4. **A store never sees a filename or plaintext.** Object keys are opaque; names and provenance
   live in the database. With encryption (on by default; locked on in hardened mode) each file has
   its own AES-256-GCM data key, chunked with an authenticated last-chunk flag, wrapped by a
   key-encryption key derived from the master key with HKDF (`uderia-filestore:v1:kek`) — or,
   when the admin chose one, by a key held in an external key manager (§5b). The content
   fingerprint is an HMAC under its own derivation.
5. **A file is read from the store it was written to.** Rebinding an agent changes only new files;
   moving files is the admin's audited migration (read back before the original is removed).
6. **Attachments are files.** An upload is a `lifetime: conversation` file in the agent's store;
   its session's manifest is sealed (`manifest.enc`, bound to user and session) and references
   the file. Deleting (archiving) a conversation purges its unkept attachments **at once** —
   object and wrapped key — exactly when the uploads folder always went. **Keep** flips the
   lifetime; nothing is copied. Attaching a kept file adds a reference to the manifest.
   **A deleted attachment:** its turns are untouched — question, answer, trace and evidence stay,
   and the answer stays in the history later turns read. A turn never reads it again: every turn
   reader takes `session_files.readable_manifest` (the manifest without entries whose file is
   deleted or gone), so naming the id in a new request — a REST client can — gets nothing. While
   it sits in Recently deleted, the manifests keep the text read at upload so a restore is
   complete; the purge (expiry, retention 0, a deleted conversation, a removed user) runs
   `session_files.forget_files` once per user, removing its entry — and that text — from EVERY
   conversation's manifest that references it (its own and any it was attached to from Files).
   The chat-log chip says what the attachment is now (`GET /v1/files/attachments?session_id=` →
   `live` · `deleted` (+`purge_after`) · `gone`, read from the conversation's own manifest):
   *Deleted* with *Restore* while it can be restored, *Deleted* alone once purged; an unreadable
   manifest says nothing rather than a false "Deleted".
   **An attachment is evidence.** The gates count the attached text as support (`attached_material`
   → `run_grounding(attached_texts=…)`, the knowledge-sufficiency honesty check). For the
   after-the-turn scores a turn records WHICH attachments it read — `attachment_evidence.files`
   (ids + names, never their text; `session_files.begin_turn`/`stamp_for_turn`, armed in the
   execution service) — and the scoring sites (online scoring, *Score this turn*) fill in the text
   through the guarded manifest (`session_files.with_attachment_evidence`) before they build the
   `RunRecord`. So a purge removes the evidence with the text: an attachment deleted since is
   named as **lost**, and every support reader (`numeric_fidelity`, `grounded_overlap`, the
   groundedness judge, every assessor that requires `chunks` — enforced once in
   `Assessor.run`) abstains in one wording rather than convict a figure whose source was
   deleted. One support definition for both lanes: `RunRecord.support_chunk_texts` (retrieved
   chunks + S9 readings, else the attachment text). The scorers read the full extracted text — a
   superset of the excerpt the model was shown.
7. **Storage applies whatever the component's enablement.** The `files` component governs what is
   shown (Workspace section, panel, Keep); an agent with Files off still stores attachments
   encrypted and deletes them with the conversation. Its paperclip behaves exactly as before.
8. **Kept parts come from the persisted answer.** A kept table's CSV is parsed from the turn's
   `final_summary_html`; a chart's CSV from its persisted `data-spec`; the whole answer from
   `final_summary_text`. Only a chart's PNG is drawn by the browser. Keeping identical content
   again answers `409 duplicate` with the existing file (fingerprint match).
9. **Live everywhere.** `put_file`, keep, rename, delete, restore, purge, migration and every
   recorded use announce the one component notice `component_changed` (`component_id: files`)
   on the per-user channel (`core/ui_notify.announce_files_change`, the Files spelling of
   `announce_component_change` — see [Component Signals](COMPONENT_ARCHITECTURE.md#component-signals--how-components-talk-to-each-other));
   the page turns it into `uderia:component-changed`, which the panel, the Components → Files
   tab, the Keep acts and the Workspace follow through `UderiaBus.onComponent('files')`. There is
   no Files-private change event. The routes run
   most of these writes in a worker thread (`asyncio.to_thread`); `trust_events.broadcast_notice`
   hands such a notice to the serving loop (`APP_STATE["serving_loop"]`, `call_soon_threadsafe`)
   — before that, a rename, delete, restore or keep made through the REST API never reached an
   open page.

10. **One exit for every export.** A browser-built export (a receipt, an evidence bundle, a
    canvas file, a graph picture …) leaves a page only through `FilesKeep.deliver(blob, name,
    meta)`: it downloads exactly as before, is recorded (`file_download`, browser-built — its
    data is already in the browser, so it can be named, not stopped), and offers *Save to Files*.
    A server-built export (knowledge graph, metric, component instance, a conversation's
    provenance) answers through `files_routes.export_response`: the tier's download policy
    decides and the export is audited, or `?to=files` keeps the SERVER's bytes in Files.
    `service.save_export` is the one way an export enters a store.

## 4. Lifecycle

| Event | Effect |
|---|---|
| upload | stored as `conversation`, listed under Uploads |
| Keep (an attachment) | `kept` — survives its conversation |
| conversation archived/deleted | unkept attachments purged at once; kept files stay, provenance reads "Conversation deleted" from the stored bill of materials |
| delete | Recently deleted; purged (object and key) after `retention_days` (0 = at once); restorable until then |
| store deleted | refused while it holds files |
| user deactivated | files kept with the account (`service.purge_user` exists for a hard delete) |
| master-key rotation | `maintenance/rotate_encryption_key.py` rewraps platform-wrapped data keys, leaves keys held by an external key manager unchanged (they do not depend on the master key), reseals store settings and the key-manager settings, re-seals manifests (staged as `manifest.enc.rotating`, swapped after the commit; a half-rotated manifest heals on read) |
| key manager changed | new files are wrapped by the new key; existing files keep opening with the key they name until *Re-wrap* moves them (§5b) |

Startup (`filestore.startup`): tables, the one-time idempotent migration of plaintext uploads,
then a purge of what retention no longer keeps; `maintenance_loop` purges every 6 h.

## 5. Governance — Administration → Components → Files

The component's admin page carries the usual **Overview** (global switch) and **Users**
(per-user override) tabs plus three of its own, registered through the generic
`window.ComponentAdminTabs` hook:

- **Stores** — add (backend → connection → encryption and capacity → test → save; save only after
  a passing test), edit, test (an edited store's masked secrets are taken from the saved store,
  never sent as the mask), make default, delete (refused while non-empty), move files with a
  dry-run count.
- **Keys** — which key wraps new files' data keys (§5b): the platform key or an external key
  manager; test → *Use for new files* (enabled only after a passing test); the keys in use with
  their file counts; *Re-wrap* existing files under the active key, with a dry-run count.
- **Policy** — per tier: keep files, stores its agents may use, downloads, space per user;
  globally: require encryption (locked in hardened mode), retention, largest file, warning
  threshold, platform-local capacity.
- **Capacity** — provisioned vs held vs physically free per store, what quotas promise against
  what is provisioned (overcommit said in words), per-user overrides, largest consumers. A write
  over a store's provisioned capacity is refused (`507 store_full`); a kept file over the user's
  quota is refused (`507 quota_exceeded`); attachments are never refused for quota.

The agent editor binds an agent to a store (`fileStoreConfigId`, not portable, part of the
deployment signature's `file_store` field — absent when unbound, so earlier attestations hash as
before).

### 5a. Backends — declared once (`filestore/catalog.py`)

| Backend | Location | Credentials (first match) | SDK |
|---|---|---|---|
| `local` | a folder on the host, incl. a mounted share | — | — |
| `s3` | bucket (+ prefix) on AWS or any S3-compatible endpoint | access key + secret, else the host's role | `boto3` |
| `azure_blob` | container (+ prefix) | connection string · account URL + account key · account URL + SAS token · account URL alone = the host's managed identity | `azure-storage-blob` (+ `azure-identity`) |
| `gcs` | bucket (+ prefix) | service-account key JSON (stored sealed), else the host's application default credentials | `google-cloud-storage` |

The catalog row is the single source for the factory (module + class), the validator
(`required` fields, `one_of` alternatives — Azure needs a connection string or an account URL),
the masked secrets (`SECRET_KEYS`), `GET /v1/file-stores/backends` (with `available` and the
missing package in words) and the admin's Add-store form, which draws the row's fields. Adding a
backend is one row and one class — no form, validator or mask to edit. No backend offers
pre-signed or SAS links: a link would be an exit that bypasses §3.3, and the bytes behind it are
ciphertext anyway. Every SDK is optional; a missing one makes the backend read *not installed*
in the admin and `test_connection` say so, never an import error at startup.

### 5b. External key managers (`filestore/kms.py`)

By default a data key is wrapped by the platform KEK. With an external provider active, NEW data
keys are wrapped by a key that never leaves that service:

| Provider | Key named by | Credentials |
|---|---|---|
| `aws_kms` | key id, ARN or alias | access key + secret, else the host's role (`boto3`) |
| `azure_key_vault` | key identifier URL | tenant + client id + secret, else the managed identity (`azure-keyvault-keys`, RSA-OAEP-256) |
| `gcp_kms` | key resource name | service-account key JSON, else ADC (`google-cloud-kms` < 3 — 3.x needs protobuf 6) |
| `vault_transit` | transit key name (+ mount, namespace) | token, over HTTPS (`httpx`, no SDK) |

- **The wrapped key names its wrapper:** `kms1.<provider>.<b64url key reference>.<b64url ciphertext>`
  (`files.key_version` 2; the platform wrap stays 1). Unwrapping follows the name, with that
  provider's saved settings, so switching key or provider never strands a file.
- **Bound to the file:** the payload sent to the key manager is `UFK1` + sha256("dek:"+file_id)[:16]
  + the key, checked on unwrap — a wrapped key copied onto another file does not open it (the
  binding the platform wrap gets from AES-GCM's associated data).
- **Cached briefly:** unwrapped keys live in memory for 300 s (512 entries), so an attachment read
  on every turn is not a key-manager call each time. Revoking the key in the manager makes its
  files unreadable within that window.
- **Settings:** `file_store_settings.kek_configs`, sealed under the platform key (context
  `kek-configs`), secrets masked on read and kept when left masked; `kek_provider` names the
  active one. `PUT /v1/file-stores/keys` saves only after a passing live wrap/unwrap test (audit
  `file_keys_provider`).
- **Re-wrap:** `POST /v1/file-stores/keys/rewrap {dry_run}` moves every file's data key under the
  active key — no object is rewritten (audit `file_keys_rewrapped`, with moved and failed counts).
- **Stays under the platform key:** content fingerprints and sealed records (attachment
  manifests, store settings, the key-manager settings themselves).

## 6. Components that produce files

| Source | How it reaches Files | Recorded |
|---|---|---|
| any export | *Save to Files* after the download (§3.10), or `?to=files` on a server route | `component_id`, `instance_id`; a turn's export carries the turn |
| dashboard | *Save snapshot to Files* (header camera) → `POST /v1/files/dashboard-snapshot`. The SERVER writes the PDF from the tiles' own titles, order, refresh times, agents, tables and text; only drawn tiles are sent as pictures (keyed by tile id). A chart tile is drawn again off-screen in light colours, whatever the viewer's theme | `source_json.tiles[]`: each tile's refresh time and how it was captured (`picture · table · text`) |
| flow | **File input** node (reads a kept table as rows — the shape a query gives — a document as text, JSON as its value) and **File output** node (keeps its input: rows → CSV, object → JSON, text → Markdown; name template with `$name`/`{name}`, `{date}`, `{time}`, `{run}`). Both deterministic, offered only while Files is available | `origin_kind: flow_run`, `flow_id`, `flow_run_id`, `flow_node_id`, the flow and node names |
| knowledge | *Add to knowledge* on a document → `POST /v1/files/<id>/to-knowledge` dispatches the repository's OWN upload route in process with the caller's credentials (owner check, ingest firewall, chunking, embedding, refresh-not-duplicate) | audit `file_to_knowledge` |
| the agent | `TDA_Files` (§7) | `source_kind: agent`, the turn |

## 7. The agent's own file acts — `TDA_Files`

`list` (search by name, conversation, question) · `read` (a table as columns and rows, AND
registered as a statement of the turn so `TDA_Compute` can combine it; a document as bounded
text) · `save` (`content` the model wrote, typed by the name's extension; or `statement` — a
query result of THIS turn, written as CSV from the result-set registry, never retyped). A
duplicate returns the existing file.

- **Opt-in per agent.** Files stays enabled for every agent (panel, Keep, attachments); the
  TOOL is offered only where `componentConfig.files.agentTool` is on (profile editor:
  Capabilities → Files → *The agent can list, read and save files itself*). This is the
  component handler's `offers_tool` hook (`components/base.py`), read by every tool surface and
  by every action-affordance resolution (`enablement.offers_tool`), so the companion skill
  `files-expert` and the component instructions follow the tool: an agent that has not opted in
  carries zero bytes of either, and its signature is unchanged. `agentTool` lives in the signed
  `componentConfig` — turning it on is a capability change.
- **Agent Authority.** A surface-only agent (`dataSurface.exclusive`) may not read a file that
  came from a data source; the refusal names the reason. Saving needs no write grant — a file in
  the user's own Files changes no data source.
- **Evidence.** A file saved during its turn has no bill of materials yet; the first read after
  the turn copies it (`service._backfill_bom`).

## 7a. Table attachments

`.csv`, `.tsv` and `.xlsx` attach to a question like any document, on every engine, without the
agent's own Files tool. The model reads a bounded pipe table
(`DocumentUploadHandler.table_text`: header line `[N rows · M columns]`, at most 500 rows and
60 000 characters, then `[… — the first K are shown]`), parsed by the same `formats.read_table`
that `TDA_Files read` uses. The file picker's accept list is read from the capabilities the server
reports, so the paperclip offers exactly what the server extracts.

## 8. The scheduler

`output_channel: "files"` (or `output_config.keep_in_files: true` beside email/webhook) keeps each
successful run's answer and tables as one series under the task (`scheduled_task_id`); the email
names the kept files, the webhook payload lists them with their download route.

## 9. UX conventions (gate: `test/test_files_ux.mjs`)

Shared `.sp-*` chrome; the manifest accent; Workspace entry with its real total; industrial
controls (`.card-btn`, `.ind-tab--pill`); `data-tip` tooltips; `showConfirmBanner` /
`showAppBanner`; IFOC words with the class colour as a dot; theme variables; Keep revealed only
through `ComponentGate` for the agent that answered and "Kept · Open" read from the server. The
panel opens at 820 px (`data-sp-default-width`); below 760 px a file opens in place of the list.

- **Remembered per conversation** (the knowledge-graph / canvas / dashboard pattern): opening the
  panel, and selecting a file in it, writes `files_state_<session>` (`{open, focus}`) to local
  storage; a session switch closes the panel but keeps the marker; returning to the conversation,
  or reloading the page, reopens it on the same file (`sessionManagement._restoreFilesState`, on
  both load paths). Only the user's own close removes the marker.
- **Components → Files → Files tab** (`static/js/handlers/filesTabHandler.js`), the
  instance-management tab every stateful component has (Canvases, Dashboards, Flows, Knowledge
  Graphs, Jobs): search, the panel's kind filters with counts, the storage used, and per file
  rename · keep · download (the governed route) · delete · restore, the facts (lifetime, origin,
  the conversation by its CURRENT name — `provenance.session_title` — agent, store, created) and
  *Used in*; *Open in Files panel* for preview, attach and the rest. Live on `UderiaBus.onComponent('files')`.
- **"Used in" is live:** `service.record_use` announces the Files change with action `used`; the
  panel refreshes only the open file's detail (no list reload), so a turn that attached the file
  shows under *Used in* as the turn starts.

The companion skill `files-expert` carries the judgement (what to keep, naming, keeping a query
result by statement, finding the file meant); `instructions.json` is the correctness baseline.

## 10. Gates

| Gate | Proves |
|---|---|
| `test_attachment_storage_freeze.py` | every observable of an attachment is unchanged by the storage move (195 observations, recorded on the old code) |
| `test_file_store.py` | crypto, backends, resolution, write/read/exit, attachments, migration, keep-from-turn, downloads and policy, lifecycle, listing, capacity, settings, saved exports, server-export governance, dashboard snapshot, flow nodes, `TDA_Files` incl. Agent Authority, the bill-of-materials backfill, table attachments, the backend catalog + Azure/GCS (fake SDKs), the key managers (wrap/unwrap, binding, cache, re-wrap, rotation skip), the `used` announcement, worker-thread notices reach the page, the conversation's current name, a deleted attachment (not read by a turn, forgotten on purge across conversations, restorable before) (257) |
| `test_files_ux.mjs` | the component conventions above, the one export exit, snapshot, flow nodes, knowledge, the agent opt-in, the catalog-driven form, the Keys tab (test before use, dry-run re-wrap), the per-conversation panel memory, live *Used in*, the Components → Files tab, the chip's Deleted/Restore state (121) |
| `test_component_enablement.py` | the tool gate narrows only the action affordances; `TDA_Files`, its instructions and its companion follow the opt-in |
| `test_rotate_encryption_key.py` | rotation covers the file store's key family, leaves externally wrapped keys alone and reseals the key-manager settings |

The cloud backends and key managers were also rehearsed against real SDKs on emulators
(Azurite, fake-gcs-server, LocalStack KMS, Vault dev): round trips, a disabled key and a bad token
are reported in words, a Vault-wrapped file downloads, re-wrap moves every key with zero failures.
