"""Workbench: page + API + job workers + mock bureau systems, in one process.

  python3 app/server.py            serves http://127.0.0.1:5690 and runs every job worker

Camunda is the source of truth for cases, tasks, decisions and agent runs. SQLite (app/cb.db) stands in for
bureau systems the workers integrate with: the credit file, the notification outbox, the case outcome
store and the recognition ledger.
"""
import json
import os
import random
import sqlite3
import sys
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import c8  # noqa: E402

PORT = int(c8.cfg.get("CB_PORT", "5690"))
DB = os.path.join(HERE, "cb.db")
SC = json.load(open(os.path.join(ROOT, "data", "scenarios.json")))
FIXTURES = {v["start"]["disputeId"]: v for v in SC["disputes"].values()}
CLUSTER = c8.cfg["CAMUNDA_CLUSTER_ID"]
REGION = c8.cfg["CAMUNDA_CLUSTER_REGION"]
WEBHOOK = f"https://{REGION}.connectors.camunda.io/{CLUSTER}/inbound/cb-monitoring-alert"
OPERATE = f"https://{REGION}.api.camunda.io/{CLUSTER}/operate"
TASKLIST = f"https://{REGION}.api.camunda.io/{CLUSTER}/tasklist"
LOG = []
LOCKS = set()          # dispute ids whose credit file record is locked (exception demo)
_dblock = threading.Lock()
DOCS_DIR = os.path.join(ROOT, "data", "documents")
DOCS = json.load(open(os.path.join(DOCS_DIR, "manifest.json"))) if os.path.exists(os.path.join(DOCS_DIR, "manifest.json")) else {}


def log(kind, text):
    LOG.append({"at": time.strftime("%H:%M:%S"), "kind": kind, "text": text})
    del LOG[:-300]
    print(f"[{kind}] {text}", flush=True)


# ------------------------------------------------------------------------------------------- database
def db():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _dblock, db() as con:
        con.executescript("""
        create table if not exists credit_file_changes(id integer primary key, dispute_id text, account text, outcome text, correction text, decided_by text, at text);
        create table if not exists outbox(id integer primary key, kind text, ref text, recipient text, body text, at text);
        create table if not exists outcomes(dispute_id text primary key, case_ref text, dispute_type text, jurisdiction text, route text, rule text,
            recommendation text, confidence real, furnisher_agrees int, fraud int, vulnerable int, analyst_decision text, override_reason text,
            final_outcome text, decided_by text, at text);
        create table if not exists ledger(id integer primary key, employee text, event_type text, points int, source_ref text, at text, day text);
        create table if not exists badges(id integer primary key, employee text, badge text, reward text, approved_by text, at text);
        create table if not exists counters(name text primary key, n int);
        """)
        if not con.execute("select count(*) from ledger").fetchone()[0]:
            seed = [("Nomvula Khumalo", 236), ("Arvind Rao", 218), ("Megan Brooks", 171), ("Karthik Subramanian", 158),
                    ("Lerato Molefe", 132), ("Priyanka Nair", 97), ("Sipho Ndlovu", 84), ("Daniel Okafor", 61)]
            day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
            for name, pts in seed:
                con.execute("insert into ledger(employee,event_type,points,source_ref,at,day) values(?,?,?,?,?,?)",
                            (name, "OPENING_BALANCE", pts, "Programme to date", day + "T09:00:00", day))
            con.execute("insert into badges(employee,badge,reward,approved_by,at) values(?,?,?,?,?)",
                        ("Nomvula Khumalo", "Bronze: First Responder", "Badge on profile", "auto", day))
            con.execute("insert into badges(employee,badge,reward,approved_by,at) values(?,?,?,?,?)",
                        ("Arvind Rao", "Bronze: First Responder", "Badge on profile", "auto", day))


def next_seq(name):
    with _dblock, db() as con:
        r = con.execute("select n from counters where name=?", (name,)).fetchone()
        n = (r["n"] if r else 0) + 1
        con.execute("insert or replace into counters(name,n) values(?,?)", (name, n))
        return n


# ------------------------------------------------------------------------------------------- camunda helpers
def publish(name, key, variables, ttl="PT1H", message_id=None):
    body = {"name": name, "correlationKey": key or "", "timeToLive": 3600000, "variables": variables}
    if message_id:
        body["messageId"] = message_id
    s, d = c8.call("POST", "/messages/publication", body)
    if s not in (200, 204):
        log("error", f"publish {name} {key}: {s} {d}")
    return s


def open_tasks(pik, element=None):
    flt = {"processInstanceKey": pik, "state": "CREATED"}
    if element:
        flt["elementId"] = element
    try:
        return c8.search("/user-tasks/search", flt, size=20)
    except Exception:
        return []


def patch_task(key, changeset):
    return c8.call("PATCH", f"/user-tasks/{key}", {"changeset": changeset})


