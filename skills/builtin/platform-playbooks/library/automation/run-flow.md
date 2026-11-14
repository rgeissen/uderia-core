# Run a flow

Run one of the user's flows now, follow the run, and report what it produced — or stop it.

**Use for:** "run the quarterly answer flow", "execute my report flow with region EMEA", "start the pipeline", "stop the running flow", "cancel that run".

## Steps

1. call `flows.list` with `fields: ["flow_id", "name", "description"]` and agree which flow. A flow with open slots cannot run — say which slots are open.
2. call `flows.run` with `flow_id` and, when the flow takes inputs, `input_params` (e.g. `{"region": "EMEA"}`). It is **submitted for approval** unless the agent may run changes itself; a run spends tokens and may write files or act through its nodes.
3. Once it runs: call `flows.run_status` with the `run_id` it returned and read `status` (`queued`, `running`, `complete`, `error`, `cancelled`) and `result`. A long run: tell the user it is running and where to watch it (Flows) rather than waiting.
4. To stop it: call `flows.run_cancel` with `run_id`.

## Reading the result

- `flow_slots_unfilled`: nothing ran — the named slots need an agent first.
- `status: error`: read the failing node in `node_runs` and name it.
- `cost_usd` and the token counts are the run's spend; say them when the user asked about cost.

## What to tell the user

What ran and what came out, briefly: *"'Quarterly answer' finished in 48 s for $0.02. @COORD's combined answer: …"* — or that it waits for approval in the Assurance Inbox.

## Do not

- Run a flow twice for one request.
- Run a flow the user did not name.
