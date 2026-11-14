# Platform roles

What an agent with **TDA_Platform** is *for*. A role names the platform operations an agent may
use across namespaces, the effects it may call, and the playbooks it is taught. Roles belong to
the Platform component: its `manifest.json` declares them (`roles`: where an agent's grant lives,
the default, this folder, the playbook skill), and `core/platform_roles.py` reads that
declaration. For agent owners: [the Platform Roles guide](../../../../docs/manuals/PLATFORM_ROLES.md)
— what each role offers and does not, with examples and the playbook appendix. Contract and
rationale: [PLATFORM_OPERATIONS_ARCHITECTURE.md §17, §21](../../../../docs/Architecture/PLATFORM_OPERATIONS_ARCHITECTURE.md).

**A role is not a user tier.** The tier (user · developer · admin) says who the user is and
gates each operation through its own descriptor; a role says what the agent is for and carries
**no tier** — the gate refuses one.

## A role file — `<id>.json` (the file name is the id)

| Key | Meaning |
|---|---|
| `id` | lower-kebab, equals the file name |
| `title`, `description` | how people and the model read the role |
| `status` | `active` (grants its surface) or `planned` (grants nothing yet) |
| `rank` | its place in the work order (unique) |
| `effects` | what it may CALL: `read`, `write` — never a person's act |
| `operations` | op-id globs across namespaces (`profiles.*`); a leading `!` excludes and wins |
| `playbooks` | `<skill>:<anchor glob>` — the playbooks it is taught |
| `needs` | the namespaces a planned role still waits for |
| `examples` | `{ask, outcome}` — what a person asks and what the agent does (and where it stops); cases tested live |
| `not_offered` | `{what, why, ops?, args?}` — what it does NOT do and why; `ops` the person's acts and cross-user operations it lists that the item explains (globs), `args` the arguments left out of an operation it uses (`op:argument`) |

`examples` and `not_offered` are words about the role: shown in the guide and the Roles tab,
never part of its digest. The gate holds them to the catalog — **every person's act and
cross-user operation a role lists must be explained in its own `not_offered`**, every `ops`
pattern must match something it lists, and every `args` entry must name an argument the
operation really leaves out.

## Making a role active

1. Describe every namespace it needs (`maintenance/platform_operations.py draft --prefix …`).
2. Write its playbooks in `skills/builtin/platform-playbooks/library/<role area>/`.
3. Give it `examples` and `not_offered` (every person's act it lists explained), and regenerate
   the guide (`python maintenance/platform_operations.py manual --write`).
4. Set `"status": "active"`; run `python maintenance/platform_operations.py roles` and
   `PYTHONPATH=src python test/test_platform_roles.py` — an active role whose patterns match no
   described operation, or whose playbook patterns match no playbook, fails the gate.

## Giving an agent a role

Choose them in the agent editor (Capabilities → Platform) — stored as
`componentConfig.platform.roles: ["guide", "agent-builder"]` (no roles → the manifest's default,
the Guide). It is capability, so signed: each held role enters the agent's signature with a
digest of what it grants (status, effects, operations, playbooks), so **changing what a role
grants moves the signature of every agent that holds it** — re-sign after a release that widens
a role. Its title, description, needs and rank are words about it and do not. A planned or
unknown role grants nothing.

## Seeing what a role does

Components → Platform → **Roles** shows each role's operations (what it reads, the changes it
can submit — the ones a person always approves marked — and what it lists but cannot call, with
why), its playbooks, its digest and the agents that hold it; the agent editor opens the same
reading under each role; Components → Skill Library → Libraries shows, per playbook, the roles
taught it. All of it is `GET /v1/platform/roles?detail=1`, computed by the rules TDA_Platform
enforces.
