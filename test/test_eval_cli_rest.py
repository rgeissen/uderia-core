"""WS3 W3.3b — CLI exit codes + REST eval surface wiring.

Verifies the CLI gate returns CI-usable exit codes, the eval REST endpoints are
defined, and the REST executor imports. CI-pure (no server).

Run:
    PYTHONPATH=src python test/test_eval_cli_rest.py
"""
import os
import sys
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import EvalCase  # noqa: E402
from trusted_data_agent.eval.store import EvalStore, reset_eval_store  # noqa: E402
from trusted_data_agent.eval.runner import run_dataset  # noqa: E402
from trusted_data_agent.eval import cli  # noqa: E402

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _good():
    return {"profile_type": "tool_enabled", "status": "success", "isValid": True,
            "final_summary_text": "ok",
            "execution_trace": [{"action": {"tool_name": "base_readQuery", "arguments": {}},
                                 "result": {"tool_output_summary": {"status": "success"}}}],
            "provenance_meta": {"sealed": True}, "turn_cost": 0.01}


def _bad():
    d = _good()
    d["execution_trace"] = [{"action": {"tool_name": "base_writeQuery", "arguments": {}},
                             "result": {"tool_output_summary": {"status": "success"}}}]
    return d


def t_cli_gate_exit_codes():
    store = EvalStore(Path(tempfile.mkdtemp(prefix="tda_cli_")) / "e.db")
    reset_eval_store(store)  # CLI's get_eval_store() now returns this
    store.create_dataset("core", "Core")
    store.add_case(EvalCase(id="c1", query="r", profile_class="tool_enabled", cohort="risky",
                            dataset_id="core",
                            assertions={"require_tools_subset": ["base_readQuery"],
                                        "must_not_call": ["base_writeQuery"]}))

    async def good_exec(case):
        return _good()

    async def bad_exec(case):
        return _bad()

    _loop.run_until_complete(run_dataset(store, "core", good_exec, k=1, run_id="good"))
    _loop.run_until_complete(run_dataset(store, "core", bad_exec, k=1, run_id="bad"))

    rc_pass = cli.main(["gate", "--run", "good", "--min-pass-k", "0.9"])
    check("CLI gate: passing run -> exit 0", rc_pass == 0)
    rc_fail = cli.main(["gate", "--run", "bad", "--cohort", "risky=0.9"])
    check("CLI gate: failing cohort -> exit 1", rc_fail == 1)
    # report + list run without error
    check("CLI report -> exit 0", cli.main(["report", "--run", "good"]) == 0)
    check("CLI list -> exit 0", cli.main(["list"]) == 0)
    reset_eval_store(None)


def t_cli_parser():
    p = cli.build_parser()
    args = p.parse_args(["run", "--dataset", "core", "--k", "3", "--base-url", "http://x"])
    check("CLI parser: run args", args.dataset == "core" and args.k == 3 and args.base_url == "http://x")
    args2 = p.parse_args(["gate", "--run", "r", "--cohort", "a=0.8", "--cohort", "b=0.9"])
    check("CLI parser: repeatable --cohort", args2.cohort == ["a=0.8", "b=0.9"])
    check("CLI cohort parse helper", cli._parse_cohorts(["a=0.8", "b=0.9"]) == {"a": 0.8, "b": 0.9})


def t_rest_surface():
    import trusted_data_agent.api.rest_routes as rr
    for fn in ("evals_datasets", "evals_cases", "evals_mine", "evals_get_run",
               "evals_run_scores", "evals_gate", "evals_score_turn", "evals_turn_score",
               "evals_online_spans", "evals_judge_calibration", "evals_judge_queue"):
        check(f"REST eval endpoint defined: {fn}", hasattr(rr, fn))
    from trusted_data_agent.eval.rest_executor import RestEvalExecutor
    ex = RestEvalExecutor(base_url="http://localhost:5050/")
    check("RestEvalExecutor strips trailing slash", ex.base_url == "http://localhost:5050")


