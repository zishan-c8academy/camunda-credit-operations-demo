# How it works

## Processes

| Process | Starts from | What it does |
|---|---|---|
| `CB_CreditDispute` | The workbench (REST API) | Verifies identity, builds the statutory clock (public holiday REST call plus `CB_DisputeSlaPolicy`), sends the dispute to the furnisher and waits for its answer as a message, runs the investigator agent, decides who signs off (`CB_DisputeApprovalAuthority`), then updates the credit file and notifies. Timers on the reinvestigation warn at 75% of the clock and escalate at the deadline. Event sub-processes handle a withdrawal and a service incident. |
| `CB_IncidentManagement` | Webhook `cb-monitoring-alert` | Looks up the service, runs the triage agent, sets severity (`CB_IncidentSeverity`), flags open disputes at risk, pages responders, escalates level by level until someone acknowledges, then resolves. |
| `CB_RuleImprovement` | The workbench, or every Monday 06:00 | The improvement agent reads the change policy, override statistics and a backtest, proposes a threshold change, a policy check runs, the rule owner approves, and a new version of the authority table is deployed. |
| `CB_RecognitionJourney` | Message from the other processes | Scores each achievement (`CB_PointsScoring`), applies the daily cap, updates the ledger, checks reward eligibility (`CB_RewardEligibility`) and asks a manager to approve rewards. |

## The three agents

Each agent is an **AI Agent sub-process (v2)**: an ad-hoc sub-process whose inner tasks are the agent's tools. The
agent can only call what the model gives it.

| Agent | Tools |
|---|---|
| Dispute investigator | credit file tradeline, furnisher response, consumer evidence, dispute history, identity and fraud signals, dispute policy, evidence score (decision table), ask a dispute specialist (user task) |
| Incident triage | service health, recent changes, similar incidents, runbook, ask the on-call engineer (user task) |
| Improvement analyst | change policy, override statistics, backtest |

Settings that matter:
- **Provider:** OpenAI-compatible. Endpoint and model from `CB_GENAI`, key from the connector secret `CB_LLM_API_KEY`.
- **System prompt:** from the prompt registry `CB_PROMPTS` (id, version, owner, text). Each case stores the prompt
  version it ran with (`obsPromptVersion`).
- **Output:** JSON only. The process parses it and copies the fields it needs.
- **Limits:** a cap on model calls per run, a 90-second timeout per call and a 4,096-token answer limit.
- **Guardrail:** if the agent hits its call limit, returns unparseable output or trips a model guardrail, an error
  boundary ("Agent off rail") catches it and the case goes to a senior analyst (or, for incidents, is treated as high
  impact) without an AI recommendation.
- **Routing stays in DMN.** The evidence score comes from `CB_EvidenceStrength` and the agent must pass it on. Who
  signs off comes from `CB_DisputeApprovalAuthority` after the agent: fraud (A-01), vulnerable consumers (A-02) and
  adverse outcomes (A-05) always go to a person.

## Decision tables

| Table | Decides |
|---|---|
| `CB_DisputeSlaPolicy` | Statutory clock by jurisdiction and dispute type: regulation, days, business or calendar days, furnisher window |
| `CB_EvidenceStrength` | Evidence score and band from the furnisher's position, the consumer's evidence and fraud signals |
| `CB_DisputeApprovalAuthority` | Who signs off: auto-apply, analyst, senior analyst or fraud and compliance, with priority. The learn loop changes its auto-apply threshold (rule A-06) only |
| `CB_IncidentSeverity` | Priority, major incident or not, acknowledge and resolve SLAs, escalation chain |
| `CB_PointsScoring` | Points per achievement event |
| `CB_RewardEligibility` | Badge, reward and whether a manager must approve |

## Job workers (in `app/server.py`)

The workbench runs one long-polling worker per job type against `POST /v2/jobs/activation`. They stand in for
bureau systems; replace them with your own services to integrate for real.

