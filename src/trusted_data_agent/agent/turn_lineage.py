"""S8 — Regenerate as an Experiment: sibling turns and edit branches.

A *regenerate* is a SCORED SIBLING: a new turn under the SAME user message (no second user
message), scored by the evaluation layer like any turn, compared by its verdicts. Exactly one
sibling of a set is ADOPTED — the one the conversation continues from — and adoption is the
only thing that moves: the record keeps every sibling (append-only), the model's context keeps
only the adopted one.

An *edit-and-resend* is a BRANCH: the edited user message becomes the head of a new branch; the
turns it displaces stay in the record; the view (and the model's context) follows the ACTIVE
branch, and switching back restores them.

ONE mechanism for "not in the model's context": the existing `isValid` flag every history
reader already honours (the purge toggle's flag) — a non-adopted sibling and a displaced branch
turn are invalid for exactly the reason a purged turn is. Nothing here rewrites history: a turn
record is never removed or edited beyond its own lineage keys and its validity.

Data (all additive; every reader tolerates their absence):
  turn record   sibling_of: <root turn>   adopted: bool   branch_of: <edited turn>   branch: <head>
  message       sibling_of (the sibling's assistant message)  ·  branch_of (the edit's user message)
  session       branches: {"active": <head>, "heads": [{"turn", "of", "parent"}]}

The per-turn relation is a task-local contextvar armed at the execution seam
(`execution_service`) and read by the two session_manager seams (message add, turn persist);
unset ⇒ nothing stamped ⇒ byte-identical baseline.
"""
from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from trusted_data_agent.core.config import APP_CONFIG

log = logging.getLogger("quart.app")

KINDS = ("regenerate", "edit")
ROOT_BRANCH = 0


def enabled() -> bool:
    return bool(getattr(APP_CONFIG, "TURN_LINEAGE", False))


# ── the per-turn relation ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TurnRelation:
    kind: str                 # "regenerate" | "edit"
    of: int                   # regenerate: the turn asked for (may itself be a sibling); edit: the edited turn
    root: int                 # regenerate: the sibling set's root; edit: == of
    new_turn: int             # the number the new record will take (len(workflow_history) + 1 at arming)
    query: str                # the user query the new turn runs (a regenerate's is the root's, verbatim)
    branch: int = ROOT_BRANCH  # the branch the new record belongs to (edit: its own head == new_turn)
    carried: Dict[str, Any] = field(default_factory=dict)  # a regenerate's inherited attachments/specs

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "of": self.of, "root": self.root, "new_turn": self.new_turn, "branch": self.branch}


_TURN: contextvars.ContextVar[Optional[TurnRelation]] = contextvars.ContextVar("s8_turn_relation", default=None)


def set_turn(rel: Optional[TurnRelation]):
    return _TURN.set(rel)


def reset_turn(token) -> None:
    try:
        _TURN.reset(token)
    except Exception:  # noqa: BLE001
        _TURN.set(None)


def current() -> Optional[TurnRelation]:
    return _TURN.get()