def wait_task(pik, element, tries=12):
    for _ in range(tries):
        t = open_tasks(pik, element)
        if t:
            return t[0]
        time.sleep(1.5)
    return None


# ------------------------------------------------------------------------------------------- workers
def h_identity(job, v):
    return {"identity": {"verified": True, "method": "Identity resolution service", "matchScore": 0.98, "consumerId": v.get("consumerId")}}


def h_furnisher_send(job, v):
    did, ref = v["disputeId"], v.get("caseRef", v["disputeId"])
    fx = FIXTURES.get(ref, {})
    acdv = {"acdvId": f"ACDV-{random.randint(100000, 999999)}", "furnisher": v.get("furnisher"), "sentAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "channel": "e-OSCAR" if v.get("jurisdiction") == "US" else "Furnisher dispute API"}
    if fx.get("lockCreditFile"):
        LOCKS.add(did)
    f = fx.get("furnisher", {"respondAfterSeconds": 6, "position": "VERIFIED", "code": "VERIFIED_AS_REPORTED", "detail": "Verified as reported."})
    delay = f.get("respondAfterSeconds", 6)
    if delay >= 0:
        resp = {"position": f["position"], "code": f["code"], "detail": f["detail"], "acdvId": acdv["acdvId"], "furnisher": v.get("furnisher")}

        def later():
            resp["respondedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            publish("cb-furnisher-response", did, {"furnisherResponse": resp}, message_id=f"{did}-{job['processInstanceKey']}-furnisher")
            log("furnisher", f"{v.get('furnisher')} answered {did}: {f['code']}")
        threading.Timer(delay, later).start()
    else:
        log("furnisher", f"{v.get('furnisher')} will not answer {did}")
    return {"acdv": acdv}


def h_creditfile(job, v):
    did = v["disputeId"]
    d = v.get("decision") or {}
    if did in LOCKS:
        raise JobFailure(f"Credit file record {v.get('accountRef')} for {did} is locked by the Metro 2 batch load (LOCK-7731). Release the lock, then retry.")
    a = v.get("assessment") or {}
    with _dblock, db() as con:
        con.execute("insert into credit_file_changes(dispute_id,account,outcome,correction,decided_by,at) values(?,?,?,?,?,?)",
                    (did, v.get("accountRef"), d.get("outcome"), d.get("correction"), d.get("decidedBy"), time.strftime("%Y-%m-%dT%H:%M:%S")))
        con.execute("""insert or replace into outcomes values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (did, v.get("caseRef"), v.get("disputeType"), v.get("jurisdiction"), v.get("obsRoute"), v.get("obsAuthorityRule"),
                     a.get("recommendation"), a.get("confidence"), int(bool(a.get("furnisherAgrees"))), int(bool(a.get("fraudIndicators"))),
                     int(bool(v.get("vulnerableConsumer"))), v.get("obsAnalystDecision"), v.get("obsOverrideReason"), d.get("outcome"),
                     d.get("decidedBy"), time.strftime("%Y-%m-%dT%H:%M:%S")))
    log("creditfile", f"{did}: {d.get('outcome')} written ({d.get('decidedBy')})")
    return {"creditFileUpdate": {"status": "APPLIED", "outcome": d.get("outcome"), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}}


def h_notify(job, v):
    kind = v.get("notificationType", "NOTICE")
    if kind == "DISPUTE_RESULT":
        d = v.get("decision") or {}
        rows = [("consumer", v.get("consumerName"), d.get("letter") or ""), ("furnisher", v.get("furnisher"), f"Outcome {d.get('outcome')}: {d.get('correction')}")]
        ref = v.get("disputeId")
    else:
        sev = v.get("severity") or {}
        rows = [("pager", g, f"{sev.get('priority')} {v.get('alert', {}).get('title')}") for g in (sev.get("escalationChain") or "").split(",")[:1]]
        if sev.get("majorIncident"):
            rows.append(("bridge", "Major incident bridge", f"{v.get('incidentId')}: {v.get('alert', {}).get('title')}"))
        ref = v.get("incidentId")
    with _dblock, db() as con:
        for kind2, rcpt, body in rows:
            con.execute("insert into outbox(kind,ref,recipient,body,at) values(?,?,?,?,?)", (kind2, ref, rcpt, body, time.strftime("%Y-%m-%dT%H:%M:%S")))
    log("notify", f"{ref}: {', '.join(r[0] + ' ' + str(r[1]) for r in rows)}")
    return {"notifiedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}


def h_sla_alert(job, v):
    status = v.get("newSlaStatus", "AT_RISK")
    pik = job["processInstanceKey"]
    pri = 95 if status == "AT_RISK" else 100
    for t in open_tasks(pik):
        patch_task(t["userTaskKey"], {"priority": pri})
    log("sla", f"{v.get('disputeId')}: statutory clock {status}, open tasks priority {pri}")
    return {"slaStatus": status, "slaAlertAt": time.strftime("%Y-%m-%dT%H:%M:%S")}


def h_review_escalate(job, v):
    pik = job["processInstanceKey"]
    t = wait_task(pik, "ReviewRecommendation", 4)
    ladder = {"dispute-analysts": ["senior-analysts"], "senior-analysts": ["senior-analysts", "team-managers"],
              "fraud-compliance": ["fraud-compliance", "compliance-officers"]}
    group = ((v.get("authority") or {}).get("reviewGroup")) or "dispute-analysts"
    to = ladder.get(group, ["senior-analysts"])
    if t:
        patch_task(t["userTaskKey"], {"candidateGroups": to, "priority": 95})
        if t.get("assignee"):
            c8.call("DELETE", f"/user-tasks/{t['userTaskKey']}/assignee")
    log("sla", f"{v.get('disputeId')}: review SLA missed, {group} -> {', '.join(to)}")
    return {"reviewEscalated": True, "escalatedTo": ", ".join(to), "escalatedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}


def h_achievement(job, v):
    payload = {"eventType": v.get("eventType"), "employee": v.get("employee") or "Team", "withinSla": bool(v.get("withinSla")),
               "sourceRef": v.get("sourceRef")}
    if str(payload["employee"]).startswith(("Rule ", "Policy ", "Consumer")):
        log("achievement", f"{payload['sourceRef']}: no points, decided by {payload['employee']}")
        return {"achievementPublished": False}
    publish("cb-achievement", "", payload, message_id=f"{v.get('sourceRef')}-{v.get('eventType')}-{job['processInstanceKey']}")
    log("achievement", f"{payload['employee']}: {payload['eventType']} ({payload['sourceRef']})")
    return {"achievementPublished": True}


def h_flag_disputes(job, v):
    items = c8.search("/process-instances/search", {"processDefinitionId": "CB_CreditDispute", "state": "ACTIVE"}, size=100)
    flagged = []
    for pi in items:
        vs = c8.search("/variables/search", {"processInstanceKey": pi["processInstanceKey"], "name": "disputeId"}, size=5)
        if vs:
            did = json.loads(vs[0]["value"])
            publish("cb-dispute-service-incident", did, {"incidentRef": v.get("incidentId"), "incidentPriority": (v.get("severity") or {}).get("priority")})
            flagged.append(did)
    log("incident", f"{v.get('incidentId')}: flagged {len(flagged)} open disputes")
    return {"flaggedDisputes": len(flagged), "flaggedIds": flagged[:50]}


def h_incident_escalate(job, v):
    chain = ((v.get("severity") or {}).get("escalationChain") or "sre-oncall").split(",")
    level = min(int(v.get("escalationLevel") or 0) + 1, len(chain) - 1)
    group = chain[level]
    t = wait_task(job["processInstanceKey"], "Acknowledge", 3)
    if t:
        patch_task(t["userTaskKey"], {"candidateGroups": [group, chain[0]], "priority": 100})
    publish_note = f"{v.get('incidentId')}: not acknowledged, escalated to {group} (level {level})"
    with _dblock, db() as con:
        con.execute("insert into outbox(kind,ref,recipient,body,at) values(?,?,?,?,?)", ("pager", v.get("incidentId"), group, publish_note, time.strftime("%Y-%m-%dT%H:%M:%S")))
    log("incident", publish_note)
    return {"escalationLevel": level, "escalatedTo": group}


def cvar(name):
    s, d = c8.call("GET", f"/cluster-variables/global/{name}")
    val = d.get("value") if isinstance(d, dict) else None
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except ValueError:
            pass
    return val


def outcomes():
    with db() as con:
        return [dict(r) for r in con.execute("select * from outcomes")]


def h_learn_stats(job, v):
    rows = outcomes()
    bands = {}
    for r in rows:
        if r["route"] != "REVIEW":
            continue
        c = r["confidence"] or 0
        band = "0.90+" if c >= 0.9 else "0.85-0.90" if c >= 0.85 else "0.80-0.85" if c >= 0.8 else "below 0.80"
        k = f"{r['dispute_type']} | {band} | rule {r['rule']} | furnisher agrees {bool(r['furnisher_agrees'])}"
        b = bands.setdefault(k, {"approved": 0, "overridden": 0, "overrideReasons": []})
        if r["analyst_decision"] == "OVERRIDDEN":
            b["overridden"] += 1
            if r["override_reason"]:
                b["overrideReasons"].append(r["override_reason"])
        elif r["analyst_decision"] == "APPROVED":
            b["approved"] += 1
    auto = sum(1 for r in rows if r["route"] == "AUTO_APPLY")
    res = {"completedDisputes": len(rows), "autoApplied": auto, "reviewedByAnalysts": sum(1 for r in rows if r["route"] == "REVIEW"), "bands": bands}
    log("learn", f"override statistics over {len(rows)} completed disputes")
    return {"toolCallResult": res}


def would_auto(r, t):
    return (r["recommendation"] in ("CORRECT", "DELETE") and not r["fraud"] and not r["vulnerable"] and r["furnisher_agrees"]
            and (r["confidence"] or 0) >= t)


def h_learn_backtest(job, v):
    t = float(v.get("candidateValue") or 0.9)
    current = float((cvar("CB_POLICY") or {}).get("autoApplyMinConfidence", 0.9))
    rows = [r for r in outcomes() if r["route"] == "REVIEW" and r["rule"] in ("A-07",)]
    affected = [r for r in rows if would_auto(r, t) and not would_auto(r, current)]
    over = [r for r in affected if r["analyst_decision"] == "OVERRIDDEN"]
    res = {"candidateValue": t, "currentValue": current, "casesReplayed": len(outcomes()), "casesAffected": len(affected),
           "overriddenAmongAffected": len(over), "affectedDisputes": [r["dispute_id"] for r in affected][:20],
           "creditTo": sorted({r["decided_by"] for r in affected if r["decided_by"]})}
    log("learn", f"backtest {t}: {len(affected)} affected, {len(over)} overridden")
    return {"toolCallResult": res}


def h_learn_deploy(job, v):
    p = v.get("proposal") or {}
    t = float(p["proposedValue"])
    import importlib
    import build
    importlib.reload(build)
    path = os.path.join(ROOT, "dmn", "CB_DisputeApprovalAuthority.dmn")
    open(path, "w").write(build.authority(t))
    s, d = c8.deploy([path])
    if s != 200:
        raise JobFailure(f"Deploy failed: {s} {d}")
    dd = d["deployments"][0]["decisionDefinition"]
    json.dump({"autoApplyMinConfidence": t, "deployedVersion": dd["version"], "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
              open(os.path.join(ROOT, "data", "policy_state.json"), "w"))
    val = cvar("CB_POLICY")
    val["autoApplyMinConfidence"] = t
    c8.call("PUT", "/cluster-variables/global/CB_POLICY", {"value": val})
    log("learn", f"deployed CB_DisputeApprovalAuthority v{dd['version']} with autoApplyMinConfidence {t}")
    return {"deployedVersion": dd["version"], "deployedDecisionKey": dd["decisionDefinitionKey"], "newThreshold": t}


def ledger_totals(employee):
    with db() as con:
        tot = con.execute("select coalesce(sum(points),0) from ledger where employee=?", (employee,)).fetchone()[0]
        today = con.execute("select coalesce(sum(points),0) from ledger where employee=? and day=? and event_type!='OPENING_BALANCE'",
                            (employee, time.strftime("%Y-%m-%d"))).fetchone()[0]
    return tot, today


def h_points_read(job, v):
    tot, today = ledger_totals(v.get("employee"))
    return {"previousTotal": tot, "pointsToday": today}


def h_points_update(job, v):
    pts = int(v.get("points") or 0)
    with _dblock, db() as con:
        con.execute("insert into ledger(employee,event_type,points,source_ref,at,day) values(?,?,?,?,?,?)",
                    (v.get("employee"), v.get("eventType"), pts, v.get("sourceRef"), time.strftime("%Y-%m-%dT%H:%M:%S"), time.strftime("%Y-%m-%d")))
    tot, _ = ledger_totals(v.get("employee"))
    log("points", f"{v.get('employee')} +{pts} for {v.get('eventType')} (total {tot})")
    return {"newTotal": tot}


def h_points_award(job, v):
    r = v.get("reward") or {}
    if v.get("managerDecision") == "DECLINE":
        return {"awarded": False}
    with _dblock, db() as con:
        con.execute("insert into badges(employee,badge,reward,approved_by,at) values(?,?,?,?,?)",
                    (v.get("employee"), r.get("badge"), r.get("rewardName"), "manager" if r.get("needsApproval") else "auto", time.strftime("%Y-%m-%dT%H:%M:%S")))
    log("points", f"{v.get('employee')} awarded {r.get('badge')}")
    return {"awarded": True}


HANDLERS = {
    "cb.identity.verify": h_identity, "cb.furnisher.send": h_furnisher_send, "cb.creditfile.update": h_creditfile,
    "cb.notify": h_notify, "cb.sla.alert": h_sla_alert, "cb.review.escalate": h_review_escalate,
    "cb.achievement.publish": h_achievement, "cb.disputes.flag": h_flag_disputes, "cb.incident.escalate": h_incident_escalate,
    "cb.learn.stats": h_learn_stats, "cb.learn.backtest": h_learn_backtest, "cb.learn.deploy": h_learn_deploy,
    "cb.points.read": h_points_read, "cb.points.update": h_points_update, "cb.points.award": h_points_award,
}


class JobFailure(Exception):
    pass


def worker_loop(jobtype, fn):
    while True:
        try:
            s, d = c8.call("POST", "/jobs/activation", {"type": jobtype, "worker": "cb-workbench", "timeout": 30000,
                                                       "maxJobsToActivate": 8, "requestTimeout": 20000})
            if s != 200:
                time.sleep(3)
                continue
            for job in d.get("jobs", []):
                threading.Thread(target=run_job, args=(jobtype, fn, job), daemon=True).start()
        except Exception as e:  # network blips: keep polling
            log("error", f"{jobtype} poll: {e}")
            time.sleep(3)


def run_job(jobtype, fn, job):
    key = job["jobKey"]
    try:
        out = fn(job, job.get("variables") or {})
        s, d = c8.call("POST", f"/jobs/{key}/completion", {"variables": out or {}})
        if s not in (200, 204):
            log("error", f"{jobtype} complete {key}: {s} {d}")
    except JobFailure as e:
        c8.call("POST", f"/jobs/{key}/failure", {"retries": 0, "errorMessage": str(e)})
        log("incident", f"{jobtype}: {e}")
    except Exception as e:
        traceback.print_exc()
        retries = max(0, job.get("retries", 1) - 1)
        c8.call("POST", f"/jobs/{key}/failure", {"retries": retries, "errorMessage": f"{type(e).__name__}: {e}", "retryBackOff": 5000})
        log("error", f"{jobtype} failed: {e}")


# ------------------------------------------------------------------------------------------- read model
def vval(v):
    try:
        return json.loads(v.get("value"))
    except (TypeError, ValueError):
        return v.get("value")


def variables(pik, names=None):
    flt = {"processInstanceKey": pik}
    items = c8.search("/variables/search", flt, size=500)
    out = {}
    for it in sorted(items, key=lambda x: 0 if str(x.get("scopeKey")) == str(pik) else 1, reverse=True):
        if names and it["name"] not in names:
            continue
        val = vval(it)
        if it.get("isTruncated"):
            s, full = c8.call("GET", f"/variables/{it['variableKey']}")
            val = vval(full) if s == 200 else val
        out[it["name"]] = val
    return out


def case_list(pid):
    items = c8.search("/process-instances/search", {"processDefinitionId": pid}, size=60, sort=[{"field": "startDate", "order": "DESC"}])
    return items


def agent_trail(pik, agent_var):
    vs = c8.search("/variables/search", {"processInstanceKey": pik, "name": "agent"}, size=10)
    a = None
    for it in vs:
        val = vval(it)
        if it.get("isTruncated"):
            s, full = c8.call("GET", f"/variables/{it['variableKey']}")
            val = vval(full) if s == 200 else val
        a = val
    if a is None:
        vs = c8.search("/variables/search", {"processInstanceKey": pik, "name": agent_var}, size=5)
        for it in vs:
            s, full = c8.call("GET", f"/variables/{it['variableKey']}")
            a = vval(full) if s == 200 else vval(it)
    if not isinstance(a, dict):
        return None
    ctx = a.get("context") or {}
    steps = []
    for m in (ctx.get("conversation") or {}).get("messages") or []:
        ts = (m.get("metadata") or {}).get("timestamp")
        if m.get("role") == "assistant":
            for tc in m.get("toolCalls") or []:
                steps.append({"type": "call", "id": tc.get("id"), "name": tc.get("name"), "args": tc.get("arguments"), "at": ts})
            txt = " ".join(c.get("text", "") for c in m.get("content") or [] if c.get("type") == "text").strip()
            if txt and not m.get("toolCalls"):
                steps.append({"type": "answer", "text": txt[:3000], "at": ts})
        elif m.get("role") == "tool_call_result":
            for r in m.get("results") or []:
                content = r.get("content")
                if isinstance(content, list):
                    content = [c.get("content", c.get("text")) for c in content]
                    content = content[0] if len(content) == 1 else content
                steps.append({"type": "result", "id": r.get("id"), "name": r.get("name"), "result": content, "at": ts})
    return {"state": ctx.get("state"), "metrics": ctx.get("metrics"), "steps": steps}


def case_detail(pik):
    s, inst = c8.call("GET", f"/process-instances/{pik}")
    if s != 200:
        return {"error": inst}
    v = variables(pik)
    els = sorted(c8.search("/element-instances/search", {"processInstanceKey": pik}, size=300), key=lambda e: (e.get("startDate") or "", e.get("elementInstanceKey")))
    tasks = c8.search("/user-tasks/search", {"processInstanceKey": pik}, size=50)
    incs = c8.search("/incidents/search", {"processInstanceKey": pik}, size=20)
    decs = c8.search("/decision-instances/search", {"processInstanceKey": pik}, size=50)
    dec_out = []
    for dd in decs:
        s2, full = c8.call("GET", f"/decision-instances/{dd['decisionEvaluationInstanceKey']}")
        if s2 == 200:
            dec_out.append({"decisionId": full.get("decisionDefinitionId"), "name": full.get("decisionDefinitionName"),
                            "version": full.get("decisionDefinitionVersion"), "at": full.get("evaluationDate"),
                            "matchedRules": [{"ruleId": m.get("ruleId"), "ruleIndex": m.get("ruleIndex"),
                                              "outputs": {o.get("outputName"): vval({"value": o.get("outputValue")}) for o in m.get("evaluatedOutputs") or []}}
                                             for m in full.get("matchedRules") or []],
                            "inputs": {i.get("inputName"): vval({"value": i.get("inputValue")}) for i in full.get("evaluatedInputs") or []},
                            "result": vval({"value": full.get("result")})})
    pid = inst.get("processDefinitionId")
    agent_var = {"CB_CreditDispute": "investigator", "CB_IncidentManagement": "triageAgent", "CB_RuleImprovement": "improvementAgent"}.get(pid)
    return {"instance": inst, "variables": v, "elements": [{"id": e["elementId"], "name": e.get("elementName"), "type": e.get("type"),
                                                            "state": e.get("state"), "start": e.get("startDate"), "end": e.get("endDate"),
                                                            "incident": e.get("hasIncident")} for e in els],
            "tasks": tasks, "incidents": incs, "decisions": dec_out,
            "agent": agent_trail(pik, agent_var) if agent_var else None,
            "operate": f"{OPERATE}/processes/{pik}", "locked": v.get("disputeId") in LOCKS}


SUMMARY_VARS = {"disputeId", "consumerName", "jurisdiction", "disputeType", "furnisher", "slaStatus", "clock", "authority", "decision",
                "serviceIncident", "incidentId", "alert", "severity", "triage", "escalationLevel", "service", "employee", "eventType",
                "proposal", "validation", "deployedVersion", "assessment", "reviewEscalated"}


def summaries(pid, limit=40):
    out = []
    for pi in case_list(pid)[:limit]:
        pik = pi["processInstanceKey"]
        vs = {}
        for it in c8.search("/variables/search", {"processInstanceKey": pik}, size=300):
            if it["name"] in SUMMARY_VARS and (it["name"] not in vs or str(it.get("scopeKey")) == str(pik)):
                vs[it["name"]] = vval(it) if not it.get("isTruncated") else "(large)"
        out.append({"key": pik, "state": pi.get("state"), "start": pi.get("startDate"), "end": pi.get("endDate"),
                    "incident": pi.get("hasIncident"), "version": pi.get("processDefinitionVersion"), "vars": vs})
    return out


_cache = {}


def cached(key, ttl, fn):
    c = _cache.get(key)
    if c and time.time() - c[0] < ttl:
        return c[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


# ------------------------------------------------------------------------------------------- documents
def upload_document(path, file_name, props, content_type="application/pdf"):
    """Store one file in Camunda's document store; returns the document reference to keep as a process variable."""
    b = "----cb" + os.urandom(8).hex()
    meta = {"contentType": content_type, "fileName": file_name, "customProperties": props}
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\nContent-Type: {content_type}\r\n\r\n').encode() \
        + open(path, "rb").read() + (f'\r\n--{b}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n'
                                     + json.dumps(meta) + f'\r\n--{b}--\r\n').encode()
    s, d = c8.call("POST", "/documents", raw=body, ctype=f"multipart/form-data; boundary={b}")
    if s >= 300:
        log("error", f"document upload {file_name}: {s} {str(d)[:200]}")
        return None
    return d


def evidence_documents(base, dispute_id):
    refs = []
    for doc in DOCS.get(base, []):
        ref = upload_document(os.path.join(DOCS_DIR, doc["file"]), doc["fileName"],
                              {"evidenceType": doc["type"], "disputeId": dispute_id, "thumb": doc["thumb"]})
        if ref:
            refs.append(ref)
    return refs


def fetch_document(doc_id, content_hash=None):
    q = f"?contentHash={content_hash}" if content_hash else ""
    req = urllib.request.Request(c8.BASE + f"/documents/{doc_id}" + q, headers={"Authorization": "Bearer " + c8.token(), "User-Agent": c8.UA})
    r = urllib.request.urlopen(req, timeout=60)
    return r.read(), r.headers.get("Content-Type", "application/octet-stream")


# ------------------------------------------------------------------------------------------- actions
def start_dispute(name, extra=None):
    fx = SC["disputes"][name]
    v = dict(fx["start"])
    base = v["disputeId"]
    n = next_seq(base)
    v["caseRef"] = base
    v["disputeId"] = base if n == 1 else f"{base}-{n:02d}"
    v.update(extra or {})
    v["evidenceDocuments"] = evidence_documents(base, v["disputeId"])
    s, d = c8.call("POST", "/process-instances", {"processDefinitionId": "CB_CreditDispute", "variables": v, "businessId": v["disputeId"]})
    if s != 200:
        s, d = c8.call("POST", "/process-instances", {"processDefinitionId": "CB_CreditDispute", "variables": v})
    log("dispute", f"started {v['disputeId']} ({name}) -> {d.get('processInstanceKey') if isinstance(d, dict) else d}")
    return {"status": s, "disputeId": v["disputeId"], "processInstanceKey": d.get("processInstanceKey") if isinstance(d, dict) else None}


def raise_alert(name, via_webhook=True, extra=None):
    fx = dict(SC["incidents"][name])
    n = next_seq("INC")
    body = {"incidentId": f"INC-26-{1400 + n}", "alertId": fx["alertId"], "service": fx["service"], "source": fx["source"], "title": fx["title"],
            "raisedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}
    body.update(extra or {})
    if via_webhook:
        try:
            req = urllib.request.Request(WEBHOOK, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (cb-observability-sim)"})
            r = urllib.request.urlopen(req, timeout=20)
            log("incident", f"alert {body['incidentId']} posted to webhook: {r.status}")
            return {"status": r.status, "incidentId": body["incidentId"], "via": "webhook"}
        except Exception as e:
            log("error", f"webhook failed ({e}); starting by API")
    v = {"alert": body, "incidentId": body["incidentId"], "service": body["service"]}
    v.update({k: body[k] for k in ("clockSecondsPerMinute",) if k in body})
    s, d = c8.call("POST", "/process-instances", {"processDefinitionId": "CB_IncidentManagement", "variables": v, "businessId": body["incidentId"]})
    return {"status": s, "incidentId": body["incidentId"], "via": "api", "processInstanceKey": d.get("processInstanceKey") if isinstance(d, dict) else None}


def complete_task(key, variables_, assignee=None):
    if assignee:
        c8.call("POST", f"/user-tasks/{key}/assignment", {"assignee": assignee, "allowOverride": True})
    s, d = c8.call("POST", f"/user-tasks/{key}/completion", {"variables": variables_ or {}})
    log("task", f"completed {key} by {assignee}: {s}")
    return {"status": s, "detail": d}


def governance():
    s, d = c8.call("POST", "/agent-instances/search", {"filter": {}, "page": {"limit": 300}, "sort": [{"field": "creationDate", "order": "DESC"}]})
    names = {"DisputeInvestigator": "Dispute investigator", "TriageAgent": "Incident triage", "ImprovementAgent": "Improvement analyst"}
    items = [{"agent": names[a["elementId"]], "processInstanceKey": a.get("processInstanceKey"), "status": a.get("status"),
              "model": (a.get("definition") or {}).get("model"), "modelCalls": (a.get("metrics") or {}).get("modelCalls"),
              "toolCalls": (a.get("metrics") or {}).get("toolCalls"), "maxModelCalls": (a.get("limits") or {}).get("maxModelCalls"),
              "tokens": ((a.get("metrics") or {}).get("inputTokens") or 0) + ((a.get("metrics") or {}).get("outputTokens") or 0),
              "created": a.get("creationDate")}
             for a in (d.get("items", []) if s == 200 and isinstance(d, dict) else []) if a.get("elementId") in names]
    with db() as con:
        oc = [dict(r) for r in con.execute("select * from outcomes order by at desc")]
    reviewed = [o for o in oc if o["route"] == "REVIEW"]
    over = [o for o in reviewed if o["analyst_decision"] == "OVERRIDDEN"]
    pol, prompts, genai = cvar("CB_POLICY"), cvar("CB_PROMPTS"), cvar("CB_GENAI")
    return {"agents": items, "agentStatus": s, "outcomes": oc[:60],
            "kpi": {"completed": len(oc), "auto": sum(1 for o in oc if o["route"] == "AUTO_APPLY"), "reviewed": len(reviewed),
                    "overridden": len(over), "unverified": sum(1 for o in oc if o["route"] == "UNVERIFIED_DELETE"),
                    "agreement": round(100 * (len(reviewed) - len(over)) / len(reviewed)) if reviewed else None},
            "policy": pol, "prompts": {k: {kk: vv for kk, vv in p.items() if kk != "text"} for k, p in (prompts or {}).items()},
            "genai": genai}


def leaderboard():
    with db() as con:
        rows = [dict(r) for r in con.execute("select employee, sum(points) total, max(at) last from ledger group by employee order by total desc")]
        badges = [dict(r) for r in con.execute("select * from badges order by at desc")]
        recent = [dict(r) for r in con.execute("select * from ledger where event_type!='OPENING_BALANCE' order by id desc limit 20")]
    for r in rows:
        r["badges"] = [b["badge"] for b in badges if b["employee"] == r["employee"]]
    return {"rows": rows, "recent": recent, "badges": badges[:20]}


# ------------------------------------------------------------------------------------------- http
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        try:
            if p == "/api/disputes":
                return self.send_json(cached("disputes", 2.5, lambda: summaries("CB_CreditDispute")))
            if p == "/api/incidents":
                return self.send_json(cached("incidents", 2.5, lambda: summaries("CB_IncidentManagement", 20)))
            if p == "/api/improvements":
                return self.send_json(cached("improvements", 3, lambda: summaries("CB_RuleImprovement", 10)))
            if p.startswith("/api/case/"):
                return self.send_json(case_detail(p.rsplit("/", 1)[1]))
            if p == "/api/tasks":
                flt = {"state": "CREATED"}
                return self.send_json(cached("tasks", 2, lambda: c8.search("/user-tasks/search", flt, size=200, sort=[{"field": "creationDate", "order": "DESC"}])))
            if p == "/api/leaderboard":
                return self.send_json(leaderboard())
            if p == "/api/governance":
                return self.send_json(cached("gov", 4, governance))
            if p == "/api/log":
                return self.send_json(LOG[-80:])
            if p.startswith("/api/documents/"):
                body, ctype = fetch_document(p.rsplit("/", 1)[1], (q.get("contentHash") or [None])[0])
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition", "inline")
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                return self.wfile.write(body)
            if p == "/api/config":
                return self.send_json({"operate": OPERATE, "tasklist": TASKLIST, "webhook": WEBHOOK, "cluster": CLUSTER,
                                       "scenarios": {k: {"consumerName": v["start"]["consumerName"], "jurisdiction": v["start"]["jurisdiction"],
                                                         "disputeType": v["start"]["disputeType"]} for k, v in SC["disputes"].items()},
                                       "incidentScenarios": {k: v["title"] for k, v in SC["incidents"].items()}, "locks": sorted(LOCKS)})
            path = os.path.join(HERE, "static", "index.html" if p in ("/", "") else p.lstrip("/"))
            if p.startswith("/assets/"):
                path = os.path.join(ROOT, p.lstrip("/"))
            if p.startswith("/evidence/"):
                path = os.path.join(DOCS_DIR, os.path.basename(p))
            if os.path.isfile(path):
                b = open(path, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", MIME.get(os.path.splitext(path)[1], "application/octet-stream"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(b)
            self.send_json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        b = self.body()
        try:
            _cache.clear()
            if p == "/api/disputes/start":
                return self.send_json(start_dispute(b["scenario"], b.get("variables")))
            if p == "/api/incidents/alert":
                return self.send_json(raise_alert(b["scenario"], b.get("webhook", True), b.get("variables")))
            if p.startswith("/api/tasks/") and p.endswith("/complete"):
                return self.send_json(complete_task(p.split("/")[3], b.get("variables"), b.get("assignee")))
            if p.startswith("/api/tasks/") and p.endswith("/claim"):
                s, d = c8.call("POST", f"/user-tasks/{p.split('/')[3]}/assignment", {"assignee": b["assignee"], "allowOverride": True})
                return self.send_json({"status": s, "detail": d})
            if p.startswith("/api/tasks/") and p.endswith("/unclaim"):
                s, d = c8.call("DELETE", f"/user-tasks/{p.split('/')[3]}/assignee")
                return self.send_json({"status": s})
            if p.startswith("/api/tasks/") and p.endswith("/reassign"):
                s, d = patch_task(p.split("/")[3], {"candidateGroups": b["candidateGroups"]})
                return self.send_json({"status": s, "detail": d})
            if p.startswith("/api/ops/unlock/"):
                did = p.rsplit("/", 1)[1]
                LOCKS.discard(did)
                log("creditfile", f"lock released for {did}")
                return self.send_json({"unlocked": did})
            if p.startswith("/api/ops/retry/"):
                pik = p.rsplit("/", 1)[1]
                res = []
                for inc in c8.search("/incidents/search", {"processInstanceKey": pik, "state": "ACTIVE"}, size=10):
                    if inc.get("jobKey"):
                        c8.call("PATCH", f"/jobs/{inc['jobKey']}", {"changeset": {"retries": 1}})
                        time.sleep(1.5)   # let the retries update land before resolving
                    s, d = c8.call("POST", f"/incidents/{inc['incidentKey']}/resolution", {})
                    res.append(s)
                log("creditfile", f"incident retried for {pik}: {res}")
                return self.send_json({"resolved": res})
            if p == "/api/improvements/start":
                s, d = c8.call("POST", "/process-instances", {"processDefinitionId": "CB_RuleImprovement", "variables": {"requestedBy": b.get("requestedBy", "Continuous Improvement")}})
                log("learn", f"improvement review started: {s}")
                return self.send_json({"status": s, "processInstanceKey": d.get("processInstanceKey") if isinstance(d, dict) else None})
            if p.startswith("/api/disputes/") and p.endswith("/withdraw"):
                did = p.split("/")[3]
                return self.send_json({"status": publish("cb-dispute-withdrawn", did, {"withdrawnAt": time.strftime("%Y-%m-%dT%H:%M:%S")})})
            self.send_json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)


def main():
    init_db()
    for jt, fn in HANDLERS.items():
        threading.Thread(target=worker_loop, args=(jt, fn), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    log("server", f"Workbench on http://127.0.0.1:{PORT} with {len(HANDLERS)} job workers")
    srv.serve_forever()


if __name__ == "__main__":
    main()
