"""Generate every BPMN, DMN and form for the demo. Edit this file, not the XML.

  python3 scripts/build.py            writes bpmn/, dmn/, forms/
  python3 scripts/build.py --threshold 0.85   (used by the learn loop to write a new authority table)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from bpmnkit import Proc, dmn, error, message, message_nokey, msg_def, err_def, timer_def, a  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = open(os.path.join(ROOT, "scripts", "agent-icon.txt")).read().strip()
SYS = "camunda.vars.env.CB_SYSTEMS"
CLOCK = "camunda.vars.env.CB_CLOCK"


def secs(expr_units, per):
    """FEEL: ISO duration of <expr_units> demo units, per-unit seconds from a case variable or CB_CLOCK."""
    return (f'"PT" + string(max(5, floor({expr_units} * (if is defined({per}) and {per} != null then {per} '
            f'else {CLOCK}.{per[5:6].lower() + per[6:]})))) + "S"')


ERROR_EXPR = ('=if error != null and (error.code = "MAXIMUM_NUMBER_OF_MODEL_CALLS_REACHED" or error.code = "FAILED_TO_PARSE_RESPONSE_CONTENT" '
              'or error.code = "MODEL_RESPONSE_GUARDRAIL_INTERVENED" or error.code = "MODEL_CONTEXT_WINDOW_EXCEEDED") '
              'then bpmnError("AGENT_OFF_RAIL", error.message, {agentErrorCode: error.code}) else null')


def agent_ext(prompt_key, user_prompt, ctx_var, json_var, max_calls=12):
    ins = [
        ("openai", "provider.type"), ("completions", "provider.openai.api.type"), ("custom", "provider.openai.backend.type"),
        ("=camunda.vars.env.CB_GENAI.endpoint", "provider.openai.backend.custom.endpoint"),
        ("apiKey", "provider.openai.backend.custom.authentication.type"),
        ("{{secrets." + os.environ.get("CB_LLM_SECRET_NAME", "CB_LLM_API_KEY") + "}}", "provider.openai.backend.custom.authentication.apiKey"),
        ("=if is defined(modelOverride) and modelOverride != null then modelOverride else camunda.vars.env.CB_GENAI.model", "provider.openai.model.model"),
        ("modelDefault", "provider.openai.api.completions.effort"),
        ("=4096", "provider.openai.api.completions.maxCompletionTokens"),
        ("PT90S", "provider.openai.timeouts.timeout"),
        (f"=camunda.vars.env.CB_PROMPTS.{prompt_key}.text", "data.systemPrompt.prompt"),
        (user_prompt, "data.userPrompt.prompt"),
        (f"={ctx_var}.context", "agentContext"),
        ("in-process", "data.memory.storage.type"), ("=30", "data.memory.contextWindowSize"),
        (f"=if is defined(maxModelCallsOverride) and maxModelCallsOverride != null then maxModelCallsOverride else {max_calls}", "data.limits.maxModelCalls"),
        ("WAIT_FOR_TOOL_CALL_RESULTS", "data.events.behavior"),
        ("text", "data.response.format.type"), ("=true", "data.response.format.parseJson"),
        ("=false", "data.response.includeAssistantMessage"), ("=true", "data.response.includeAgentContext"),
        (None, "agent"),
    ]
    from bpmnkit import io
    return ('<zeebe:adHoc outputCollection="toolCallResults" outputElement="={&#10;  id: toolCall._meta.id,&#10;  name: toolCall._meta.name,&#10;  content: toolCallResult&#10;}" />'
            '<zeebe:taskDefinition type="io.camunda.agenticai:aiagent:subprocess:2" retries="3" />'
            + io(ins, [("=agent", ctx_var), ('=if agent.responseJson != null then agent.responseJson else if agent.responseText != null then from json(replace(replace(agent.responseText, "^\\s*```(json)?\\s*", ""), "\\s*```\\s*$", "")) else null', json_var)]) +
            '<zeebe:taskHeaders><zeebe:header key="elementTemplateVersion" value="1" />'
            '<zeebe:header key="elementTemplateId" value="io.camunda.connectors.agenticai.ai-agent-subprocess.v2" />'
            '<zeebe:header key="retryBackoff" value="PT10S" />'
            f'<zeebe:header key="errorExpression" value="{a(ERROR_EXPR)}" /></zeebe:taskHeaders>'
            '<zeebe:properties><zeebe:property name="io.camunda.agenticai.toolContainer" value="true" /></zeebe:properties>'
            '<zeebe:agentDefinition agentType="aiAgentSubProcess" />')


AGENT_ATTRS = (' zeebe:modelerTemplate="io.camunda.connectors.agenticai.ai-agent-subprocess.v2" zeebe:modelerTemplateVersion="1"'
               f' zeebe:modelerTemplateIcon="{ICON}"')


def tool_script(p, eid, name, doc, expr, cx, cy, parent, inputs=()):
    p.script(eid, name, cx, cy, expr, "toolCallResult", inputs=inputs, doc=doc, parent=parent)


# =============================================================================== 1. credit dispute
def dispute():
    p = Proc("CB_CreditDispute", "Consumer credit dispute",
             "A consumer disputes an item on their credit file. The statutory clock comes from a decision table per jurisdiction. "
             "An AI agent investigates and recommends; an approval authority table decides who signs off; analysts approve or override.")
    p.defs += [message("Msg_FurnisherResponse", "cb-furnisher-response", "=disputeId"),
               message("Msg_Withdrawn", "cb-dispute-withdrawn", "=disputeId"),
               message("Msg_ServiceIncident", "cb-dispute-service-incident", "=disputeId"),
               error("Err_AgentOffRail", "Agent off the rails", "AGENT_OFF_RAIL"),
               error("Err_Holidays", "Holiday calendar unavailable", "HOLIDAYS_UNAVAILABLE")]
    Y = 330
    p.start("DisputeReceived", "Dispute received", 90, Y)
    p.service("VerifyIdentity", "Verify consumer identity", 210, Y, "cb.identity.verify",
              outputs=[("=identity", "identity")])
    # REST connector: real public API for the business-day calendar
    rest_in = [("noAuth", "authentication.type"), ("GET", "method"),
               ('="https://date.nager.at/api/v3/PublicHolidays/" + string(today().year) + "/" + (if jurisdiction = "UK" then "GB" else jurisdiction)', "url"),
               ("=10", "connectionTimeoutInSeconds"), ("=10", "readTimeoutInSeconds")]
    from bpmnkit import io
    p.task("FetchHolidays", "Fetch public holidays (REST)", 360, Y, "serviceTask",
           '<zeebe:taskDefinition type="io.camunda:http-json:1" retries="2" />' + io(rest_in) +
           '<zeebe:taskHeaders><zeebe:header key="resultExpression" value="={holidays: if is defined(response.body) and response.body != null then for h in response.body return h.date else []}" />'
           '<zeebe:header key="retryBackoff" value="PT2S" />'
           '<zeebe:header key="errorExpression" value="=if error != null then bpmnError(&quot;HOLIDAYS_UNAVAILABLE&quot;, error.message, {holidayError: error.message, holidayErrorCode: error.code}) else null" /></zeebe:taskHeaders>',
           attrs=' zeebe:modelerTemplate="io.camunda.connectors.HttpJson.v2" zeebe:modelerTemplateVersion="12"',
           doc="Calls the public Nager.Date API for the jurisdiction's public holidays, so business-day clocks are correct.")
    p.boundary("HolidaysDown", "Calendar unavailable", "FetchHolidays", 80, err_def("HolidaysDown", "Err_Holidays"), outputs=[("=holidayError", "holidayError")])
    p.script("WeekendsOnly", "Count weekends only", 470, Y + 140, '=[]', "holidays")
    p.gw("ClockMerge", "", 470, Y)
    p.rule("SetClockPolicy", "Set statutory clock policy", 580, Y, "CB_DisputeSlaPolicy", "clockPolicy",
           doc="Decision table owned by Consumer Relations Compliance: clock length, business or calendar days, furnisher window, regulation.")
    due = ('if clockPolicy.businessDays then ((for i in 1..(clockPolicy.slaDays * 2 + 25) return today() + duration("P" + string(i) + "D"))'
           '[not(list contains(["Saturday","Sunday"], day of week(item))) and not(list contains(holidays, string(item)))])[clockPolicy.slaDays] '
           'else today() + duration("P" + string(clockPolicy.slaDays) + "D")')
    clock_expr = ('={slaDays: clockPolicy.slaDays, businessDays: clockPolicy.businessDays, regulation: clockPolicy.regulation, '
                  f'receivedOn: string(today()), dueDate: string({due}), holidaysConsidered: count(holidays), '
                  f'warnAfter: {secs("clockPolicy.slaDays * 0.75", "clockSecondsPerDay")}, '
                  f'breachAfter: {secs("clockPolicy.slaDays", "clockSecondsPerDay")}, '
                  f'furnisherWindow: {secs("clockPolicy.furnisherWindowDays", "clockSecondsPerDay")}}}')
    p.script("StartClock", "Start statutory clock", 710, Y, clock_expr, "clock",
             outputs=[("=clock", "clock"), ('="ON_TRACK"', "slaStatus")])

    # ---------------------------------------------------------------- reinvestigation sub-process
    SX, SY, SW, SH = 790, 90, 1820, 610
    p.container("Reinvestigation", "Statutory reinvestigation", SX, SY, SW, SH,
                doc="Everything inside runs against the statutory clock. Boundary timers warn at 75% and escalate at the deadline.")
    P = "Reinvestigation"
    p.start("ReinvestigationStarted", "", 830, Y, parent=P)
    p.service("NotifyFurnisher", "Send dispute to furnisher", 940, Y, "cb.furnisher.send", parent=P)
    p.gw("AwaitFurnisher", "", 1060, Y, kind="eventBased", parent=P)
    p.catch_msg("FurnisherResponded", "Furnisher responded", 1140, Y, "Msg_FurnisherResponse", parent=P)
    p.catch_timer("FurnisherWindowElapsed", "Furnisher window elapsed", 1060, 610, "=clock.furnisherWindow", parent=P)

    # agent
    AX, AY, AW, AH = 1200, 130, 520, 400
    up = ('="Investigate dispute " + disputeId + " for " + consumerName + " (" + jurisdiction + ").\\n'
          'Dispute type: " + disputeType + ". Furnisher: " + furnisher + ", account " + accountRef + ".\\n'
          'Received via: " + channel + ".\\nConsumer statement: " + consumerStatement')
    up = up.replace("\\n", "\n")
    p.container("DisputeInvestigator", "Dispute investigator agent", AX, AY, AW, AH, tag="adHocSubProcess", attrs=AGENT_ATTRS,
                ext=agent_ext("disputeInvestigator", up, "investigator", "assessment"),
                doc="Investigates and recommends. It cannot decide: the approval authority table after it decides who signs off.", parent=P)
    A = "DisputeInvestigator"
    c1, c2, c3, c4 = 1280, 1410, 1540, 1660
    tool_script(p, "GetTradeline", "Get credit file tradeline",
                "Returns the disputed tradeline from the credit file: furnisher, account, reported status, balance, dates and 24-month payment history.",
                f"=get or else(get value({SYS}.creditFile, caseRef), {{error: \"No tradeline found\"}})", c1, 220, A)
    tool_script(p, "GetFurnisherResponse", "Get furnisher response",
                "Returns the furnisher's response to the dispute (ACDV): position AGREES_WITH_CONSUMER or VERIFIED, response code and detail.",
                "=furnisherResponse", c2, 220, A)
    tool_script(p, "GetConsumerEvidence", "Get consumer evidence",
                "Returns the documents the consumer supplied, with fields extracted by document processing, and documentStatus DOCUMENTED, PARTIAL or NONE.",
                f"=get or else(get value({SYS}.consumerEvidence, caseRef), {{documentStatus: \"NONE\", documents: []}})", c3, 220, A)
    tool_script(p, "CheckDisputeHistory", "Check dispute history",
                "Returns the consumer's disputes in the last 12 months and their outcomes.",
                f"=get or else(get value({SYS}.disputeHistory, consumerId), {{disputes12m: 0, items: []}})", c4, 220, A)
    tool_script(p, "CheckFraudSignals", "Check identity and fraud signals",
                "Returns identity verification status and fraud signals for the consumer from the identity resolution service.",
                f"=get or else(get value({SYS}.fraudSignals, consumerId), {{signals: [], identityVerified: false}})", c1, 340, A)
    tool_script(p, "LookUpDisputePolicy", "Look up dispute policy",
                "Returns the regulation and the policy text that governs disputes in a jurisdiction (US, ZA, IN or UK). Quote it.",
                f"=get or else(get value({SYS}.policies, jurisdictionCode), {{error: \"Unknown jurisdiction\"}})", c2, 340, A,
                inputs=[('=fromAi(toolCall.jurisdiction, "Jurisdiction code: US, ZA, IN or UK")', "jurisdictionCode")])
    p.rule("ScoreEvidence", "Score evidence (DMN)", c3, 340, "CB_EvidenceStrength", "toolCallResult", parent=A,
           doc="Applies the evidence strength table owned by Consumer Relations Quality. Its score is binding: return it unchanged as confidence.",
           inputs=[('=fromAi(toolCall.furnisherPosition, "Furnisher position from the furnisher response: AGREES_WITH_CONSUMER, VERIFIED or NO_RESPONSE")', "furnisherPosition"),
                   ('=fromAi(toolCall.consumerEvidence, "documentStatus from the consumer evidence: DOCUMENTED, PARTIAL or NONE")', "consumerEvidence"),
                   ('=fromAi(toolCall.fraudSignalCount, "Number of fraud signals returned by the fraud check", "number")', "fraudSignalCount")])
    p.user("AskSpecialist", "Ask a dispute specialist", c4, 340, "CB_SpecialistQuestionForm", groups="dispute-specialists", parent=A,
           doc="Sends one specific question to a dispute specialist and waits for the answer. Use only when the furnisher and the consumer evidence contradict each other.",
           inputs=[('=fromAi(toolCall.question, "One specific question for the specialist")', "agentQuestion")],
           outputs=[('={question: agentQuestion, answer: specialistAnswer, answeredBy: "dispute specialist"}', "toolCallResult")])
    p.close(A)
    p.boundary("InvestigatorOffRail", "Agent off rail", A, 470, err_def("InvestigatorOffRail", "Err_AgentOffRail"), parent=P,
               outputs=[("=agentErrorCode", "agentErrorCode")])
    p.script("InvestigatorFallback", "Send to senior analyst (no AI recommendation)", 1670, 610,
             '={recommendation: "NEEDS_HUMAN", confidence: 0, furnisherAgrees: false, fraudIndicators: false, '
             'summary: "The investigator agent stopped (" + get or else(agentErrorCode, "unknown") + "). Review the case directly.", evidence: [], consumerLetter: ""}',
             "assessment", parent=P)

    auth_in = [('=get or else(assessment.recommendation, "NEEDS_HUMAN")', "recommendation"),
               ("=get or else(assessment.confidence, 0)", "confidence"),
               ("=get or else(assessment.fraudIndicators, false)", "fraudIndicators"),
               ("=get or else(assessment.furnisherAgrees, false)", "furnisherAgrees")]
    auth_out = [("=authority", "authority"),
                ("=authority.route", "obsRoute"), ("=authority.ruleId", "obsAuthorityRule"),
                ('=get or else(assessment.recommendation, "NEEDS_HUMAN")', "obsRecommendation"),
                ("=get or else(assessment.confidence, 0)", "obsConfidence"),
                ('="NOT_REQUIRED"', "obsAnalystDecision"),
                ("=get or else(assessment.furnisherAgrees, false)", "obsFurnisherAgrees"),
                ("=get or else(assessment.fraudIndicators, false)", "obsFraudIndicators"),
                ("=if is defined(investigator) and investigator != null then get or else(investigator.context.metrics.modelCalls, 0) else 0", "obsModelCalls"),
                ("=if is defined(investigator) and investigator != null then get or else(investigator.context.metrics.tokenUsage.inputTokenCount, 0) + get or else(investigator.context.metrics.tokenUsage.outputTokenCount, 0) else 0", "obsTokens"),
                ("=camunda.vars.env.CB_PROMPTS.disputeInvestigator.version", "obsPromptVersion"),
                ("=camunda.vars.env.CB_GENAI.model", "obsModel")]
    p.rule("DetermineAuthority", "Determine approval authority", 1800, Y, "CB_DisputeApprovalAuthority", "authority",
           inputs=auth_in, outputs=auth_out, parent=P,
           doc="Approval authority table owned by Consumer Relations Quality. The agent never approves its own recommendation.")
    p.gw("RouteGateway", "Who signs off?", 1920, Y, default="Flow_Route_Review", parent=P)
    p.script("AutoApply", "Accept recommendation (within authority)", 2050, 190,
             '={outcome: assessment.recommendation, correction: assessment.correction, decidedBy: "Rule " + authority.ruleId, mode: "AUTO", letter: assessment.consumerLetter}',
             "decision", parent=P)
    review_in = [
        ('=assessment.recommendation', "aiRecommendation"), ('=string(assessment.confidence)', "aiConfidence"),
        ('=get or else(assessment.correction, "")', "aiCorrection"),
        ('=authority.ruleId + ": " + authority.reason', "authorityReason"),
        ('=get or else(assessment.summary, "")', "aiSummary"),
        ('=string join(for e in get or else(assessment.evidence, []) return e.source + ": " + e.finding, "\n")', "evidenceText"),
        ('=get or else(assessment.consumerLetter, "")', "consumerLetter"),
    ]
    review_out = [('={outcome: if analystDecision = "APPROVE" then assessment.recommendation else overrideOutcome, '
                   'correction: if analystDecision = "APPROVE" then assessment.correction else get or else(overrideReason, ""), '
                   'decidedBy: get or else(reviewerName, "Analyst"), mode: if analystDecision = "APPROVE" then "APPROVED" else "OVERRIDDEN", '
                   'reason: get or else(overrideReason, null), letter: consumerLetter}', "decision"),
                  ('=if analystDecision = "APPROVE" then "APPROVED" else "OVERRIDDEN"', "obsAnalystDecision"),
                  ("=get or else(overrideReason, null)", "obsOverrideReason")]
    review_due = 'replace(string(now() + duration(' + secs("authority.reviewHours", "clockSecondsPerHour") + ')), "@.*$", "Z")'
    p.user("ReviewRecommendation", "Review AI recommendation", 2050, Y, "CB_DisputeReviewForm", groups="=[authority.reviewGroup]",
           priority="=authority.priority", due="=" + review_due, inputs=review_in, outputs=review_out, parent=P,
           doc="The analyst sees the recommendation, the evidence and the rule that sent the case to them, and approves or overrides.")
    p.boundary("ReviewSlaMissed", "Review SLA missed", "ReviewRecommendation", 85,
               timer_def("ReviewSlaMissed", "=" + secs("authority.reviewHours", "clockSecondsPerHour")), interrupting=False, parent=P)
    p.service("EscalateReview", "Reassign to senior analysts, raise priority", 2190, 470, "cb.review.escalate", parent=P)
    p.end("ReviewEscalated", "Review escalated", 2310, 470, parent=P)
    p.script("MarkUnverified", "Delete unverified item (policy)", 1240, 610,
             '={outcome: "DELETE", correction: "Delete the item: the furnisher did not verify it within the window", decidedBy: "Policy " + clock.regulation, mode: "UNVERIFIED", letter: "We could not verify the information you disputed, so we have removed it from your credit file."}',
             "decision", outputs=[("=decision", "decision"), ('="UNVERIFIED_DELETE"', "obsRoute"), ('="NOT_REQUIRED"', "obsAnalystDecision")], parent=P)
    p.gw("DecisionMerge", "", 2190, Y, parent=P)
    p.service("UpdateCreditFile", "Update credit file", 2310, Y, "cb.creditfile.update", parent=P, retries=1,
              doc="Writes the outcome to the credit file database. A locked record fails the job: the incident shows in Operate and is retried from there.")
    p.service("SendResults", "Send results to consumer and furnisher", 2430, Y, "cb.notify",
              inputs=[('="DISPUTE_RESULT"', "notificationType")], parent=P)
    p.end("ReinvestigationDone", "", 2560, Y, parent=P)

    p.flow("ReinvestigationStarted", "NotifyFurnisher", parent=P)
    p.flow("NotifyFurnisher", "AwaitFurnisher", parent=P)
    p.flow("AwaitFurnisher", "FurnisherResponded", parent=P)
    p.flow("AwaitFurnisher", "FurnisherWindowElapsed", parent=P)
    p.flow("FurnisherResponded", "DisputeInvestigator", parent=P)
    p.flow("FurnisherWindowElapsed", "MarkUnverified", parent=P)
    p.flow("MarkUnverified", "DecisionMerge", parent=P, points=[(1295, 610), (2190, 610), (2190, Y + 25)])
    p.flow("DisputeInvestigator", "DetermineAuthority", parent=P, points=[(AX + AW, Y), (1745, Y)])
    p.flow("InvestigatorOffRail", "InvestigatorFallback", parent=P)
    p.flow("InvestigatorFallback", "DetermineAuthority", parent=P, points=[(1725, 610), (1800, 610), (1800, Y + 40)])
    p.flow("DetermineAuthority", "RouteGateway", parent=P)
    p.flow("RouteGateway", "AutoApply", name="Within authority", cond='=authority.route = "AUTO_APPLY"', parent=P)
    p.flow("RouteGateway", "ReviewRecommendation", name="Person signs off", parent=P, fid="Flow_Route_Review")
    p.flow("AutoApply", "DecisionMerge", parent=P, points=[(2105, 190), (2190, 190), (2190, Y - 25)])
    p.flow("ReviewRecommendation", "DecisionMerge", parent=P)
    p.flow("ReviewSlaMissed", "EscalateReview", parent=P)
    p.flow("EscalateReview", "ReviewEscalated", parent=P)
    p.flow("DecisionMerge", "UpdateCreditFile", parent=P)
    p.flow("UpdateCreditFile", "SendResults", parent=P)
    p.flow("SendResults", "ReinvestigationDone", parent=P)
    p.close(P)

    # statutory clock boundaries
    p.boundary("ClockWarning", "75% of statutory clock", P, 420, timer_def("ClockWarning", "=clock.warnAfter"), interrupting=False)
    p.service("AlertTeamLead", "Alert team lead, raise case priority", 1210, 800, "cb.sla.alert",
              inputs=[('="AT_RISK"', "newSlaStatus")])
    p.end("TeamLeadAlerted", "Team lead alerted", 1350, 800)
    p.boundary("ClockBreached", "Statutory deadline reached", P, 760, timer_def("ClockBreached", "=clock.breachAfter"), interrupting=False)
    p.service("MarkBreached", "Record deadline breach", 1550, 800, "cb.sla.alert", inputs=[('="BREACHED"', "newSlaStatus")])
    p.user("ComplianceBreachReview", "Compliance review of missed deadline", 1700, 800, "CB_StatutoryBreachForm", groups="compliance-officers", priority="=100")
    p.end("BreachReviewed", "Breach reviewed", 1840, 800)
    p.flow("ClockWarning", "AlertTeamLead")
    p.flow("AlertTeamLead", "TeamLeadAlerted")
    p.flow("ClockBreached", "MarkBreached")
    p.flow("MarkBreached", "ComplianceBreachReview")
    p.flow("ComplianceBreachReview", "BreachReviewed")

    p.send("RecordAchievement", "Record achievement for the analyst", 2700, Y, "cb.achievement.publish",
           inputs=[('="DISPUTE_RESOLVED"', "eventType"), ("=decision.decidedBy", "employee"),
                   ('=slaStatus != "BREACHED"', "withinSla"), ("=disputeId", "sourceRef")])
    p.end("DisputeResolved", "Dispute resolved", 2830, Y)

    p.flow("DisputeReceived", "VerifyIdentity")
    p.flow("VerifyIdentity", "FetchHolidays")
    p.flow("FetchHolidays", "ClockMerge")
    p.flow("HolidaysDown", "WeekendsOnly", points=[(390, Y + 40 + 18), (390, Y + 140), (415, Y + 140)])
    p.flow("WeekendsOnly", "ClockMerge")
    p.flow("ClockMerge", "SetClockPolicy")
    p.flow("SetClockPolicy", "StartClock")
    p.flow("StartClock", "Reinvestigation", points=[(765, Y), (SX, Y)])
    p.flow("Reinvestigation", "RecordAchievement", points=[(SX + SW, Y), (2645, Y)])
    p.flow("RecordAchievement", "DisputeResolved")

    # event sub-processes
    p.container("WithdrawnHandler", "Consumer withdraws dispute", 790, 920, 440, 170, attrs=' triggeredByEvent="true"')
    p.start("WithdrawnStart", "Dispute withdrawn", 850, 1005, parent="WithdrawnHandler", body=msg_def("WithdrawnStart", "Msg_Withdrawn"))
    p.script("CloseWithdrawn", "Close case as withdrawn", 1000, 1005, '={outcome: "WITHDRAWN", decidedBy: "Consumer", mode: "WITHDRAWN"}', "decision", parent="WithdrawnHandler")
    p.end("ClosedWithdrawn", "Closed as withdrawn", 1150, 1005, parent="WithdrawnHandler")
    p.flow("WithdrawnStart", "CloseWithdrawn", parent="WithdrawnHandler")
    p.flow("CloseWithdrawn", "ClosedWithdrawn", parent="WithdrawnHandler")
    p.close("WithdrawnHandler")
    p.container("ServiceIncidentHandler", "Service incident affects this case", 1290, 920, 440, 170, attrs=' triggeredByEvent="true"')
    p.start("ServiceIncidentStart", "Dispute service incident", 1350, 1005, parent="ServiceIncidentHandler",
            body=msg_def("ServiceIncidentStart", "Msg_ServiceIncident"), attrs=' isInterrupting="false"')
    p.script("FlagClockAtRisk", "Flag statutory clock at risk", 1500, 1005,
             '={incidentId: incidentRef, priority: incidentPriority, flaggedAt: string(now())}', "serviceIncident", parent="ServiceIncidentHandler")
    p.end("ClockFlagged", "Clock flagged", 1650, 1005, parent="ServiceIncidentHandler")
    p.flow("ServiceIncidentStart", "FlagClockAtRisk", parent="ServiceIncidentHandler")
    p.flow("FlagClockAtRisk", "ClockFlagged", parent="ServiceIncidentHandler")
    p.close("ServiceIncidentHandler")
    return p.xml()


# =============================================================================== 2. incident management
def incident():
    p = Proc("CB_IncidentManagement", "Incident management with AI triage",
             "A monitoring alert arrives by webhook. An AI agent triages; a severity matrix sets the priority and SLAs; responders acknowledge "
             "or the escalation ladder runs. Incidents on the dispute service flag the statutory clocks of open disputes.")
    p.defs += [error("Err_TriageOffRail", "Agent off the rails", "AGENT_OFF_RAIL")]
    Y = 330
    wh = ''.join(f'<zeebe:property name="{k}" value="{a(v)}" />' for k, v in [
        ("inbound.type", "io.camunda:webhook:1"), ("inbound.subtype", "ConfigurableInboundWebhook"), ("inbound.method", "post"),
        ("inbound.context", "cb-monitoring-alert"), ("inbound.shouldValidateHmac", "disabled"), ("inbound.auth.type", "NONE"),
        ("resultExpression", "={alert: request.body, incidentId: request.body.incidentId, service: request.body.service}"),
        ("inbound.responseExpression", "={body: {accepted: true, incidentId: request.body.incidentId}, statusCode: 202}"),
        ("deduplicationModeManualFlag", "false"), ("deduplicationMode", "AUTO"), ("consumeUnmatchedEvents", "true")])
    p.start("AlertReceived", "Monitoring alert received (webhook)", 90, Y, ext=f"<zeebe:properties>{wh}</zeebe:properties>",
            attrs=' zeebe:modelerTemplate="io.camunda.connectors.webhook.WebhookConnector.v1" zeebe:modelerTemplateVersion="14"')
    p.script("LookUpService", "Look up service in CMDB", 220, Y, f"=get value({SYS}.services, service)", "serviceInfo")
    AX, AY, AW, AH = 300, 130, 520, 400
    up = '="Triage alert " + incidentId + " on service " + service + " (" + serviceInfo.name + ", tier " + string(serviceInfo.tier) + ").\nAlert: " + alert.title + ". Source: " + alert.source + "."'
    p.container("TriageAgent", "Incident triage agent", AX, AY, AW, AH, tag="adHocSubProcess", attrs=AGENT_ATTRS,
                ext=agent_ext("incidentTriage", up, "triageAgent", "triage", max_calls=10),
                doc="Correlates health, changes, past incidents and runbooks. Recommends impact and urgency; the severity matrix decides.")
    A = "TriageAgent"
    tool_script(p, "GetServiceHealth", "Get service health",
                "Returns live SLO metrics for the service: p95 latency, error rate, trend and consumers affected.",
                f"=get value({SYS}.health, serviceId)", 380, 220, A,
                inputs=[('=fromAi(toolCall.serviceId, "Service id, for example dispute-intake-api")', "serviceId")])
    tool_script(p, "GetRecentChanges", "Get recent changes",
                "Returns changes deployed to the service recently, with change ids.",
                f"=get or else(get value({SYS}.changes, serviceId), [])", 510, 220, A,
                inputs=[('=fromAi(toolCall.serviceId, "Service id")', "serviceId")])
    tool_script(p, "SearchSimilarIncidents", "Search similar incidents",
                "Returns past incidents on the service with their resolution.",
                f"=get or else(get value({SYS}.incidentHistory, serviceId), [])", 640, 220, A,
                inputs=[('=fromAi(toolCall.serviceId, "Service id")', "serviceId")])
    tool_script(p, "GetRunbook", "Get runbook",
                "Returns the runbook for the service.",
                f"=get value({SYS}.runbooks, serviceId)", 380, 360, A,
                inputs=[('=fromAi(toolCall.serviceId, "Service id")', "serviceId")])
    p.user("AskOnCall", "Ask the on-call engineer", 510, 360, "CB_OnCallQuestionForm", groups="=[serviceInfo.resolverGroup]", parent=A,
           doc="Sends one question to the on-call engineer for the service and waits for the answer. Use only when the data contradict each other.",
           inputs=[('=fromAi(toolCall.question, "One specific question for the on-call engineer")', "agentQuestion")],
           outputs=[('={question: agentQuestion, answer: onCallAnswer}', "toolCallResult")])
    p.close(A)
    p.boundary("TriageOffRail", "Agent off rail", A, 470, err_def("TriageOffRail", "Err_TriageOffRail"), outputs=[("=agentErrorCode", "agentErrorCode")])
    p.script("ManualTriage", "Assume high impact (no AI triage)", 770, 610,
             '={impact: "HIGH", urgency: "HIGH", suspectedCause: "Unknown: triage agent stopped", linkedChange: "none", similarIncident: "none", firstActions: ["Triage manually"], summary: "The triage agent stopped; treated as high impact until a person triages.", confidence: 0}',
             "triage")
    sev_in = [('=get or else(triage.impact, "HIGH")', "impact"), ('=get or else(triage.urgency, "HIGH")', "urgency"),
              ("=serviceInfo.customerFacing", "customerFacing"), ("=serviceInfo.regulatoryService", "regulatoryService")]
    sev_out = [("=severity", "severity"), ("=severity.priority", "obsPriority"), ("=severity.ruleId", "obsSeverityRule"),
               ("=if is defined(triageAgent) and triageAgent != null then get or else(triageAgent.context.metrics.modelCalls, 0) else 0", "obsModelCalls"),
               ("=if is defined(triageAgent) and triageAgent != null then get or else(triageAgent.context.metrics.tokenUsage.inputTokenCount, 0) + get or else(triageAgent.context.metrics.tokenUsage.outputTokenCount, 0) else 0", "obsTokens"),
               ("=0", "escalationLevel"), ("=camunda.vars.env.CB_PROMPTS.incidentTriage.version", "obsPromptVersion"), ("=camunda.vars.env.CB_GENAI.model", "obsModel")]
    p.rule("SetSeverity", "Set severity and SLAs", 900, Y, "CB_IncidentSeverity", "severity", inputs=sev_in, outputs=sev_out,
           doc="Severity matrix owned by Global Operations: priority, acknowledge and resolve SLAs, escalation chain.")
    p.gw("DisputeImpactGw", "Statutory clocks at risk?", 1020, Y, default="Flow_NoDisputeImpact")
    p.service("FlagDisputes", "Flag open disputes at risk", 1130, 190, "cb.disputes.flag")
    p.gw("ImpactMerge", "", 1240, Y)
    p.service("NotifyResponders", "Page responders, open bridge", 1350, Y, "cb.notify", inputs=[('="INCIDENT_PAGE"', "notificationType")])
    ack_due = 'replace(string(now() + duration(' + secs("severity.ackMinutes", "clockSecondsPerMinute") + ')), "@.*$", "Z")'
    p.user("Acknowledge", "Acknowledge and take ownership", 1490, Y, "CB_IncidentAckForm", groups="=[serviceInfo.resolverGroup]",
           priority='=if severity.priority = "P1" then 100 else if severity.priority = "P2" then 80 else 50', due="=" + ack_due,
           inputs=[('=get or else(triage.summary, "")', "triageSummary"), ('=string join(get or else(triage.firstActions, []), "\n")', "firstActionsText"),
                   ("=severity.priority", "priorityShown")],
           outputs=[("=get or else(ackNote, \"\")", "ackNote"), ("=now()", "acknowledgedAt")])
    p.boundary("AckSlaMissed", "Acknowledge SLA missed", "Acknowledge", 85,
               timer_def("AckSlaMissed", '="R3/" + ' + secs("severity.ackMinutes", "clockSecondsPerMinute"), kind="timeCycle"), interrupting=False)
    p.service("EscalateIncident", "Escalate to next level", 1630, 470, "cb.incident.escalate")
    p.end("Escalated", "Escalated", 1760, 470)
    p.user("Resolve", "Resolve and confirm triage", 1640, Y, "CB_IncidentResolveForm", groups="=[serviceInfo.resolverGroup]",
           inputs=[('=get or else(triage.suspectedCause, "")', "suspectedCause"), ('=get or else(triage.linkedChange, "none")', "linkedChange")],
           outputs=[("={resolution: resolution, rootCause: rootCause, triageAccurate: triageAccurate}", "resolutionRecord"),
                    ("=triageAccurate", "obsTriageAccurate")])
    p.send("RecordIncidentAchievement", "Record achievement for the responder", 1780, Y, "cb.achievement.publish",
           inputs=[('="INCIDENT_RESOLVED"', "eventType"), ('=get or else(resolverName, "On-call engineer")', "employee"),
                   ("=true", "withinSla"), ("=incidentId", "sourceRef")])
    p.end("IncidentClosed", "Incident closed", 1910, Y)

    p.flow("AlertReceived", "LookUpService")
    p.flow("LookUpService", "TriageAgent", points=[(275, Y), (AX, Y)])
    p.flow("TriageAgent", "SetSeverity", points=[(AX + AW, Y), (845, Y)])
    p.flow("TriageOffRail", "ManualTriage")
    p.flow("ManualTriage", "SetSeverity", points=[(825, 610), (900, 610), (900, Y + 40)])
    p.flow("SetSeverity", "DisputeImpactGw")
    p.flow("DisputeImpactGw", "FlagDisputes", name="Yes", cond="=serviceInfo.regulatoryService and serviceInfo.customerFacing and (severity.priority = \"P1\" or severity.priority = \"P2\")")
    p.flow("DisputeImpactGw", "ImpactMerge", name="No", fid="Flow_NoDisputeImpact")
    p.flow("FlagDisputes", "ImpactMerge")
    p.flow("ImpactMerge", "NotifyResponders")
    p.flow("NotifyResponders", "Acknowledge")
    p.flow("AckSlaMissed", "EscalateIncident")
    p.flow("EscalateIncident", "Escalated")
    p.flow("Acknowledge", "Resolve")
    p.flow("Resolve", "RecordIncidentAchievement")
    p.flow("RecordIncidentAchievement", "IncidentClosed")
    return p.xml()


# =============================================================================== 3. learn loop
def improvement():
    p = Proc("CB_RuleImprovement", "Continuous improvement: tune the approval authority rule",
             "Observe, decide, execute, learn. Reads how analysts decided on AI recommendations, proposes one bounded change, "
             "a rule owner approves, and a new decision version deploys with no code change.")
    p.defs += [error("Err_CiOffRail", "Agent off the rails", "AGENT_OFF_RAIL")]
    Y = 330
    p.start("ReviewRequested", "Improvement review requested", 90, 250)
    p.start("WeeklyReview", "Every Monday 06:00", 90, 420, body=timer_def("WeeklyReview", "0 0 6 * * MON", kind="timeCycle"))
    p.gw("StartMerge", "", 180, Y)
    AX, AY, AW, AH = 260, 150, 420, 330
    up = '="Review the approval authority table CB_DisputeApprovalAuthority. Current autoApplyMinConfidence: " + string(camunda.vars.env.CB_POLICY.autoApplyMinConfidence) + "."'
    p.container("ImprovementAgent", "Improvement analyst agent", AX, AY, AW, AH, tag="adHocSubProcess", attrs=AGENT_ATTRS,
                ext=agent_ext("ruleImprovement", up, "improvementAgent", "proposal", max_calls=10))
    A = "ImprovementAgent"
    tool_script(p, "ReadChangePolicy", "Read change policy",
                "Returns the parameters of the authority table that may be tuned, their limits and the maximum step per release.",
                "=camunda.vars.env.CB_POLICY", 340, 250, A)
    p.service("GetOverrideStats", "Get override statistics", 470, 250, "cb.learn.stats", parent=A,
              doc="Returns, per confidence band and dispute type, how many cases analysts approved and overrode, from completed disputes in Operate.")
    p.service("BacktestThreshold", "Backtest threshold", 600, 250, "cb.learn.backtest", parent=A,
              doc="Replays completed disputes against a candidate autoApplyMinConfidence and returns how many would have auto-applied and how many of those an analyst overrode.",
              inputs=[('=fromAi(toolCall.candidateValue, "Candidate autoApplyMinConfidence, for example 0.85", "number")', "candidateValue")])
    p.close(A)
    p.boundary("CiOffRail", "Agent off rail", A, 380, err_def("CiOffRail", "Err_CiOffRail"))
    p.end("CiStopped", "No proposal", 640, 580)
    val = ('={ok: proposal.change = true and proposal.proposedValue >= camunda.vars.env.CB_POLICY.limits.min and proposal.proposedValue <= camunda.vars.env.CB_POLICY.limits.max '
           'and abs(proposal.proposedValue - camunda.vars.env.CB_POLICY.autoApplyMinConfidence) <= camunda.vars.env.CB_POLICY.limits.maxStep '
           'and get or else(proposal.backtest.overriddenAmongAffected, 1) = 0, '
           'checkedAgainst: "CB_POLICY limits " + string(camunda.vars.env.CB_POLICY.limits.min) + " to " + string(camunda.vars.env.CB_POLICY.limits.max) + ", max step " + string(camunda.vars.env.CB_POLICY.limits.maxStep)}')
    p.script("ValidateProposal", "Check proposal against change policy", 780, Y, val, "validation")
    p.gw("ValidGw", "Within policy?", 900, Y, default="Flow_Invalid")
    p.end("NoChange", "No change", 900, 520)
    p.user("RuleOwnerReview", "Rule owner approves change", 1030, Y, "CB_RuleChangeApprovalForm", groups="rule-owners", priority="=70",
           inputs=[("=proposal.parameter", "parameterShown"), ("=string(proposal.currentValue)", "currentShown"),
                   ("=string(proposal.proposedValue)", "proposedShown"), ("=proposal.rationale", "rationaleShown"),
                   ('="Cases affected: " + string(proposal.backtest.casesAffected) + ". Overridden among them: " + string(proposal.backtest.overriddenAmongAffected)', "backtestShown"),
                   ("=proposal.risk", "riskShown")],
           outputs=[("=ruleOwnerDecision", "ruleOwnerDecision"), ("=get or else(ruleOwnerComments, \"\")", "ruleOwnerComments")])
    p.gw("ApprovedGw", "Approved?", 1160, Y, default="Flow_Rejected")
    p.service("DeployDecision", "Deploy new decision version", 1290, Y, "cb.learn.deploy",
              doc="Writes the authority table with the approved value and deploys it as a new version. The previous version stays for in-flight cases and rollback.")
    p.send("RecogniseContributors", "Recognise contributing analysts", 1420, Y, "cb.achievement.publish",
           inputs=[('="IMPROVEMENT_ADOPTED"', "eventType"), ('=get or else(proposal.creditTo, "Dispute analysts team")', "employee"),
                   ("=true", "withinSla"), ('="CB_DisputeApprovalAuthority v" + string(deployedVersion)', "sourceRef")])
    p.end("NewVersionLive", "New rule version live", 1550, Y)
    p.end("ChangeRejected", "Change rejected", 1160, 520)
    p.flow("ReviewRequested", "StartMerge")
    p.flow("WeeklyReview", "StartMerge")
    p.flow("StartMerge", "ImprovementAgent", points=[(205, Y), (AX, Y)])
    p.flow("ImprovementAgent", "ValidateProposal", points=[(AX + AW, Y), (725, Y)])
    p.flow("CiOffRail", "CiStopped")
    p.flow("ValidateProposal", "ValidGw")
    p.flow("ValidGw", "RuleOwnerReview", name="Yes", cond="=validation.ok")
    p.flow("ValidGw", "NoChange", name="No", fid="Flow_Invalid")
    p.flow("RuleOwnerReview", "ApprovedGw")
    p.flow("ApprovedGw", "DeployDecision", name="Approve", cond='=ruleOwnerDecision = "APPROVE"')
    p.flow("ApprovedGw", "ChangeRejected", name="Reject", fid="Flow_Rejected")
    p.flow("DeployDecision", "RecogniseContributors")
    p.flow("RecogniseContributors", "NewVersionLive")
    return p.xml()


# =============================================================================== 4. recognition journey
def recognition():
    p = Proc("CB_RecognitionJourney", "Recognition journey: points, badges, leaderboard",
             "Every resolved case, SLA hit and adopted improvement emits an achievement event. Points come from a decision table, "
             "with a daily cap; badges above a threshold need a manager's approval.")
    p.defs += [message_nokey("Msg_Achievement", "cb-achievement")]
    Y = 300
    p.start("AchievementEvent", "Achievement event", 90, Y, body=msg_def("AchievementEvent", "Msg_Achievement"))
    p.service("ReadLedger", "Read points ledger", 220, Y, "cb.points.read")
    p.rule("ScorePoints", "Score event (DMN)", 360, Y, "CB_PointsScoring", "basePoints")
    p.script("ApplyCap", "Apply daily cap", 500, Y,
             "=min([get or else(basePoints, 0), max([0, camunda.vars.env.CB_RECOGNITION.dailyCap - pointsToday])])", "points")
    p.service("UpdateLedger", "Update points ledger", 640, Y, "cb.points.update")
    p.rule("CheckReward", "Check reward eligibility (DMN)", 780, Y, "CB_RewardEligibility", "reward")
    p.gw("BadgeGw", "Badge earned?", 900, Y, default="Flow_NoBadge")
    p.end("PointsRecorded", "Points recorded", 900, 460)
    p.gw("ApprovalGw", "Needs approval?", 1010, Y, default="Flow_NoApproval")
    p.user("ApproveReward", "Approve recognition reward", 1130, 190, "CB_RewardApprovalForm", groups="team-managers", priority="=40",
           outputs=[("=managerDecision", "managerDecision"), ("=get or else(managerNote, \"\")", "managerNote")])
    p.gw("RewardMerge", "", 1250, Y)
    p.service("AwardBadge", "Award badge, update leaderboard", 1370, Y, "cb.points.award")
    p.end("BadgeAwarded", "Badge awarded", 1500, Y)
    p.flow("AchievementEvent", "ReadLedger")
    p.flow("ReadLedger", "ScorePoints")
    p.flow("ScorePoints", "ApplyCap")
    p.flow("ApplyCap", "UpdateLedger")
    p.flow("UpdateLedger", "CheckReward")
    p.flow("CheckReward", "BadgeGw")
    p.flow("BadgeGw", "ApprovalGw", name="Yes", cond='=reward.badge != "NONE"')
    p.flow("BadgeGw", "PointsRecorded", name="No", fid="Flow_NoBadge")
    p.flow("ApprovalGw", "ApproveReward", name="Yes", cond="=reward.needsApproval")
    p.flow("ApprovalGw", "RewardMerge", name="No", fid="Flow_NoApproval")
    p.flow("ApproveReward", "RewardMerge")
    p.flow("RewardMerge", "AwardBadge")
    p.flow("AwardBadge", "BadgeAwarded")
    return p.xml()


# =============================================================================== DMN
def sla_policy():
    return dmn("CB_DisputeSlaPolicy", "Dispute statutory clock by jurisdiction", "FIRST",
               [("Jurisdiction", "jurisdiction", "string"), ("Dispute type", "disputeType", "string")],
               [("Clock (days)", "slaDays", "number"), ("Business days", "businessDays", "boolean"),
                ("Furnisher window (days)", "furnisherWindowDays", "number"), ("Regulation", "regulation", "string")],
               [("S-01", ['"US"', "-"], ["30", "false", "20", '"FCRA s611"'], "US: 30 days to reinvestigate"),
                ("S-02", ['"ZA"', "-"], ["20", "true", "12", '"NCA s72, Reg 20"'], "South Africa: 20 business days"),
                ("S-03", ['"IN"', "-"], ["30", "false", "21", '"RBI CIC framework 2023"'], "India: 30 days, 21 for the credit institution"),
                ("S-04", ['"UK"', "-"], ["28", "false", "18", '"CCA s159"'], "UK: 28 days"),
                ("S-99", ["-", "-"], ["30", "false", "20", '"Default policy"'], "Any other jurisdiction")])


def evidence_strength():
    return dmn("CB_EvidenceStrength", "Evidence strength", "FIRST",
               [("Furnisher position", "furnisherPosition", "string"), ("Consumer evidence", "consumerEvidence", "string"),
                ("Fraud signals", "fraudSignalCount", "number")],
               [("Evidence score", "evidenceScore", "number"), ("Band", "band", "string")],
               [("E-01", ["-", "-", ">= 2"], ["0.5", '"Conflicting: fraud indicators"'], "Fraud indicators override the furnisher"),
                ("E-02", ['"AGREES_WITH_CONSUMER"', '"DOCUMENTED"'], ["0.95", '"Strong"'], ""),
                ("E-03", ['"AGREES_WITH_CONSUMER"', '"PARTIAL"'], ["0.87", '"Good"'], ""),
                ("E-04", ['"AGREES_WITH_CONSUMER"', '"NONE"'], ["0.8", '"Good"'], ""),
                ("E-05", ['"VERIFIED"', '"NONE"'], ["0.92", '"Strong"'], "Furnisher verified, no contrary evidence"),
                ("E-06", ['"VERIFIED"', '"PARTIAL"'], ["0.85", '"Good"'], ""),
                ("E-07", ['"VERIFIED"', '"DOCUMENTED"'], ["0.6", '"Conflicting"'], "Furnisher and consumer documents disagree"),
                ("E-99", ["-", "-", "-"], ["0.5", '"Weak"'], "")])


def authority(threshold=0.9):
    return _authority(threshold)


def _authority(threshold):
    th = f"{threshold:g}"
    return dmn("CB_DisputeApprovalAuthority", "Dispute approval authority", "FIRST",
               [("Recommendation", "recommendation", "string"), ("Confidence", "confidence", "number"),
                ("Fraud indicators", "fraudIndicators", "boolean"), ("Furnisher agrees", "furnisherAgrees", "boolean"),
                ("Vulnerable consumer", "vulnerableConsumer", "boolean"), ("Dispute type", "disputeType", "string")],
               [("Route", "route", "string"), ("Review group", "reviewGroup", "string"), ("Review SLA (hours)", "reviewHours", "number"),
                ("Priority", "priority", "number"), ("Rule", "ruleId", "string"), ("Reason", "reason", "string")],
               [("A-01", ["-", "-", "true", "-", "-", "-"], ['"REVIEW"', '"fraud-compliance"', "24", "85", '"A-01"', '"Possible identity fraud: fraud and compliance decide"'], ""),
                ("A-02", ["-", "-", "-", "-", "true", "-"], ['"REVIEW"', '"senior-analysts"', "8", "75", '"A-02"', '"Vulnerable consumer: a senior analyst decides"'], ""),
                ("A-03", ['"NEEDS_HUMAN"', "-", "-", "-", "-", "-"], ['"REVIEW"', '"senior-analysts"', "8", "70", '"A-03"', '"No usable AI recommendation"'], ""),
                ("A-04", ["-", "< 0.7", "-", "-", "-", "-"], ['"REVIEW"', '"senior-analysts"', "8", "65", '"A-04"', '"Evidence conflicts (score below 0.7)"'], ""),
                ("A-05", ['"VERIFY_AS_REPORTED"', "-", "-", "-", "-", "-"], ['"REVIEW"', '"dispute-analysts"', "8", "55", '"A-05"', '"Outcome goes against the consumer: a person signs off"'], ""),
                ("A-06", ['"CORRECT","DELETE"', f">= {th}", "false", "true", "false", "-"], ['"AUTO_APPLY"', '"none"', "0", "0", '"A-06"', f'"Furnisher agrees and evidence score at least {th}: within authority"'], "Tunable: autoApplyMinConfidence"),
                ("A-07", ["-", "-", "-", "-", "-", "-"], ['"REVIEW"', '"dispute-analysts"', "8", "45", '"A-07"', '"Default: an analyst signs off"'], "")])


def severity():
    return dmn("CB_IncidentSeverity", "Incident severity matrix", "FIRST",
               [("Impact", "impact", "string"), ("Urgency", "urgency", "string"), ("Customer facing", "customerFacing", "boolean"),
                ("Regulatory service", "regulatoryService", "boolean")],
               [("Priority", "priority", "string"), ("Acknowledge (minutes)", "ackMinutes", "number"), ("Resolve (hours)", "resolveHours", "number"),
                ("Major incident", "majorIncident", "boolean"), ("Escalation chain", "escalationChain", "string"), ("Rule", "ruleId", "string")],
               [("I-01", ['"HIGH"', '"HIGH"', "-", "-"], ['"P1"', "15", "4", "true", '"sre-oncall,incident-managers,head-of-operations"', '"I-01"'], ""),
                ("I-02", ['"HIGH"', "-", "-", "true"], ['"P1"', "15", "4", "true", '"sre-oncall,incident-managers,head-of-operations"', '"I-02"'], "Regulatory service with high impact"),
                ("I-03", ['"HIGH"', '"MEDIUM","LOW"', "-", "-"], ['"P2"', "30", "8", "false", '"sre-oncall,incident-managers"', '"I-03"'], ""),
                ("I-04", ['"MEDIUM"', '"HIGH"', "true", "-"], ['"P2"', "30", "8", "false", '"sre-oncall,incident-managers"', '"I-04"'], ""),
                ("I-05", ['"MEDIUM"', "-", "-", "-"], ['"P3"', "120", "24", "false", '"service-owners,sre-oncall"', '"I-05"'], ""),
                ("I-99", ["-", "-", "-", "-"], ['"P4"', "480", "72", "false", '"service-owners"', '"I-99"'], "")])


def points():
    return dmn("CB_PointsScoring", "Recognition points", "COLLECT",
               [("Event type", "eventType", "string"), ("Within SLA", "withinSla", "boolean")],
               [("Points", "points", "number")],
               [("P-01", ['"DISPUTE_RESOLVED"', "-"], ["10"], "Resolved dispute"),
                ("P-02", ['"DISPUTE_RESOLVED"', "true"], ["5"], "Bonus: inside the statutory clock"),
                ("P-03", ['"INCIDENT_RESOLVED"', "-"], ["15"], "Resolved incident"),
                ("P-04", ['"INCIDENT_RESOLVED"', "true"], ["10"], "Bonus: inside SLA"),
                ("P-05", ['"IMPROVEMENT_ADOPTED"', "-"], ["50"], "Your decisions improved a rule")],
               aggregation="SUM")


def reward():
    return dmn("CB_RewardEligibility", "Reward eligibility", "FIRST",
               [("Previous total", "previousTotal", "number"), ("New total", "newTotal", "number")],
               [("Badge", "badge", "string"), ("Reward", "rewardName", "string"), ("Needs approval", "needsApproval", "boolean")],
               [("W-01", ["< 500", ">= 500"], ['"Gold: Consumer Champion"', '"Recognition award and team lunch"', "true"], ""),
                ("W-02", ["< 250", ">= 250"], ['"Silver: Clock Keeper"', '"Recognition award"', "true"], ""),
                ("W-03", ["< 100", ">= 100"], ['"Bronze: First Responder"', '"Badge on profile"', "false"], ""),
                ("W-99", ["-", "-"], ['"NONE"', '"none"', "false"], "")])


# =============================================================================== forms
def form(form_id, components):
    for i, c in enumerate(components):
        c.setdefault("id", f"{form_id}_f{i}")
    return json.dumps({"executionPlatform": "Camunda Cloud", "executionPlatformVersion": "8.10.0",
                       "exporter": {"name": "cb-camunda-demo build.py", "version": "1"}, "schemaVersion": 18,
                       "id": form_id, "type": "default", "components": components}, indent=2)


def ro(key, label, kind="textfield"):
    return {"type": kind, "key": key, "label": label, "readonly": True}


FORMS = {
    "CB_DisputeReviewForm": form("CB_DisputeReviewForm", [
        {"type": "text", "text": "## Dispute {{disputeId}}: {{consumerName}}\n{{disputeType}} on {{furnisher}} {{accountRef}}, {{jurisdiction}}. Statutory due date {{clock.dueDate}}."},
        ro("aiRecommendation", "AI recommendation"), ro("aiConfidence", "Evidence score"), ro("aiCorrection", "Proposed correction"),
        ro("authorityReason", "Why this needs your sign-off"), ro("aiSummary", "Investigator summary", "textarea"),
        ro("evidenceText", "Evidence cited", "textarea"),
        {"type": "documentPreview", "label": "Consumer evidence", "dataSource": "=evidenceDocuments", "maxHeight": 420,
         "conditional": {"hide": "=evidenceDocuments = null or count(evidenceDocuments) = 0"}},
        {"type": "radio", "key": "analystDecision", "label": "Your decision", "validate": {"required": True},
         "values": [{"label": "Approve the recommendation", "value": "APPROVE"}, {"label": "Override", "value": "OVERRIDE"}]},
        {"type": "select", "key": "overrideOutcome", "label": "Outcome instead", "conditional": {"hide": "=analystDecision != \"OVERRIDE\""},
         "values": [{"label": "Correct the item", "value": "CORRECT"}, {"label": "Delete the item", "value": "DELETE"},
                    {"label": "Verify as reported", "value": "VERIFY_AS_REPORTED"}]},
        {"type": "textarea", "key": "overrideReason", "label": "Reason for override", "conditional": {"hide": "=analystDecision != \"OVERRIDE\""}},
        {"type": "textarea", "key": "consumerLetter", "label": "Letter to the consumer"},
        {"type": "textfield", "key": "reviewerName", "label": "Your name"}]),
    "CB_SpecialistQuestionForm": form("CB_SpecialistQuestionForm", [
        {"type": "text", "text": "## The dispute investigator has a question\nDispute {{disputeId}}, {{consumerName}}"},
        ro("agentQuestion", "Question", "textarea"),
        {"type": "textarea", "key": "specialistAnswer", "label": "Your answer", "validate": {"required": True}}]),
    "CB_StatutoryBreachForm": form("CB_StatutoryBreachForm", [
        {"type": "text", "text": "## Statutory deadline reached\nDispute {{disputeId}} ({{jurisdiction}}, {{clock.regulation}}) passed its due date {{clock.dueDate}}."},
        {"type": "radio", "key": "complianceAction", "label": "Action", "validate": {"required": True},
         "values": [{"label": "Delete the disputed item now", "value": "DELETE_NOW"}, {"label": "Record exception and notify regulator log", "value": "LOG_EXCEPTION"}]},
        {"type": "textarea", "key": "complianceNotes", "label": "Notes"}]),
    "CB_OnCallQuestionForm": form("CB_OnCallQuestionForm", [
        {"type": "text", "text": "## The triage agent has a question\nIncident {{incidentId}} on {{service}}"},
        ro("agentQuestion", "Question", "textarea"),
        {"type": "textarea", "key": "onCallAnswer", "label": "Your answer", "validate": {"required": True}}]),
    "CB_IncidentAckForm": form("CB_IncidentAckForm", [
        {"type": "text", "text": "## {{priorityShown}}: {{alert.title}}\nIncident {{incidentId}}"},
        ro("triageSummary", "Triage summary", "textarea"), ro("firstActionsText", "Suggested first actions", "textarea"),
        {"type": "textarea", "key": "ackNote", "label": "Acknowledgement note"}]),
    "CB_IncidentResolveForm": form("CB_IncidentResolveForm", [
        {"type": "text", "text": "## Resolve {{incidentId}}\nSuspected cause from triage: {{suspectedCause}} (change {{linkedChange}})"},
        {"type": "textarea", "key": "resolution", "label": "What fixed it", "validate": {"required": True}},
        {"type": "textfield", "key": "rootCause", "label": "Root cause"},
        {"type": "radio", "key": "triageAccurate", "label": "Was the AI triage right?", "validate": {"required": True},
         "values": [{"label": "Yes", "value": "YES"}, {"label": "Partly", "value": "PARTLY"}, {"label": "No", "value": "NO"}]},
        {"type": "textfield", "key": "resolverName", "label": "Your name"}]),
    "CB_RuleChangeApprovalForm": form("CB_RuleChangeApprovalForm", [
        {"type": "text", "text": "## Proposed change to the dispute approval authority table"},
        ro("parameterShown", "Parameter"), ro("currentShown", "Current value"), ro("proposedShown", "Proposed value"),
        ro("rationaleShown", "Rationale", "textarea"), ro("backtestShown", "Backtest"), ro("riskShown", "Risk"),
        {"type": "radio", "key": "ruleOwnerDecision", "label": "Decision", "validate": {"required": True},
         "values": [{"label": "Approve and deploy", "value": "APPROVE"}, {"label": "Reject", "value": "REJECT"}]},
        {"type": "textarea", "key": "ruleOwnerComments", "label": "Comments"}]),
    "CB_RewardApprovalForm": form("CB_RewardApprovalForm", [
        {"type": "text", "text": "## Recognition reward for {{employee}}\n{{reward.badge}}: {{reward.rewardName}}. Total points {{newTotal}}."},
        {"type": "radio", "key": "managerDecision", "label": "Decision", "validate": {"required": True},
         "values": [{"label": "Approve", "value": "APPROVE"}, {"label": "Decline", "value": "DECLINE"}]},
        {"type": "textarea", "key": "managerNote", "label": "Note to the employee"}]),
}


def write_all(threshold):
    out = {
        "bpmn/CB_CreditDispute.bpmn": dispute(), "bpmn/CB_IncidentManagement.bpmn": incident(),
        "bpmn/CB_RuleImprovement.bpmn": improvement(), "bpmn/CB_RecognitionJourney.bpmn": recognition(),
        "dmn/CB_DisputeSlaPolicy.dmn": sla_policy(), "dmn/CB_EvidenceStrength.dmn": evidence_strength(),
        "dmn/CB_DisputeApprovalAuthority.dmn": authority(threshold), "dmn/CB_IncidentSeverity.dmn": severity(),
        "dmn/CB_PointsScoring.dmn": points(), "dmn/CB_RewardEligibility.dmn": reward(),
    }
    out.update({f"forms/{k}.form": v for k, v in FORMS.items()})
    for rel, body in out.items():
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(body)
        print("wrote", rel)


if __name__ == "__main__":
    th = float(sys.argv[sys.argv.index("--threshold") + 1]) if "--threshold" in sys.argv else 0.9
    write_all(th)
