"""
Conversation-lane bench (docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §3) — drives a fixed
question set per profile CLASS through the UI stream (/ask_stream, the path a person uses),
fresh session per question, and measures what the person experiences:

  time-to-first-token (first answer_delta) · time-to-usable-answer (final_answer)
  · time-to-verified (turn_score_complete, when it lands on the stream)
  · LLM steps · tool calls · refusals · rewrites (existing events)
  · input/output tokens · cost (the persisted turn)
  · partial-answer preservation under an injected Stop (S1)

Run:  python3 test/performance/conversation_lane_bench.py --label after
      python3 test/performance/conversation_lane_bench.py --compare before after
      python3 test/performance/conversation_lane_bench.py --classes ideate,react --label quick

Results land in test/performance/results/conversation_lane_<label>.json. The `fusion_bench`
precedent: every increment must show its metric moving and every other metric flat.
"""
import argparse, glob, json, os, re, sys, time, statistics as st
import httpx, requests

BASE = os.environ.get("UDERIA_BASE_URL", "http://localhost:5050")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# One profile per class (local admin instance); override with --profiles class=TAG,...
CLASS_PROFILES = {"ideate": "IDPURE", "react": "IDGGT", "focus": "UDERIA", "optimize": "OPGGT", "coordinate": "COGGT"}
QUESTIONS = {
    "ideate": ["In three short paragraphs, explain what a knowledge graph is and why an analyst would want one.",
               "List five common causes of slow SQL queries and one fix for each."],
    "react": ["How many customers are in the fitness database, and which three cities have the most?",
              "Which product category has the highest total sales quantity? Answer in two sentences.",
              "List the price of every product, then give me the total and the average price.",
              # S5 — a question that needs more steps than a small budget allows (the budget case)
              "Using base_readQuery only: run five separate COUNT(*) queries, one each for fitness_db.Customers, fitness_db.Sales, fitness_db.Products, fitness_db.ServiceTickets and fitness_db.SaleDetails, and report each count.",
              # S7 — a question that invites a figure no result holds (the outcome-loop case)
              "Using base_readQuery, count the rows in fitness_db.Customers and report the count. Then also give a rough estimate, as one concrete number without any further query, of how many of those customers live in Berlin."],
    "focus": ["What is the difference between a planner repository and a knowledge repository?",
              "How does a profile bind to a context window type?"],
    "optimize": ["How many customers are in the fitness database?",
                 "What are the top 3 products by revenue?",
                 "List the price of every product in the fitness_db database, then report the average price across them.",
                 # S5 — three separate tool phases the plan cannot consolidate (the budget case)
                 "First use base_tableList to list all tables in fitness_db. Then use base_columnDescription on fitness_db.Customers. Then run a COUNT(*) query on fitness_db.Sales. Report all three results.",
                 # S6 — a question whose tool the plain catalog holds back (the surface case)
                 "Which tables in fitness_db use the most disk space?",
                 # S7 — a question that invites a figure no result holds (the outcome-loop case)
                 "Count the rows in fitness_db.Customers and report the count. Then also give a rough estimate, as one concrete number without any further query, of how many of those customers live in Berlin."],
    "coordinate": ["How many customers are in the fitness database, and what does that tell us about the business?"],
}
STOP_AFTER_S = 4.0   # the injected Stop, at the latest
STOP_QUESTION = "Write a long, detailed essay (at least 900 words) about the history of relational databases, with headings."


def login(user="admin", pw="admin"):
    r = requests.post(f"{BASE}/api/v1/auth/login", json={"username": user, "password": pw}, timeout=30)
    r.raise_for_status(); return r.json()["token"], r.json().get("user", {})


