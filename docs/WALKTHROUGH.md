# Walkthrough: what to click and what to look for

Five acts. Run `python3 scripts/stage_demo.py` first so the queues have
work. The workbench is at http://127.0.0.1:5690. The persona picker at the top changes whose queue and rights you see.

| Persona | Role | Queues |
|---|---|---|
| Nomvula Khumalo | Dispute analyst, Johannesburg | dispute-analysts, dispute-specialists |
| Arvind Rao | Senior dispute analyst, Chennai | senior-analysts, dispute-analysts, dispute-specialists |
| Megan Brooks | Fraud and compliance, Chicago | fraud-compliance, compliance-officers |
| Karthik Subramanian | On-call engineer, Pune | the on-call groups, service-owners |
| Elena Petrova | Incident manager | incident-managers, head-of-operations |
| David Mensah | Rule owner and team manager | rule-owners, team-managers |
| Operations lead | Sees everything | all |

## Act 1: a consumer dispute, end to end

**The model.** Open `CB_CreditDispute.bpmn` in Hub (or the process in Operate). The large box is the statutory
reinvestigation, with two timers on its edge: a warning at 75% of the clock and the deadline. Inside it, the
**Dispute investigator agent** is an AI Agent sub-process; the boxes inside it are its tools. Right of the agent:
**Determine approval authority** (a decision table), the **Who signs off?** gateway and **Review AI recommendation**.
Click any element: its documentation says what it does and who owns it.

**Log a dispute**. As Nomvula, open **Disputes**, pick *Thandiwe Mokoena, Balance incorrect, ZA*
and click **Log dispute**. The case view opens and fills in as the process runs:
- identity verified by a job worker (a stand-in for an identity service);
- public holidays from a real REST call to a public holiday API, then the statutory clock from the
  `CB_DisputeSlaPolicy` table: South Africa's NCA, 20 business days, with the due date on screen;
- her evidence document on the left, stored in Camunda's document store; click it to open the PDF next to what the
  investigator read from it;
- the furnisher's answer arrives as a message event a few seconds later;
- the agent calls its tools (credit file, furnisher response, evidence, history, fraud signals, policy, then the
  evidence table) and recommends **verify as reported**: she paid three days after the reporting date;
- the authority table applies rule A-05: the outcome goes against the consumer, so a person signs off.

The agent cannot make up the evidence score. The `CB_EvidenceStrength` table sets it and the prompt tells the agent
to pass it on unchanged.

**The analyst decides**. The right panel shows the recommendation, the evidence with its sources, the
policy clause, the rule that sent it here and a draft letter. Edit the letter, then approve or override with a reason.
Both go on the record and feed the learn loop in Act 4.

If nobody acts for about a minute, the review SLA fires: eight working hours in demo time. The task moves to
senior-analysts at priority 95. Switch to Arvind Rao and decide as him.

**Other routes from the same agent**. In **Disputes**, open the staged cases:
- *Jordan Ellis*: furnisher agrees, documented evidence, high score. Rule A-06 applies it with no person involved.
- *Priya Raman* (not my account): rule A-01 sends it to fraud and compliance (Megan). Her identity theft report and
  proof of address are on the case. The agent may also have asked a dispute specialist a question.
- *Lakshmi Iyer* (vulnerable consumer, India): rule A-02, senior analyst, whatever the score.
- *Marcus Bell*: the furnisher never answered. When the window closed the item was deleted as unverified.

**Work queues**. **My work** as Megan, then Arvind: priority, due countdown, claim. Open Tasklist: the same tasks
in the product's own task list, using the forms in `forms/`.

**A system fails**. Open *Oliver Hughes* (red Incident). The credit file record is locked by a batch load, so the
update failed and Camunda raised an incident instead of losing the case. In Operate you see it on **Update credit
file**. In the workbench click **Release lock**, then **Retry**. The case completes. Nothing was lost or restarted.

## Act 2: a production incident and AI triage

**Alert by webhook**. As Karthik, open **Incidents**, pick *Dispute Intake API p95 latency* and click
**Send monitoring alert**. The workbench POSTs it to the process's webhook, as an observability tool would. The triage
agent reads service health, recent changes, similar incidents and the runbook, then names a likely cause and a first
action.

**Severity and blast radius.** The `CB_IncidentSeverity` table sets P1 and the acknowledge SLA. The incident also flags
every open dispute that depends on the service: open Priya's case and see the banner and the clock at risk. The two
processes talk through a message, not point-to-point code.

**Escalation ladder**. If nobody acknowledges in time, the incident escalates a level. As Karthik,
**Acknowledge**, then **Resolve** and answer "Was the AI triage right?". That answer is data too.

## Act 3: change a rule

In Hub, open `CB_DisputeSlaPolicy.dmn`, change a row (for example the UK clock), and deploy it. No code
change and no process redeploy. The next dispute uses the new version; running cases keep theirs. Operate's
**Decisions** view shows every version with its own decision instances.

If you also run `scripts/setup.py` later, it redeploys the repo's version of the table.

## Act 4: the learn loop and recognition

**Improvement review**. Open **Rule improvement**, click **Run improvement review**. The improvement agent
reads the change policy (cluster variable `CB_POLICY`), the override statistics from analysts' decisions and a
backtest of a lower auto-apply threshold, then proposes a change with the cases affected and overridden. A policy check
runs before anyone sees it. As David, **Approve and deploy**: a new version of `CB_DisputeApprovalAuthority` goes live.

Then log *Dana Whitfield, Late payment, US* (partial evidence). With the lower threshold it now applies within
authority. The model did not change; the rule around it did, from your analysts' own decisions.

On a fresh install the agent will first say **no change**, and it is right to. The override statistics come from
closed disputes recorded by the workbench, and the change policy needs at least five cases in a band before it moves
a threshold. Seed some history first, with the workbench running (10 to 20 minutes):

```
python3 scripts/verify.py seed --n 6
```

This runs six partial-evidence disputes that analysts approve, plus one they override. Then run the review again.

**Recognition**. The **Recognition** page shows points from the `CB_PointsScoring` table, the daily cap and
badges. A reward above the threshold waits for a manager's approval.

## Act 5: governance

**AI governance**. The workbench page joins the Agent Instances API to the cases: the prompt registry
(`CB_PROMPTS`, with id, version and owner), the change policy, each agent run with tokens and model calls against its
limit, and how often analysts agreed with the AI.

In Operate, open a dispute instance and the agent's panel: the conversation, every tool call and the tokens used.

Optimize can report on the same processes: duration, the slowest step and the agent metrics written as
flat variables (`obsRoute`, `obsModel`, `obsPromptVersion`, `obsTokens` and others).