| Job type | Stands in for |
|---|---|
| `cb.identity.verify` | Identity resolution |
| `cb.furnisher.send` | Sending the dispute to the furnisher (e-OSCAR in the US). Publishes the furnisher's answer back as message `cb-furnisher-response` a few seconds later |
| `cb.creditfile.update` | Writing the correction to the credit file. Fails with a lock for the "system locked" scenario |
| `cb.notify` | Letters and notifications to consumer and furnisher, pages for incidents |
| `cb.sla.alert`, `cb.review.escalate`, `cb.incident.escalate` | Team lead alerts, review reassignment, incident escalation |
| `cb.disputes.flag` | Publishes `cb-dispute-service-incident` to every open dispute on the affected service |
| `cb.learn.stats`, `cb.learn.backtest`, `cb.learn.deploy` | Override statistics, backtest, deploying the new authority table version |
| `cb.achievement.publish`, `cb.points.read`, `cb.points.update`, `cb.points.award` | Recognition events and the points ledger |

The mock systems keep their state in `app/cb.db` (SQLite). Data the agents' tools read comes from the cluster variable
`CB_SYSTEMS`, so the tools run inside Camunda as script tasks with no worker.

## Connectors used

- **AI Agent sub-process** for the three agents.
- **REST** (`io.camunda:http-json:1`) to `https://date.nager.at` for public holidays. If it fails, a boundary event
  falls back to counting weekends only.
- **Webhook** (inbound) at `https://<region>.connectors.camunda.io/<cluster-id>/inbound/cb-monitoring-alert`. No
  authentication in the demo. To send an alert yourself:

```
curl -X POST "https://<region>.connectors.camunda.io/<cluster-id>/inbound/cb-monitoring-alert" \
  -H "Content-Type: application/json" \
  -d '{"incidentId":"INC-26-9001","alertId":"ALR-77120","service":"dispute-intake-api","source":"Observability platform","title":"Dispute Intake API p95 latency 4.8 s (SLO 1.5 s), error rate 7.2%"}'
```

## Cluster variables

| Variable | Holds |
|---|---|
| `CB_GENAI` | LLM endpoint and model |
| `CB_PROMPTS` | The three system prompts with id, version and owner |
| `CB_SYSTEMS` | Mock bureau data the agents' tools read, keyed by dispute or incident |
| `CB_CLOCK` | Demo time: one statutory day = 30 s, one working hour = 8 s, one minute = 4 s |
| `CB_POLICY` | What the learn loop may change, its limits, and the current threshold |
| `CB_RECOGNITION` | Recognition programme settings (daily cap) |

`scripts/setup_cluster.py` writes them from `data/`. Edit the JSON there and rerun it. For a real deployment, remove
`CB_CLOCK` from the timers so they use real time.

## Documents

Each new dispute uploads its evidence PDFs (`data/documents/`, fictional) to Camunda's document store with
`POST /v2/documents` and keeps the references in the variable `evidenceDocuments`. The workbench fetches the bytes with
`GET /v2/documents/{id}` when you open one. The review form also has a document preview component, so the files should show in
Tasklist too. We have not checked that view.

## What the workbench calls

All calls go from `app/server.py` to the Orchestration Cluster REST API (`/v2`) with OAuth client credentials:

| Purpose | Endpoints |
|---|---|
| Start cases | `POST /process-instances` |
| Read cases | `POST /process-instances/search`, `/element-instances/search`, `/variables/search`, `/incidents/search`, `/decision-instances/search` |
| Tasks | `POST /user-tasks/search`, `/user-tasks/{key}/assignment`, `/user-tasks/{key}/completion`, `PATCH /user-tasks/{key}` |
| Events | `POST /messages/publication` |
| Workers | `POST /jobs/activation`, `/jobs/{key}/completion`, `/jobs/{key}/failure` |
| Incidents | `PATCH /jobs/{key}` (retries), `POST /incidents/{key}/resolution` |
| Documents | `POST /documents`, `GET /documents/{id}` |
| AI governance | `POST /agent-instances/search`, `GET` and `PUT /cluster-variables/global/{name}` |
| Learn loop | `POST /deployments` (new authority table version) |

The browser never sees a token. Swap the UI for your own, or use Tasklist and Operate as they are: the process does
not depend on this workbench, only on something completing its user tasks and running its job workers.