# ── REST executor: the HTTP flow itself ───────────────────────────────────────
# The old test only asserted the class IMPORTS, which is why a `_last_turn` that
# hit a route the server does not serve (`GET /api/v1/sessions/<id>` → 404 HTML)
# shipped: every offline run recorded `executor failed: Expecting value: line 1
# column 1` and scored 0% regardless of how the agent actually performed. These
# drive the real `__call__` against a stub transport.

class _Resp:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _StubClient:
    """Records every request; serves the routes the REAL server serves."""

    def __init__(self, session_read=None):
        self.requests = []
        self.bodies = []
        self._session_read = session_read

    async def post(self, url, headers=None, json=None):
        self.requests.append(("POST", url))
        self.bodies.append(json)
        if url.endswith("/auth/login"):
            return _Resp(200, {"token": "t"})
        if url.endswith("/api/v1/sessions"):
            return _Resp(201, {"session_id": "sid-1"})
        if url.endswith("/query"):
            return _Resp(202, {"task_id": "task-1"})
        return _Resp(404, None, "<html>404</html>")

    async def get(self, url, headers=None):
        self.requests.append(("GET", url))
        if url.endswith("/api/v1/profiles"):
            return _Resp(200, {"profiles": [{"tag": "IDEAT", "id": "profile-ideat"}]})
        if "/tasks/" in url:
            return _Resp(200, {"status": "complete"})
        if url.startswith("/session/"):
            return self._session_read if self._session_read else _Resp(
                200, {"workflow_history": [{"status": "success",
                                            "final_summary_text": "ping",
                                            "user_query": "Reply with exactly the word: ping"}]})
        # Anything else is a route the server does not serve.
        return _Resp(404, None, "<!doctype html>404")

    async def aclose(self):
        return None


def _exec_with(stub):
    from trusted_data_agent.eval.rest_executor import RestEvalExecutor

    class _Ex(RestEvalExecutor):
        async def _client(self):
            return stub
    ex = _Ex(base_url="http://x")
    ex.poll_timeout = 0.1
    return ex


def t_rest_executor_flow():
    stub = _StubClient()
    ex = _exec_with(stub)
    case = EvalCase(id="c", query="Reply with exactly the word: ping",
                    profile_class="llm_only", profile_tag="IDEAT", dataset_id="d")
    turn = _loop.run_until_complete(ex(case))

    check("executor returns the persisted turn (not an error dict)",
          turn.get("status") == "success" and turn.get("final_summary_text") == "ping")

    body = next(b for b in stub.bodies if b and "prompt" in b)
    check("prompt is the BARE question — no @TAG pollution",
          body["prompt"] == "Reply with exactly the word: ping")
    check("profile is selected by profile_id in the body",
          body.get("profile_id") == "profile-ideat")

    gets = [u for m, u in stub.requests if m == "GET"]
    check("reads the turn from GET /session/<id>",
          any(u.startswith("/session/sid-1") for u in gets))
    check("never calls the non-existent GET /api/v1/sessions/<id>",
          not any(u.startswith("/api/v1/sessions/") for u in gets))


def t_rest_executor_session_read_failure_is_named():
    # A 404 (the shipped bug) must degrade to a NAMED error, never a bare
    # JSONDecodeError surfacing as "executor failed: Expecting value...".
    stub = _StubClient(session_read=_Resp(404, None, "<!doctype html>404"))
    ex = _exec_with(stub)
    case = EvalCase(id="c", query="q", profile_class="llm_only",
                    profile_tag="IDEAT", dataset_id="d")
    turn = _loop.run_until_complete(ex(case))
    check("404 on the session read names itself",
          turn.get("status") == "error" and "HTTP 404" in str(turn.get("error_message")))

    stub2 = _StubClient(session_read=_Resp(200, None, "not json"))
    turn2 = _loop.run_until_complete(_exec_with(stub2)(case))
    check("non-JSON session read names itself",
          turn2.get("status") == "error" and "non-JSON" in str(turn2.get("error_message")))