def resolve_profile(jwt, tag):
    r = requests.get(f"{BASE}/api/v1/profiles", headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
    r.raise_for_status(); d = r.json(); ps = d if isinstance(d, list) else d.get("profiles", d)
    for p in ps:
        if p.get("tag") == tag: return p["id"]
    raise SystemExit(f"profile @{tag} not found")


def stream_turn(jwt, profile_id, question, *, stop_after_first_delta=False, timeout_s=300, session_id=None, attachments=None,
                drop_after=None, steer=None):
    """Drive one turn over /ask_stream; return the experienced timings + event counts.
    ``session_id`` reuses a session (the chained mode — where a prompt cache earns its keep);
    absent, every turn opens a fresh session."""
    h = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    sid = session_id or requests.post(f"{BASE}/api/v1/sessions", headers=h, json={}, timeout=30).json()["session_id"]
    body = {"message": question, "session_id": sid, "profile_override_id": profile_id}
    if attachments:
        # S9 — upload the files into THIS session first (the platform reads them at upload)
        body["attachments"] = _upload(jwt, sid, attachments)
    t0 = time.time(); m = {"session_id": sid, "t_first_delta": None, "t_final": None, "t_verified": None,
                           "deltas": 0, "resets": 0, "llm_steps": 0, "tool_calls": 0, "refusals": 0, "rewrites": 0,
                           "final_partial": None, "stream_summary": None, "stopped": False, "error": None,
                           # S10 — the cursor the stream reached, the task, whether the connection was DROPPED
                           # and the turn RESUMED from the cursor (frames replayed + tailed), the steer sent
                           "task_id": None, "cursor": 0, "frames": 0, "dropped": False, "resumed": None, "frames_after_drop": 0,
                           "steer_sent": None}
    stopped_sent = False

    def consume(iter_text, on_frame=None):
        """Consume frames to the END of the stream (the server closes it only after the turn is
        persisted — returning at final_answer would race the record). Returns True when the
        final answer was delivered; `on_frame` may cut the connection early (the S10 drop)."""
        nonlocal stopped_sent
        buf = ""; done = False
        for chunk in iter_text:
            buf += chunk
            while "\n\n" in buf:
                msg, buf = buf.split("\n\n", 1); data = ev = None; seq = None
                for line in msg.splitlines():
                    if line.startswith("data: "): data = line[6:]
                    elif line.startswith("event: "): ev = line[7:]
                    elif line.startswith("id: "):
                        try: seq = int(line[4:])
                        except ValueError: seq = None
                if data is None: continue
                try: d = json.loads(data)
                except Exception: continue
                if seq is not None and seq > m["cursor"]: m["cursor"] = seq
                m["frames"] += 1
                now = time.time() - t0
                if ev == "task_start": m["task_id"] = d.get("task_id")
                if ev in ("reattach_gap", "reattach_end"): continue
                if _handle(d, ev, now): done = True
                if on_frame and on_frame(): return done
        return done

    def _handle(d, ev, now):
        nonlocal stopped_sent
        if ev == "answer_delta":
            m["deltas"] += 1
            if d.get("reset"): m["resets"] += 1
            if m["t_first_delta"] is None: m["t_first_delta"] = round(now, 3)
            if stop_after_first_delta and not stopped_sent and m["deltas"] >= 2:
                time.sleep(0.4)
                requests.post(f"{BASE}/api/session/{sid}/cancel_stream", headers=h, timeout=30)
                stopped_sent = True; m["stopped"] = True
        if stop_after_first_delta and not stopped_sent and now > STOP_AFTER_S:
            # no delta yet (streaming off, or a slow first token): stop on the clock so the
            # probe measures the same act in both configurations
            requests.post(f"{BASE}/api/session/{sid}/cancel_stream", headers=h, timeout=30)
            stopped_sent = True; m["stopped"] = True
        elif ev == "final_answer":
            m["t_final"] = round(now, 3); m["final_partial"] = bool(d.get("partial"))
            m["stream_summary"] = d.get("answer_stream"); m["final_chars"] = len(re.sub("<[^>]+>", "", str(d.get("final_answer") or "")))
        elif ev == "turn_score_complete":
            m["t_verified"] = round(now, 3)
        elif ev == "error":
            m["error"] = d.get("error")
        elif ev == "notification":
            t = d.get("type"); p = d.get("payload") or {}
            if t in ("conversation_llm_step",): m["llm_steps"] += 1
            elif t == "conversation_tool_invoked": m["tool_calls"] += 1
            elif t == "conversation_tool_rewrite": m["rewrites"] += 1
            elif t == "conversation_tool_completed" and '"preflight": true' in json.dumps(p): m["refusals"] += 1
        return ev == "final_answer"

    with httpx.Client(timeout=timeout_s) as c:
        with c.stream("POST", f"{BASE}/ask_stream", json=body, headers=h) as r:
            # S10 — with `drop_after`, the connection is CUT after that many frames (the turn keeps
            # running server-side); the client then steers (optionally) and resumes from its cursor
            done = consume(r.iter_text(), on_frame=(lambda: drop_after is not None and m["frames"] >= drop_after))
        if not done and drop_after is not None and m["task_id"]:
            m["dropped"] = True
            if steer:
                sr = requests.post(f"{BASE}/api/v1/sessions/{sid}/steer", headers=h, json={"text": steer}, timeout=30)
                m["steer_sent"] = 1 if sr.status_code == 202 else 0
            before = m["frames"]
            with c.stream("GET", f"{BASE}/api/v1/tasks/{m['task_id']}/stream?since={m['cursor']}", headers={"Authorization": f"Bearer {jwt}"}) as r2:
                if r2.status_code == 200:
                    done = consume(r2.iter_text())
            m["frames_after_drop"] = m["frames"] - before
            m["resumed"] = 1 if done else 0
    return m


def _numeric_fidelity(turn):
    """The numeric-fidelity measurement over the persisted turn (the eval scorer, unthresholded)."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
        from trusted_data_agent.eval.scorers.reliability import NumericFidelityScorer
        from trusted_data_agent.eval.types import RunRecord, EvalCase
        v = NumericFidelityScorer().score(EvalCase(id="b", query=turn.get("user_query") or "", profile_class=turn.get("profile_type") or "tool_enabled"), RunRecord(turn))
        return v.score
    except Exception:
        return None


def read_turn(user_uuid, sid):
    files = glob.glob(f"tda_sessions/{user_uuid}/{sid}.json") + glob.glob(f"tda_sessions/{user_uuid}/{sid}/conversation.json")
    if not files: return {}
    d = json.load(open(files[0])); wh = d.get("last_turn_data", {}).get("workflow_history") or []
    return wh[-1] if wh else {}


def _upload(jwt, sid, paths):
    """S9 — upload files into a session; returns the attachment refs the query body carries."""
    h = {"Authorization": f"Bearer {jwt}"}
    files = [("files", (os.path.basename(p), open(p, "rb"))) for p in paths]
    r = requests.post(f"{BASE}/api/v1/chat/upload", headers=h, data={"session_id": sid}, files=files, timeout=120)
    r.raise_for_status()
    return [{"file_id": f["file_id"], "filename": f["original_filename"]} for f in r.json().get("files", [])]


def _attachment_cols(turn):
    """S9 — what the record says about the turn's attachments: how many were read as a source,
    how many the platform VERIFIED (vs a model transcription), and how many of the answer's
    figures trace to an attachment table (a `quoted` figure whose statement is an attachment)."""
    a = turn.get("attachment_sources") or {}
    srcs = [x for x in (a.get("sources") or []) if isinstance(x, dict)]
    usable = [x for x in srcs if (x.get("tables") or x.get("text_chars"))]
    stmts = {int(s.get("n")): s for s in ((turn.get("compute") or {}).get("statements") or []) if isinstance(s, dict) and s.get("n") is not None}
    quoted = (turn.get("compute_lineage") or {}).get("quoted") or []
    from_att = sum(1 for q in quoted if isinstance(q, dict) and (stmts.get(q.get("statement")) or {}).get("source"))
    return {"attachments_sourced": len(usable) if srcs else None,
            "attachments_verified": sum(1 for x in usable if x.get("verified")) if srcs else None,
            "figures_from_attachment": from_att if srcs else None}


ATTACHMENT_QUESTION = ("What was the revenue of APAC in the attached table, and which region had the most orders?",
                       os.path.join(os.path.dirname(__file__), "fixtures", "s9_table.png"))


def _regenerate(jwt, sid, turn_no):
    """S8 — run the question again as a scored sibling through the REST endpoint; returns the
    task's final payload (None when the endpoint refuses — the switch off, etc.)."""
    h = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    r = requests.post(f"{BASE}/api/v1/sessions/{sid}/turns/{turn_no}/regenerate", headers=h, json={}, timeout=30)
    if r.status_code != 202: return None
    tid = r.json().get("task_id"); t0 = time.time()
    while time.time() - t0 < 240:
        j = requests.get(f"{BASE}/api/v1/tasks/{tid}", headers=h, timeout=30).json()
        if j.get("status") in ("complete", "failed", "error"): return j
        time.sleep(2)
    return None


def _lineage_cols(user_uuid, sid, root_turn, regen):
    """S8 — what the record says about the question's answers: how many siblings, whether every
    one is SCORED (the reason to prefer one), whether the model's context holds exactly ONE
    answer per question, and the regenerate's own cost."""
    files = glob.glob(f"tda_sessions/{user_uuid}/{sid}.json") + glob.glob(f"tda_sessions/{user_uuid}/{sid}/conversation.json")
    if not files: return {}
    d = json.load(open(files[0])); wh = d.get("last_turn_data", {}).get("workflow_history") or []
    sibs = [t for t in wh if t.get("turn") == root_turn or t.get("sibling_of") == root_turn]
    co = [m for m in (d.get("chat_object") or []) if m.get("isValid", True) is not False and (m.get("turn_number") or 0) > 0]
    users = sum(1 for m in co if m.get("role") == "user"); answers = len(co) - users
    return {"siblings": len(sibs), "context_one_answer": 1 if (users and answers == users) else 0,
            # the sibling's own cost, read from its record (the task payload carries no cost)
            "regen_cost": next((t.get("turn_cost") for t in sibs if t.get("sibling_of") == root_turn), None) if regen else None,
            "adopted_is_newest": 1 if (sibs and max(sibs, key=lambda t: t["turn"]).get("adopted")) else (0 if sibs and len(sibs) > 1 else None)}


def run(label, classes, profiles, user_uuid, jwt, stop_probe=True, chain=False, regenerate=False, attachments=False, drop=False, steer=""):
    out = {"label": label, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "chain": chain, "regenerate": regenerate, "attachments": attachments, "drop": drop, "steer": steer, "classes": {}}
    h = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    for cls in classes:
        pid = resolve_profile(jwt, profiles[cls]); rows = []
        chain_sid = requests.post(f"{BASE}/api/v1/sessions", headers=h, json={}, timeout=30).json()["session_id"] if chain else None
        qs = [(q, None) for q in QUESTIONS[cls]]
        if attachments and os.path.exists(ATTACHMENT_QUESTION[1]):
            qs.append(ATTACHMENT_QUESTION)   # S9 — one question over an uploaded table image
        for qi, (q, att) in enumerate(qs):
            # S10 — the FIRST question per class drops its stream mid-turn (opt-in), sends a steer
            # over REST, and reattaches from the cursor; the columns say whether the tail was served
            # from the cursor and whether the steer was considered by the running turn
            m = stream_turn(jwt, pid, q, session_id=chain_sid, attachments=[att] if att else None,
                            drop_after=(6 if (drop and qi == 0) else None), steer=(steer or None) if (drop and qi == 0) else None)
            turn = read_turn(user_uuid, m["session_id"])
            # S8 — every question regenerated once as a scored sibling (opt-in): the columns say
            # whether both answers carry verdicts and whether the context keeps one answer
            regen = _regenerate(jwt, m["session_id"], turn.get("turn") or 1) if regenerate else None
            lcols = _lineage_cols(user_uuid, m["session_id"], turn.get("turn") or 1, regen) if regenerate else {}
            if regenerate:
                try:
                    sib_turn = ((regen or {}).get("result") or {}).get("turn_id")
                    hh = {"Authorization": f"Bearer {jwt}"}
                    s1 = requests.get(f"{BASE}/api/v1/evals/turn-score?session_id={m['session_id']}&turn={turn.get('turn') or 1}", headers=hh, timeout=30).json()
                    s2 = requests.get(f"{BASE}/api/v1/evals/turn-score?session_id={m['session_id']}&turn={sib_turn}", headers=hh, timeout=30).json() if sib_turn else {}
                    lcols["siblings_scored"] = 1 if (s1.get("scored") and s2.get("scored")) else 0
                except Exception:
                    lcols["siblings_scored"] = None
            cache = turn.get("cache") or {}
            lin = turn.get("compute_lineage") or {}
            acols = _attachment_cols(turn)
            _st = turn.get("steers") or {}
            m.update({"dropped": 1 if m.get("dropped") else 0, "resumed": 1 if m.get("resumed") else 0,
                      "frames_after_drop": m.get("frames_after_drop") or 0, "steer_sent": 1 if m.get("steer_sent") else 0,
                      "steer_considered": (_st.get("considered") if _st else None),
                      "question": q, "in_tokens": turn.get("turn_input_tokens") or 0, "out_tokens": turn.get("turn_output_tokens") or 0,
                      "cost": turn.get("turn_cost") or 0, "status": turn.get("status"),
                      # S2 — the turn's cache stamp: what the provider REPORTED as served from cache
                      "cached_tokens": cache.get("cached_tokens") or 0, "cached_share": cache.get("cached_share") or 0.0,
                      "cache_misses_found": cache.get("misses_found") or 0, "cache_capability": cache.get("capability"),
                      # S3 — number lineage: receipts sealed, figures traced / unreceipted, and the
                      # numeric-fidelity measurement re-scored from the persisted turn
                      "compute_receipts": len(((turn.get("compute") or {}).get("receipts")) or []),
                      "figures": lin.get("figures") or 0, "figures_receipted": len(lin.get("receipted") or []),
                      "figures_quoted": len(lin.get("quoted") or []), "figures_unreceipted": len(lin.get("unreceipted") or []),
                      "numeric_fidelity": _numeric_fidelity(turn),
                      # S4 — the resilience envelope: did the turn ANSWER, what was recovered, who served
                      # an answer is a SUCCESSFUL turn — a canned "I encountered an error" text is not one
                      "answered": 1 if turn.get("status") == "success" else 0,
                      "recovery_steps": len(((turn.get("resilience") or {}).get("steps")) or []),
                      "served_by_fallback": 1 if (turn.get("resilience") or {}).get("fallback") else 0,
                      "retries": ((turn.get("resilience") or {}).get("counts") or {}).get("retry", 0),
                      # S5 — the visible budget: armed, exhausted, graceful vs killed, and whether
                      # the answer SAYS what is left (a budget-stopped answer that names its
                      # remainder is the whole point; a kill never does)
                      "budget_armed": 1 if (turn.get("budget") or {}).get("units") else 0,
                      "budget_exhausted": 1 if (turn.get("budget") or {}).get("exhausted") else 0,
                      "graceful": 1 if (turn.get("budget") or {}).get("graceful") else 0,
                      "killed": 1 if ((turn.get("budget") or {}).get("killed") or turn.get("budget_stopped") or turn.get("token_budget_stopped")) else 0,
                      # S6 — the governed tool surface: offered / held / found, the catalog total
                      "tools_offered": ((turn.get("tool_surface") or {}).get("counts") or {}).get("offered"),
                      "tools_held": ((turn.get("tool_surface") or {}).get("counts") or {}).get("held"),
                      "tools_found": ((turn.get("tool_surface") or {}).get("counts") or {}).get("widened"),
                      "tools_total": ((turn.get("tool_surface") or {}).get("counts") or {}).get("total"),
                      # S7 — the outcome loop: was the DELIVERED answer flagged by the grounding gate
                      # (annotate/block on what the reader got), did a revision run, did it clear,
                      # and what the revision itself cost — the loop's metric is the first one
                      "delivered_flagged": 1 if ((turn.get("grounding") or {}).get("decision") in ("annotate", "block")) else 0,
                      "revised": 1 if ((turn.get("outcome") or {}).get("revisions") or 0) else 0,
                      "revision_cleared": (1 if (turn.get("outcome") or {}).get("cleared") else 0) if (turn.get("outcome") or {}).get("revisions") else None,
                      "revision_cost": (turn.get("outcome") or {}).get("cost_usd") if (turn.get("outcome") or {}).get("revisions") else None,
                      # S8 — regenerate as an experiment (only with --regenerate)
                      "siblings": lcols.get("siblings"), "siblings_scored": lcols.get("siblings_scored"),
                      "context_one_answer": lcols.get("context_one_answer"), "adopted_is_newest": lcols.get("adopted_is_newest"),
                      "regen_cost": lcols.get("regen_cost"),
                      # S9 — grounded multimodal (only on the attachment question)
                      "attachments_sourced": acols.get("attachments_sourced"), "attachments_verified": acols.get("attachments_verified"),
                      "figures_from_attachment": acols.get("figures_from_attachment"),
                      "says_what_is_left": 1 if ((turn.get("budget") or {}).get("exhausted") and re.search(r"(unable to|could not|remain|left undone|not (?:yet )?(?:retrieved|completed|performed))", turn.get("final_summary_text") or turn.get("final_summary") or "", re.I)) else 0})
            rows.append(m); print(f"  [{cls}] ttft={m['t_first_delta']} final={m['t_final']} deltas={m['deltas']} steps={m['llm_steps']} tools={m['tool_calls']} cost=${m['cost']:.5f} cached={m['cached_share']:.0%} receipts={m['compute_receipts']} unreceipted={m['figures_unreceipted']} fidelity={m['numeric_fidelity']} answered={m['answered']} recoveries={m['recovery_steps']} fallback={m['served_by_fallback']} budget={m['budget_armed']}/{m['budget_exhausted']} graceful={m['graceful']} killed={m['killed']} says_left={m['says_what_is_left']} flagged={m['delivered_flagged']} revised={m['revised']}/{m['revision_cleared']} att={m['attachments_sourced']}/{m['attachments_verified']} figs_from_att={m['figures_from_attachment']} drop={m['dropped']}/{m['resumed']} tail={m['frames_after_drop']} steer={m['steer_sent']}/{m['steer_considered']}")
        probe = None
        if stop_probe:
            p = stream_turn(jwt, pid, STOP_QUESTION, stop_after_first_delta=True); turn = read_turn(user_uuid, p["session_id"])
            probe = {"stopped": p["stopped"], "final_partial": p["final_partial"], "persisted_partial": bool(turn.get("partial_answer")),
                     "persisted_status": turn.get("status"), "kept_chars": len(turn.get("final_summary_text") or "")}
            print(f"  [{cls}] stop probe: partial delivered={probe['final_partial']} persisted={probe['persisted_partial']} chars={probe['kept_chars']}")
        out["classes"][cls] = {"profile": profiles[cls], "rows": rows, "stop_probe": probe}
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"conversation_lane_{label}.json"); json.dump(out, open(path, "w"), indent=1); print("wrote", path)
    return out


