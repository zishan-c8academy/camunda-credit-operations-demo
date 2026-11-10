# Credit operations on Camunda 8.10: agents, people and rules in one process

Operations at a fictional consumer credit bureau, built on Camunda 8.10 SaaS and ready to run on your own cluster:
four processes, six decision tables, eight forms, three AI agents, a workbench UI and the job workers behind it.

- **Credit disputes.** A consumer disputes a line on their credit file. A statutory clock starts from a decision table
  per jurisdiction (US, South Africa, India, UK). An AI agent investigates and recommends. An approval authority table
  decides whether a person signs off. Analysts approve or override, with SLA escalation if nobody acts.
- **Production incidents.** A monitoring alert arrives by webhook. A triage agent reads health, recent changes, similar
  incidents and the runbook. A severity table sets the priority, and open disputes at risk are flagged.
- **A learn loop.** An improvement agent reads how analysts decided, backtests a threshold change and proposes it. A rule
  owner approves, and a new decision version goes live.
- **Recognition.** Points, badges and rewards from decision tables, with a manager approving the bigger rewards.

All data is synthetic. The bureau, consumers, furnishers, documents and systems are fictional.

**Watch it first:** a [12 minute walkthrough](https://view-su3.highspot.com/viewer/447cd83c044511fb16bea019b516a43f) of the
whole demo, in five chapters: a dispute end to end, every route and exception, a production incident, rules that learn,
and governance.

## What you need

| | |
|---|---|
| Camunda | A Camunda 8 SaaS organization with a cluster on **8.10 or later**. A trial account works. |
| An LLM | An API key for an **OpenAI-compatible** chat completions endpoint: OpenAI, OpenRouter or your own AI gateway. |
| Your machine | **Python 3.9 or later**. No packages to install. The workbench runs on your laptop and calls the cluster over HTTPS. |

Setup takes about 15 minutes.

## Set it up

**1. Create a cluster.** In Camunda Hub, go to **Environments > Clusters**, create a cluster and pick version 8.10 or
later. Wait until it shows **Healthy**.

**2. Create an API client.** Open the cluster, go to the **API** tab, click **Create new client**, tick the
**Orchestration Cluster API** scope and create it. Copy the **Env Vars** block it shows; the secret is only shown once.

```
cp .env.example .env
```

Paste the Env Vars block into `.env`, replacing the empty values in section 1. The `export` prefixes and quotes can
stay as they are.

**3. Store your LLM key as a connector secret.** On the same cluster, open the **Cluster secrets** tab (called
**Connector secrets** in some versions), click **Create new secret**, then:
- Key: `CB_LLM_API_KEY`
- Value: your LLM API key

The agents read the key from this secret at run time, so it never sits in a model or a file. A new secret can take a
few minutes to reach the connectors.

**4. Tell the agents which endpoint and model to call.** In `.env`, fill in section 2:

```
CB_LLM_ENDPOINT=https://openrouter.ai/api/v1
CB_LLM_MODEL=anthropic/claude-sonnet-5
```

That pair is the one we tested. Any OpenAI-compatible endpoint works the same way: use your gateway's base URL (ending
before `/chat/completions`) and a model id it serves. See [Choosing a model](#choosing-a-model) before you switch.

**5. Set up the cluster.**

```
python3 scripts/setup.py
```

It checks `.env`, connects, creates six cluster variables and deploys every model. Run it again whenever you change
`.env` or a model; it is safe to repeat.

**6. Start the workbench.**

```
python3 app/server.py
```

Open http://127.0.0.1:5690. Leave this running: it serves the UI and runs the 15 job workers that stand in for
bureau systems. Cases stop at a service task while it is not running.

**7. Put work in the queues (optional, about 3 minutes).** In a second terminal:

```
python3 scripts/stage_demo.py
```

This starts six cases that land in different places, so every persona has something to open. The last line says
`Ready`. Every staged case should say `OK`. A case that says `WAIT` is usually just slow: right after a deploy, the
connectors sometimes take a few minutes to pick up work (see [When something goes wrong](#when-something-goes-wrong)).
Give it five minutes, then check the case in the workbench or Operate.

Before you show the learn loop (Act 4), seed some decision history once: `python3 scripts/verify.py seed --n 6`
(10 to 20 minutes). Without it the improvement agent correctly declines to change a rule on too few cases.

[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) has the full click path, persona by persona.

## Put the models in Hub (optional)

To read and edit the models in Camunda Hub:

1. Open a workspace and create a project, for example "Credit operations".
2. Click **Create new > Upload files** and select every file in `bpmn/`, `dmn/` and `forms/`.
3. Open `CB_CreditDispute.bpmn` and click any element. Its **Documentation** says what it does and who owns it.

You can deploy from Hub too (**Deploy & run > Deploy**). Hub does not create the cluster variables or run the job
workers, so steps 5 and 6 are still needed. If you edit a model in Hub, deploy it from Hub. `setup.py` redeploys the
files in this repo and would overwrite your edit.

## How the pieces connect

```
 your browser ──> workbench  (app/server.py on your machine, http://127.0.0.1:5690)
                    │  serves the UI, runs 15 job workers, keeps a small SQLite file for the mock systems
                    │  calls the Orchestration Cluster REST API with the client from .env (OAuth client credentials)
                    ▼
 Camunda 8.10 SaaS cluster
    engine ............ runs the 4 processes and 6 decision tables
    connectors ........ 3 AI agents ──> your LLM endpoint (key from the CB_LLM_API_KEY secret)
                        REST connector ──> date.nager.at (public holidays for the statutory clock)
                        webhook ──> starts an incident when a monitoring alert is posted
    cluster variables . CB_GENAI (endpoint, model), CB_PROMPTS (versioned prompts), CB_SYSTEMS (mock system data),
                        CB_CLOCK (demo time), CB_POLICY (learn-loop limits), CB_RECOGNITION
    document store .... evidence PDFs attached to each dispute
    Operate, Tasklist . the product's own views of the same cases and tasks
```

The browser only talks to the workbench. Credentials stay on the server side. The workbench has no login and listens
on 127.0.0.1 only, so treat it as a demo tool and do not expose it.

The workbench is one way to build a front end. It uses only public APIs (process instances, user tasks, decisions,
documents, agent instances, cluster variables), so your team can build its own UI on the same calls or use Tasklist
as it is. [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) lists every process, worker, variable and API the demo uses.

## Choosing a model

The agents use the AI Agent sub-process (v2) with the **OpenAI-compatible** provider type. The endpoint and model come
from the cluster variable `CB_GENAI`, so changing model needs no redeploy: edit `.env` and rerun `scripts/setup.py`.

- **Tested:** `anthropic/claude-sonnet-5` through OpenRouter. The prompts, the JSON output format and the routing
  were tuned and checked on it.
- **Other OpenAI-compatible models** should work, but the agent may choose differently on some cases. Run
  `python3 scripts/verify.py disputes` (with the workbench running) to check every scenario's route on your model.
- **Azure OpenAI, AWS Bedrock, Google Vertex** and similar providers are not OpenAI-compatible in this sense. Open each
  agent in Hub, change **Model provider** in the properties panel to that provider, and deploy from Hub. We have not
  tested those paths with this repo.

The model recommends; the decision tables decide. Evidence scores, approval authority, statutory clocks and incident
severity come from DMN, the agent passes the scores on unchanged, and the authority table decides who signs off.

## Reset and repeat

```
python3 scripts/reset_demo.py      # threshold back to 0.9, cancel open cases (closed cases stay as history)
python3 scripts/stage_demo.py      # reset, redeploy and stage fresh cases in one go
python3 scripts/status.py          # what is running now
```

`app/cb.db` holds the mock systems' state and the decision history the learn loop reads. Keep it between runs.

Demo time runs fast: one statutory day is 30 seconds and one working hour is 8 seconds (cluster variable `CB_CLOCK`).
Staged cases use a slower clock, but review tasks still escalate to senior analysts after about 80 minutes. Restage
shortly before you show it.

## When something goes wrong

| What you see | Likely cause and fix |
|---|---|
| `No cluster credentials found` | `.env` is missing or empty. Repeat step 2. |
| `setup.py` gets 401 or 403 | The client lacks the **Orchestration Cluster API** scope. Create a new client with it. |
| Incident on an agent: secret not available | The secret is missing or not yet propagated. Check its name is exactly `CB_LLM_API_KEY`, wait a few minutes, then retry the incident in Operate. |
| Incident on an agent: 401, 404 or model not found | Wrong key, endpoint or model id. Fix `.env` or the secret, rerun `setup.py`, retry in Operate. |
| A case sits on a service task | The workbench is not running (step 6). The jobs are picked up as soon as it starts. |
| Alert shows "via api" instead of "via webhook" | The webhook was not reachable yet. The workbench started the incident through the API instead, so the demo still works. Rerunning `setup.py` redeploys the webhook. |
| Right after `setup.py` or `stage_demo.py`, cases wait at the holiday lookup or the agent and the webhook times out | In our testing the connector runtime sometimes paused for up to 5 minutes right after a deploy, then caught up on its own. Wait, then check again. Nothing needs restarting. |
| An agent takes several minutes | Usually the LLM endpoint is slow or rate-limited. The job retries on its own after 5 minutes. |
| A case takes a different route than the walkthrough says | Normal on a model other than the tested one. See [Choosing a model](#choosing-a-model). |

## What is in the repo

| Path | What it is |
|---|---|
| `bpmn/` | 4 processes: credit dispute, incident management, rule improvement (learn loop), recognition |
| `dmn/` | 6 decision tables: statutory clock, evidence strength, approval authority, incident severity, points, rewards |
| `forms/` | 8 Camunda forms for the user tasks, so the same tasks also work in Tasklist |
| `app/` | The workbench: `server.py` (UI server, job workers, mock systems) and `static/` (the UI) |
| `data/` | Scenarios, prompts, mock system data, evidence documents (fictional) |
| `scripts/` | `setup.py`, `stage_demo.py`, `reset_demo.py`, `verify.py`, `status.py`; `build.py` generates the models |
| `docs/` | How it works, walkthrough |

The models are generated by `scripts/build.py`. To change a model, either edit it in Hub (and deploy from Hub) or change `build.py` and rerun
`setup.py`. Do not hand-edit the XML in `bpmn/`: the next build overwrites it.

This is demonstration code, provided as is, to show the patterns. It is not production software.