def t_rest_executor_refuses_unresolvable_tag():
    # Running the case against whatever the DEFAULT profile is would score the
    # wrong agent — worse than a failed case.
    stub = _StubClient()

    async def _no_profiles(url, headers=None):
        stub.requests.append(("GET", url))
        if url.endswith("/api/v1/profiles"):
            return _Resp(200, {"profiles": []})
        if "/tasks/" in url:
            return _Resp(200, {"status": "complete"})
        return _Resp(404, None, "404")
    stub.get = _no_profiles
    ex = _exec_with(stub)
    case = EvalCase(id="c", query="q", profile_class="llm_only",
                    profile_tag="GHOST", dataset_id="d")
    raised = False
    try:
        _loop.run_until_complete(ex(case))
    except RuntimeError as e:
        raised = "GHOST" in str(e)
    check("unresolvable profile tag refuses rather than using the default", raised)


# ── annotation store: the κ pair round-trip + the self-applying migration ─────
def t_annotation_store_pairs():
    store = EvalStore(Path(tempfile.mkdtemp(prefix="tda_ann_")) / "e.db")
    # a queue ruling freezes the pair; a manual form entry stays a note
    store.add_annotation(reviewer="sme", label="agree", dimension="groundedness",
                         judge_passed=True, session_id="s1", turn=3)
    store.add_annotation(reviewer="sme", label="disagree", dimension="groundedness",
                         judge_passed=False, session_id="s1", turn=4)
    store.add_annotation(reviewer="sme", label="agree", note="manual entry, no ruling")
    anns = store.list_annotations()
    check("store: 3 annotations round-trip", len(anns) == 3)
    paired = [a for a in anns if a.get("dimension")]
    check("store: pair rows carry dimension + frozen ruling",
          len(paired) == 2 and {a["judge_passed"] for a in paired} == {0, 1})
    note = next(a for a in anns if not a.get("dimension"))
    check("store: manual entry keeps NULL ruling", note["judge_passed"] is None)
    # ...and the calibration layer reads the stored rows directly
    from trusted_data_agent.eval.calibration import pairs_from_annotations
    pairs = pairs_from_annotations(anns)
    check("store -> κ pairs: (T,T) and (T,F), note excluded",
          sorted(pairs.get("groundedness", [])) == [(True, False), (True, True)])


def t_annotation_schema_migration():
    # A store created BEFORE the calibration columns existed must gain them on
    # open, with pre-migration rows surviving as pair-less notes.
    import sqlite3 as _sq
    db = Path(tempfile.mkdtemp(prefix="tda_mig_")) / "e.db"
    c = _sq.connect(db)
    c.execute("""CREATE TABLE eval_annotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, session_id TEXT,
        turn INTEGER, reviewer TEXT, label TEXT, note TEXT, created_at REAL)""")
    c.execute("INSERT INTO eval_annotations (reviewer, label, created_at) VALUES ('old','agree',1)")
    c.commit(); c.close()
    store = EvalStore(db)
    anns = store.list_annotations()
    check("migration: legacy row survives with NULL pair fields",
          len(anns) == 1 and anns[0]["judge_passed"] is None and anns[0]["dimension"] is None)
    store.add_annotation(reviewer="sme", label="agree", dimension="content_safety",
                         judge_passed=True)
    check("migration: post-migration writes carry the pair",
          any(a.get("dimension") == "content_safety" for a in store.list_annotations()))


def main():
    print("== WS3 W3.3b CLI + REST surface ==")
    for t in (t_cli_parser, t_cli_gate_exit_codes, t_rest_surface,
              t_rest_executor_flow, t_rest_executor_session_read_failure_is_named,
              t_rest_executor_refuses_unresolvable_tag,
              t_annotation_store_pairs, t_annotation_schema_migration):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