def _agg(rows, k):
    vals = [r[k] for r in rows if r.get(k) is not None]
    return round(st.mean(vals), 3) if vals else None


def compare(a, b):
    A = json.load(open(os.path.join(RESULTS_DIR, f"conversation_lane_{a}.json"))); B = json.load(open(os.path.join(RESULTS_DIR, f"conversation_lane_{b}.json")))
    print(f"{'class':10} {'metric':18} {a:>12} {b:>12}")
    for cls in B["classes"]:
        ra = (A["classes"].get(cls) or {}).get("rows", []); rb = B["classes"][cls]["rows"]
        for k in ("t_first_delta", "t_final", "t_verified", "deltas", "llm_steps", "tool_calls", "refusals", "rewrites", "in_tokens", "out_tokens", "cost", "cached_tokens", "cached_share", "cache_misses_found",
                  "compute_receipts", "figures", "figures_receipted", "figures_quoted", "figures_unreceipted", "numeric_fidelity",
                  "answered", "recovery_steps", "retries", "served_by_fallback",
                  "budget_armed", "budget_exhausted", "graceful", "killed", "says_what_is_left",
                  "tools_offered", "tools_held", "tools_found", "tools_total",
                  "delivered_flagged", "revised", "revision_cleared", "revision_cost",
                  "siblings", "siblings_scored", "context_one_answer", "adopted_is_newest", "regen_cost",
                  "attachments_sourced", "attachments_verified", "figures_from_attachment",
                  "dropped", "resumed", "frames_after_drop", "steer_sent", "steer_considered"):
            print(f"{cls:10} {k:18} {str(_agg(ra, k)):>12} {str(_agg(rb, k)):>12}")
        pa = (A["classes"].get(cls) or {}).get("stop_probe") or {}; pb = B["classes"][cls].get("stop_probe") or {}
        print(f"{cls:10} {'partial kept':18} {str(pa.get('persisted_partial')):>12} {str(pb.get('persisted_partial')):>12}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--label"); ap.add_argument("--compare", nargs=2)
    ap.add_argument("--classes", default=",".join(CLASS_PROFILES)); ap.add_argument("--profiles", default="")
    ap.add_argument("--no-stop-probe", action="store_true")
    ap.add_argument("--chain", action="store_true", help="run each class's questions in ONE session (the prompt cache's case)")
    ap.add_argument("--regenerate", action="store_true", help="S8: regenerate every question once as a scored sibling and read the lineage columns")
    ap.add_argument("--attachments", action="store_true", help="S9: add one question per class over an uploaded table image and read the attachment columns")
    ap.add_argument("--drop", action="store_true", help="S10: drop the first question's stream mid-turn, then reattach from the cursor")
    ap.add_argument("--steer", default="", help="S10: with --drop, send this steer over REST before reattaching"); a = ap.parse_args()
    if a.compare: return compare(*a.compare)
    if not a.label: ap.error("--label or --compare")
    profiles = dict(CLASS_PROFILES)
    for kv in filter(None, a.profiles.split(",")):
        k, v = kv.split("="); profiles[k] = v
    jwt, user = login(); uid = user.get("id") or user.get("user_id") or user.get("uuid")
    if not uid:
        r = requests.get(f"{BASE}/api/v1/auth/me", headers={"Authorization": f"Bearer {jwt}"}, timeout=30); uid = r.json().get("id") or (r.json().get("user") or {}).get("id")
    run(a.label, [c.strip() for c in a.classes.split(",") if c.strip()], profiles, uid, jwt, stop_probe=not a.no_stop_probe, chain=a.chain, regenerate=a.regenerate, attachments=a.attachments, drop=a.drop, steer=a.steer)


if __name__ == "__main__":
    main()