# ── pure readers over a session body ───────────────────────────────────────────────────────
def records(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    wf = ((session or {}).get("last_turn_data") or {}).get("workflow_history")
    return [t for t in wf if isinstance(t, dict)] if isinstance(wf, list) else []


def find_turn(session: Dict[str, Any], n: int) -> Optional[Dict[str, Any]]:
    for t in records(session):
        if t.get("turn") == n:
            return t
    return None


def sibling_root(session: Dict[str, Any], n: int) -> int:
    """Follow `sibling_of` to the set's root (a sibling of a sibling still names the root, but
    an older record may not — resolve transitively, bounded)."""
    seen = set()
    cur = n
    while True:
        t = find_turn(session, cur)
        if not t or cur in seen:
            return cur
        seen.add(cur)
        parent = t.get("sibling_of")
        if not isinstance(parent, int) or parent == cur:
            return cur
        cur = parent


def sibling_set(session: Dict[str, Any], root: int) -> List[int]:
    """The root first, then every sibling in record order."""
    out = [root] if find_turn(session, root) is not None else []
    for t in records(session):
        n = t.get("turn")
        if isinstance(n, int) and n != root and t.get("sibling_of") is not None and sibling_root(session, n) == root:
            out.append(n)
    return out


def is_sibling_set(session: Dict[str, Any], root: int) -> bool:
    return len(sibling_set(session, root)) > 1


def turn_branch(t: Optional[Dict[str, Any]]) -> int:
    b = (t or {}).get("branch")
    return b if isinstance(b, int) else ROOT_BRANCH


def heads(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    br = (session or {}).get("branches")
    hs = br.get("heads") if isinstance(br, dict) else None
    return [h for h in hs if isinstance(h, dict) and isinstance(h.get("turn"), int)] if isinstance(hs, list) else []


def active_head(session: Dict[str, Any]) -> int:
    br = (session or {}).get("branches")
    a = br.get("active") if isinstance(br, dict) else None
    return a if isinstance(a, int) else ROOT_BRANCH


def branch_view(session: Dict[str, Any], head: int) -> Set[int]:
    """The turns in the lineage of `head`: the head's own turns, then its parent branch cut at
    the edited turn, recursively to the root. Siblings are NOT resolved here (the view is a
    set of turns; which sibling stands for a root is `effective_adopted`)."""
    by_turn = {h["turn"]: h for h in heads(session)}
    view: Set[int] = set()
    cur, cutoff, guard = head, None, 0
    while guard < 64:
        guard += 1
        for t in records(session):
            n = t.get("turn")
            if not isinstance(n, int) or turn_branch(t) != cur:
                continue
            # a sibling travels with its QUESTION: the cut-off is judged on the root's number,
            # so a regenerate made after a branch point still shows wherever its question does
            key = sibling_root(session, n) if t.get("sibling_of") is not None else n
            if cutoff is None or key < cutoff:
                view.add(n)
        if cur == ROOT_BRANCH:
            break
        h = by_turn.get(cur)
        if not h:
            break
        cutoff = h.get("of") if isinstance(h.get("of"), int) else None
        cur = h.get("parent") if isinstance(h.get("parent"), int) else ROOT_BRANCH
    return view


def current_view(session: Dict[str, Any]) -> Set[int]:
    return branch_view(session, active_head(session))


def effective_adopted(session: Dict[str, Any], root: int, view: Optional[Set[int]] = None) -> Optional[int]:
    """The sibling that stands for `root` in the view: the adopted one when it is in view, else
    the newest sibling in view (a branch switch may have taken the adopted one out of view)."""
    view = current_view(session) if view is None else view
    members = [n for n in sibling_set(session, root) if n in view]
    if not members:
        return None
    for n in members:
        t = find_turn(session, n)
        if t and t.get("adopted") is True:
            return n
    if len(members) == 1:
        return members[0]
    # no explicit adoption in view: the root if present, else the newest
    return root if root in members else max(members)


# ── validity: ONE rule, applied to a set of turns ─────────────────────────────────────────
def _messages(session: Dict[str, Any]):
    for key in ("session_history", "chat_object"):
        lst = (session or {}).get(key)
        if isinstance(lst, list):
            for m in lst:
                if isinstance(m, dict):
                    yield m


def _is_user_role(m: Dict[str, Any]) -> bool:
    return m.get("role") == "user"


def _with_sibling_sets(session: Dict[str, Any], turns) -> List[int]:
    """The turns plus every member of a sibling set any of them belongs to — a switch that takes
    the adopted sibling out of view must re-evaluate the root, whose own membership never moved."""
    out = set()
    for n in turns:
        out.update(sibling_set(session, sibling_root(session, n)) or [n])
    return sorted(out)


def sync_validity(session: Dict[str, Any], turns: Sequence[int]) -> Dict[str, bool]:
    """Recompute `isValid` for exactly `turns` (never the rest — a user's own purge on an
    untouched turn survives): a turn is in context iff it is in the active view AND it is the
    sibling standing for its root. A root's USER message follows the view alone (the question is
    shared by every sibling); assistant messages and the record follow the adopted sibling."""
    view = current_view(session)
    result: Dict[str, bool] = {}
    for n in turns:
        t = find_turn(session, n)
        if t is None:
            continue
        root = sibling_root(session, n)
        in_view = n in view
        stands = (effective_adopted(session, root, view) == n) if is_sibling_set(session, root) else in_view
        record_valid = bool(in_view and stands)
        t["isValid"] = record_valid
        result[str(n)] = record_valid
        for m in _messages(session):
            if m.get("turn_number") != n:
                continue
            m["isValid"] = bool(in_view) if _is_user_role(m) else record_valid
    return result


# ── the acts ───────────────────────────────────────────────────────────────────────────────
def apply_adoption(session: Dict[str, Any], turn: int) -> Dict[str, Any]:
    """Make `turn` the sibling the conversation continues from. Append-only: every sibling stays
    in the record; only `adopted` and validity move."""
    root = sibling_root(session, turn)
    members = sibling_set(session, root)
    if turn not in members or len(members) < 2:
        raise ValueError(f"turn {turn} has no siblings to adopt among")
    for n in members:
        t = find_turn(session, n)
        if t is not None:
            t["adopted"] = (n == turn)
    sync_validity(session, members)
    return {"root": root, "adopted": turn, "siblings": members}


def register_head(session: Dict[str, Any], new_turn: int, of: int) -> Dict[str, Any]:
    """Start a branch at the edited turn `of`: record the head, make it active, and move the
    view — the displaced turns become invalid, the new branch's prefix stays. Returns the diff."""
    of_rec = find_turn(session, of)
    if of_rec is None:
        raise ValueError(f"turn {of} does not exist")
    if of_rec.get("sibling_of") is not None:
        raise ValueError(f"turn {of} is a regenerate — edit the turn that carries the question")
    old_view = current_view(session)
    if of not in old_view:
        raise ValueError(f"turn {of} is not on the active branch")
    br = session.setdefault("branches", {"active": ROOT_BRANCH, "heads": []})
    if not isinstance(br.get("heads"), list):
        br["heads"] = []
    br["heads"].append({"turn": new_turn, "of": of, "parent": turn_branch(of_rec)})
    br["active"] = new_turn
    new_view = current_view(session)
    touched = _with_sibling_sets(session, old_view | new_view)
    sync_validity(session, touched)
    return {"active": new_turn, "of": of, "displaced": sorted(old_view - new_view), "view": sorted(new_view)}


def switch_branch(session: Dict[str, Any], head: int) -> Dict[str, Any]:
    """Make `head` (0 = the root branch) the active branch. Only the turns whose membership
    changes are touched."""
    if head != ROOT_BRANCH and head not in {h["turn"] for h in heads(session)}:
        raise ValueError(f"branch {head} does not exist")
    old_view = current_view(session)
    br = session.setdefault("branches", {"active": ROOT_BRANCH, "heads": []})
    br["active"] = head
    new_view = current_view(session)
    touched = _with_sibling_sets(session, (old_view - new_view) | (new_view - old_view))
    sync_validity(session, touched)
    return {"active": head, "restored": sorted(new_view - old_view), "displaced": sorted(old_view - new_view), "view": sorted(new_view)}


# ── the seams' hooks ───────────────────────────────────────────────────────────────────────
def message_turn_number(rel: Optional[TurnRelation], role: str, default: int) -> int:
    """A regenerate's assistant message carries the sibling's OWN turn number (there is no user
    message to derive it from); everything else keeps today's derivation."""
    if rel is not None and rel.kind == "regenerate" and role != "user":
        return rel.new_turn
    return default


def stamp_message(rel: Optional[TurnRelation], role: str, message: Dict[str, Any]) -> None:
    if rel is None:
        return
    if rel.kind == "regenerate" and role != "user":
        message["sibling_of"] = rel.root
    elif rel.kind == "edit" and role == "user":
        message["branch_of"] = rel.of


def insert_index(history: List[Dict[str, Any]], rel: Optional[TurnRelation], role: str) -> Optional[int]:
    """Where a regenerate's assistant message goes: right after the last message of its sibling
    set, so the model's context (and the reader's timeline) keeps the answer beside its question.
    None ⇒ append (today's behaviour)."""
    if rel is None or rel.kind != "regenerate" or role == "user":
        return None
    members = set()
    for m in history:
        if isinstance(m, dict) and (m.get("turn_number") == rel.root or m.get("sibling_of") == rel.root):
            members.add(id(m))
    last = None
    for i, m in enumerate(history):
        if id(m) in members:
            last = i
    return None if last is None else last + 1


def stamp_record(session: Dict[str, Any], rel: Optional[TurnRelation], turn_data: Dict[str, Any]) -> None:
    """At the persistence chokepoint (before the record is appended): the lineage keys."""
    if not enabled():
        return
    if rel is not None:
        if rel.kind == "regenerate":
            turn_data["sibling_of"] = rel.root
            turn_data["branch"] = rel.branch
        elif rel.kind == "edit":
            turn_data["branch_of"] = rel.of
            turn_data["branch"] = rel.new_turn
        return
    # an ordinary turn on a session that has branches belongs to the active one
    if isinstance((session or {}).get("branches"), dict):
        turn_data["branch"] = active_head(session)


def after_record(session: Dict[str, Any], rel: Optional[TurnRelation], turn_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """After the record is appended: a regenerate is adopted on arrival (the reason to
    regenerate is to try again — Adopt lets the reader go back). Returns what moved."""
    if rel is None or rel.kind != "regenerate":
        return None
    n = turn_data.get("turn")
    if not isinstance(n, int):
        return None
    return apply_adoption(session, n)


# ── building a relation from a request ─────────────────────────────────────────────────────
def build_relation(session: Dict[str, Any], request: Dict[str, Any]) -> TurnRelation:
    """Validate a regenerate/edit request against the session and resolve everything the turn
    needs — the query it runs, the number it will take, the branch it belongs to. Raises
    ValueError with a reader-facing reason."""
    kind = (request or {}).get("kind")
    if kind not in KINDS:
        raise ValueError(f"unknown lineage kind {kind!r}")
    of = (request or {}).get("of")
    if not isinstance(of, int):
        raise ValueError("`of` must be a turn number")
    rec = find_turn(session, of)
    if rec is None:
        raise ValueError(f"turn {of} does not exist in this session")
    if rec.get("is_session_primer"):
        raise ValueError("a session primer cannot be regenerated or edited")
    if kind == "edit" and rec.get("sibling_of") is not None:
        raise ValueError(f"turn {of} is a regenerate — edit the turn that carries the question")
    view = current_view(session)
    if of not in view:
        raise ValueError(f"turn {of} is not on the active branch")
    new_turn = len(records(session)) + 1
    if kind == "regenerate":
        root = sibling_root(session, of)
        root_rec = find_turn(session, root) or rec
        query = root_rec.get("user_query") or rec.get("user_query") or ""
        if not str(query).strip():
            raise ValueError(f"turn {of} carries no question to regenerate")
        carried = {}
        for m in (session.get("session_history") or []):
            if isinstance(m, dict) and m.get("role") == "user" and m.get("turn_number") == root:
                for k in ("attachments", "extension_specs", "skill_specs"):
                    if m.get(k):
                        carried[k] = m[k]
                break
        # the sibling inherits its ROOT's branch (answers travel with their question — a
        # regenerate is visible on every branch that shows the question it answers)
        return TurnRelation(kind="regenerate", of=of, root=root, new_turn=new_turn, query=str(query),
                            branch=turn_branch(root_rec), carried=carried)
    # edit
    prompt = (request or {}).get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("`prompt` is required for an edit")
    return TurnRelation(kind="edit", of=of, root=of, new_turn=new_turn, query=prompt.strip(), branch=new_turn)


# ── what the reader sees ───────────────────────────────────────────────────────────────────
def lineage_payload(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The served view of a session's lineage — None when the switch is off or nothing ever
    branched or regenerated (so an untouched session serves exactly today's payload)."""
    if not enabled():
        return None
    recs = records(session)
    roots: Dict[int, List[int]] = {}
    for t in recs:
        n = t.get("turn")
        if isinstance(n, int) and t.get("sibling_of") is not None:
            root = sibling_root(session, n)
            roots.setdefault(root, [])
    hs = heads(session)
    if not roots and not hs:
        return None
    view = current_view(session)
    siblings = {}
    for root in roots:
        members = sibling_set(session, root)
        adopted = effective_adopted(session, root, view)
        siblings[str(root)] = {
            "adopted": adopted,
            "turns": [{"turn": n, "adopted": n == adopted,
                       "profile_tag": (find_turn(session, n) or {}).get("profile_tag"),
                       "model": (find_turn(session, n) or {}).get("model"),
                       "provider": (find_turn(session, n) or {}).get("provider"),
                       "in_view": n in view} for n in members],
        }
    return {"siblings": siblings,
            "branches": {"active": active_head(session), "heads": hs, "view": sorted(view)}}
