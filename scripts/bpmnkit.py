"""Small BPMN writer: elements plus diagram shapes, with orthogonal edges routed from the shapes.

Positions are centres. Edges pick their exits from the element kinds (gateway sides, boundary bottoms).
"""
from xml.sax.saxutils import escape

SIZES = {"event": (36, 36), "task": (110, 80), "gateway": (50, 50)}


def a(s):
    """Escape for an XML attribute."""
    return escape(str(s), {'"': "&quot;", "\n": "&#10;"})


def t(s):
    return escape(str(s))


def io(inputs=(), outputs=()):
    if not inputs and not outputs:
        return ""
    parts = []
    for src, tgt in inputs:
        parts.append(f'<zeebe:input source="{a(src)}" target="{tgt}" />' if src is not None else f'<zeebe:input target="{tgt}" />')
    for src, tgt in outputs:
        parts.append(f'<zeebe:output source="{a(src)}" target="{tgt}" />')
    return "<zeebe:ioMapping>" + "".join(parts) + "</zeebe:ioMapping>"


class Proc:
    def __init__(self, pid, name, doc=""):
        self.pid, self.name, self.doc = pid, name, doc
        self.children = {None: []}   # parent id -> xml list
        self.bounds = {}             # id -> (x, y, w, h)
        self.kind = {}
        self.shapes, self.edges = [], []
        self.defs = []               # messages, errors, escalations at definitions level
        self.n = 0
        self.io = {}

    # ------------------------------------------------------------ shapes
    def _shape(self, eid, kind, cx, cy, w=None, h=None, label=True, expanded=None, label_pos="below"):
        dw, dh = SIZES.get(kind, (110, 80))
        w, h = w or dw, h or dh
        x0, y0 = cx - w / 2, cy - h / 2
        self.bounds[eid] = (x0, y0, w, h)
        self.kind[eid] = kind
        lab = ""
        if label and kind in ("event", "gateway"):
            lw = 110
            ly = cy + h / 2 + 4 if label_pos == "below" else cy - h / 2 - 34
            lab = f'<bpmndi:BPMNLabel><dc:Bounds x="{cx - lw / 2:.0f}" y="{ly:.0f}" width="{lw}" height="27" /></bpmndi:BPMNLabel>'
        ex = f' isExpanded="{expanded}"' if expanded is not None else ""
        self.shapes.append(f'<bpmndi:BPMNShape id="{eid}_di" bpmnElement="{eid}"{ex}><dc:Bounds x="{x0:.0f}" y="{y0:.0f}" width="{w:.0f}" height="{h:.0f}" />{lab}</bpmndi:BPMNShape>')

    def _add(self, parent, xml):
        self.children.setdefault(parent, []).append(xml)

    # ------------------------------------------------------------ elements
    def start(self, eid, name, cx, cy, parent=None, body="", ext="", attrs="", label_pos="below"):
        e = f"<bpmn:extensionElements>{ext}</bpmn:extensionElements>" if ext else ""
        self._add(parent, f'<bpmn:startEvent id="{eid}" name="{a(name)}"{attrs}>{e}__IO_{eid}__{body}</bpmn:startEvent>')
        self._shape(eid, "event", cx, cy, label_pos=label_pos)

    def end(self, eid, name, cx, cy, parent=None, body="", ext=""):
        e = f"<bpmn:extensionElements>{ext}</bpmn:extensionElements>" if ext else ""
        self._add(parent, f'<bpmn:endEvent id="{eid}" name="{a(name)}">{e}__IO_{eid}__{body}</bpmn:endEvent>')
        self._shape(eid, "event", cx, cy)

    def task(self, eid, name, cx, cy, tag="serviceTask", ext="", doc="", parent=None, attrs="", w=None, h=None):
        d = f"<bpmn:documentation>{t(doc)}</bpmn:documentation>" if doc else ""
        self._add(parent, f'<bpmn:{tag} id="{eid}" name="{a(name)}"{attrs}>{d}<bpmn:extensionElements>{ext}</bpmn:extensionElements>__IO_{eid}__</bpmn:{tag}>')
        self._shape(eid, "task", cx, cy, w=w, h=h)

    def service(self, eid, name, cx, cy, jobtype, inputs=(), outputs=(), headers=None, retries=3, **kw):
        hd = ""
        if headers:
            hd = "<zeebe:taskHeaders>" + "".join(f'<zeebe:header key="{k}" value="{a(v)}" />' for k, v in headers.items()) + "</zeebe:taskHeaders>"
        ext = f'<zeebe:taskDefinition type="{jobtype}" retries="{retries}" />{io(inputs, outputs)}{hd}'
        self.task(eid, name, cx, cy, "serviceTask", ext, **kw)

    def send(self, eid, name, cx, cy, jobtype, inputs=(), outputs=(), **kw):
        ext = f'<zeebe:taskDefinition type="{jobtype}" retries="3" />{io(inputs, outputs)}'
        self.task(eid, name, cx, cy, "sendTask", ext, **kw)

    def script(self, eid, name, cx, cy, expr, result, inputs=(), outputs=(), **kw):
        ext = f'<zeebe:script expression="{a(expr)}" resultVariable="{result}" />{io(inputs, outputs)}'
        self.task(eid, name, cx, cy, "scriptTask", ext, **kw)

    def rule(self, eid, name, cx, cy, decision, result, inputs=(), outputs=(), **kw):
        ext = f'<zeebe:calledDecision decisionId="{decision}" resultVariable="{result}" />{io(inputs, outputs)}'
        self.task(eid, name, cx, cy, "businessRuleTask", ext, **kw)

    def user(self, eid, name, cx, cy, form, groups=None, priority=None, due=None, inputs=(), outputs=(), **kw):
        ext = '<zeebe:userTask />'
        if form:
            ext += f'<zeebe:formDefinition formId="{form}" />'
        if groups:
            ext += f'<zeebe:assignmentDefinition candidateGroups="{a(groups)}" />'
        if priority is not None:
            ext += f'<zeebe:priorityDefinition priority="{a(priority)}" />'
        if due:
            ext += f'<zeebe:taskSchedule dueDate="{a(due)}" />'
        ext += io(inputs, outputs)
        self.task(eid, name, cx, cy, "userTask", ext, **kw)

    def gw(self, eid, name, cx, cy, kind="exclusive", default=None, parent=None, label_pos="above"):
        d = f' default="{default}"' if default else ""
        self._add(parent, f'<bpmn:{kind}Gateway id="{eid}" name="{a(name)}"{d}>__IO_{eid}__</bpmn:{kind}Gateway>')
        self._shape(eid, "gateway", cx, cy, label=bool(name), label_pos=label_pos)

    def catch_msg(self, eid, name, cx, cy, msg_ref, parent=None, outputs=()):
        e = f"<bpmn:extensionElements>{io((), outputs)}</bpmn:extensionElements>" if outputs else ""
        self._add(parent, f'<bpmn:intermediateCatchEvent id="{eid}" name="{a(name)}">{e}__IO_{eid}__<bpmn:messageEventDefinition id="{eid}_def" messageRef="{msg_ref}" /></bpmn:intermediateCatchEvent>')
        self._shape(eid, "event", cx, cy)

    def catch_timer(self, eid, name, cx, cy, duration, parent=None):
        self._add(parent, f'<bpmn:intermediateCatchEvent id="{eid}" name="{a(name)}">__IO_{eid}__<bpmn:timerEventDefinition id="{eid}_def"><bpmn:timeDuration xsi:type="bpmn:tFormalExpression">{t(duration)}</bpmn:timeDuration></bpmn:timerEventDefinition></bpmn:intermediateCatchEvent>')
        self._shape(eid, "event", cx, cy)

    def boundary(self, eid, name, host, dx, definition, interrupting=True, parent=None, outputs=(), side="bottom"):
        x0, y0, w, h = self.bounds[host]
        cx = x0 + dx
        cy = y0 + h if side == "bottom" else y0
        ci = "" if interrupting else ' cancelActivity="false"'
        e = f"<bpmn:extensionElements>{io((), outputs)}</bpmn:extensionElements>" if outputs else ""
        self._add(parent, f'<bpmn:boundaryEvent id="{eid}" name="{a(name)}"{ci} attachedToRef="{host}">{e}__IO_{eid}__{definition}</bpmn:boundaryEvent>')
        self._shape(eid, "event", cx, cy)
        self.kind[eid] = "boundary"

    def container(self, eid, name, x0, y0, w, h, tag="subProcess", attrs="", ext="", doc="", parent=None):
        """Registers an expanded container. Its children are added with parent=eid; call close() to emit."""
        self.children.setdefault(eid, [])
        self._pending = getattr(self, "_pending", {})
        self._pending[eid] = (tag, name, attrs, ext, doc, parent)
        self.bounds[eid] = (x0, y0, w, h)
        self.kind[eid] = "container"
        self.shapes.append(f'<bpmndi:BPMNShape id="{eid}_di" bpmnElement="{eid}" isExpanded="true"><dc:Bounds x="{x0}" y="{y0}" width="{w}" height="{h}" /></bpmndi:BPMNShape>')

    def close(self, eid):
        tag, name, attrs, ext, doc, parent = self._pending.pop(eid)
        d = f"<bpmn:documentation>{t(doc)}</bpmn:documentation>" if doc else ""
        e = f"<bpmn:extensionElements>{ext}</bpmn:extensionElements>" if ext else ""
        inner = "\n".join(self.children.pop(eid))
        self._add(parent, f'<bpmn:{tag} id="{eid}" name="{a(name)}"{attrs}>{d}{e}__IO_{eid}__\n{inner}\n</bpmn:{tag}>')

    # ------------------------------------------------------------ flows
    def flow(self, src, tgt, name=None, cond=None, parent=None, points=None, fid=None):
        self.n += 1
        fid = fid or f"Flow_{src}_{tgt}"
        self.io.setdefault(src, [[], []])[1].append(fid)
        self.io.setdefault(tgt, [[], []])[0].append(fid)
        c = f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{t(cond)}</bpmn:conditionExpression>' if cond else ""
        nm = f' name="{a(name)}"' if name else ""
        self._add(parent, f'<bpmn:sequenceFlow id="{fid}"{nm} sourceRef="{src}" targetRef="{tgt}">{c}</bpmn:sequenceFlow>')
        pts = points or self._route(src, tgt)
        wp = "".join(f'<di:waypoint x="{px:.0f}" y="{py:.0f}" />' for px, py in pts)
        lab = ""
        if name:
            (x1, y1), (x2, y2) = pts[0], pts[1]
            if abs(x1 - x2) < 1:   # vertical first segment: label to the right of it
                lx, ly = x1 + 6, (y1 + y2) / 2 - 10 if abs(y2 - y1) > 40 else y1 + (8 if y2 > y1 else -30)
            else:
                lx, ly = x1 + 8, y1 - 22
            lab = f'<bpmndi:BPMNLabel><dc:Bounds x="{lx:.0f}" y="{ly:.0f}" width="90" height="14" /></bpmndi:BPMNLabel>'
        self.edges.append(f'<bpmndi:BPMNEdge id="{fid}_di" bpmnElement="{fid}">{wp}{lab}</bpmndi:BPMNEdge>')
        return fid

    def _route(self, src, tgt):
        sx, sy, sw, sh = self.bounds[src]
        tx, ty, tw, th = self.bounds[tgt]
        scx, scy, tcx, tcy = sx + sw / 2, sy + sh / 2, tx + tw / 2, ty + th / 2
        sk, tk = self.kind[src], self.kind[tgt]
        if sk == "boundary":
            if abs(tcx - scx) < 2:
                return [(scx, sy + sh), (tcx, ty if ty > scy else ty + th)]
            return [(scx, sy + sh), (scx, tcy), (tx if tx > scx else tx + tw, tcy)]
        if abs(scy - tcy) < 2:
            if tx >= sx + sw:
                return [(sx + sw, scy), (tx, tcy)]
            return [(scx, sy + sh), (scx, sy + sh + 40), (tcx, sy + sh + 40), (tcx, ty + th)]
        if sk == "gateway" and tx > scx:
            y0 = sy if tcy < scy else sy + sh
            if abs(tcx - scx) < 2:
                return [(scx, y0), (tcx, ty + th if tcy < scy else ty)]
            return [(scx, y0), (scx, tcy), (tx, tcy)]
        if tk == "gateway" and tcx > sx + sw:
            return [(sx + sw, scy), (tcx, scy), (tcx, ty + th if scy > tcy else ty)]
        if abs(tcx - scx) < 2:
            return [(scx, sy + sh if tcy > scy else sy), (tcx, ty if tcy > scy else ty + th)]
        mx = (sx + sw + tx) / 2
        return [(sx + sw, scy), (mx, scy), (mx, tcy), (tx, tcy)]

    # ------------------------------------------------------------ output
    def xml(self, exporter="cb-camunda-demo build.py"):
        for eid in list(getattr(self, "_pending", {})):
            self.close(eid)
        body = "\n".join(self.children[None])
        import re as _re

        def _fill(m):
            inc, out = self.io.get(m.group(1), [[], []])
            return "".join(f"<bpmn:incoming>{f}</bpmn:incoming>" for f in inc) + "".join(f"<bpmn:outgoing>{f}</bpmn:outgoing>" for f in out)
        body = _re.sub(r"__IO_([A-Za-z0-9_]+?)__", _fill, body)
        doc = f"<bpmn:documentation>{t(self.doc)}</bpmn:documentation>" if self.doc else ""
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:zeebe="http://camunda.org/schema/zeebe/1.0" xmlns:modeler="http://camunda.org/schema/modeler/1.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="Defs_{self.pid}" targetNamespace="http://camunda.org/schema/1.0/bpmn" exporter="{exporter}" modeler:executionPlatform="Camunda Cloud" modeler:executionPlatformVersion="8.10.0">
{chr(10).join(self.defs)}
<bpmn:process id="{self.pid}" name="{a(self.name)}" isExecutable="true">{doc}
{body}
</bpmn:process>
<bpmndi:BPMNDiagram id="Diagram_{self.pid}"><bpmndi:BPMNPlane id="Plane_{self.pid}" bpmnElement="{self.pid}">
{chr(10).join(self.shapes)}
{chr(10).join(self.edges)}
</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>
</bpmn:definitions>
'''


def timer_def(eid, expr, kind="timeDuration"):
    return f'<bpmn:timerEventDefinition id="{eid}_def"><bpmn:{kind} xsi:type="bpmn:tFormalExpression">{t(expr)}</bpmn:{kind}></bpmn:timerEventDefinition>'


def msg_def(eid, ref):
    return f'<bpmn:messageEventDefinition id="{eid}_def" messageRef="{ref}" />'


def err_def(eid, ref=None):
    r = f' errorRef="{ref}"' if ref else ""
    return f'<bpmn:errorEventDefinition id="{eid}_def"{r} />'


def message(mid, name, key):
    return f'<bpmn:message id="{mid}" name="{name}"><bpmn:extensionElements><zeebe:subscription correlationKey="{a(key)}" /></bpmn:extensionElements></bpmn:message>'


def message_nokey(mid, name):
    return f'<bpmn:message id="{mid}" name="{name}" />'


def error(eid, name, code):
    return f'<bpmn:error id="{eid}" name="{a(name)}" errorCode="{code}" />'


# ---------------------------------------------------------------- DMN
def dmn(dec_id, name, hit, inputs, outputs, rules, aggregation=None):
    ins = "".join(f'<input id="In_{dec_id}_{i}" label="{a(lbl)}"><inputExpression id="InExpr_{dec_id}_{i}" typeRef="{ty}"><text>{t(expr)}</text></inputExpression></input>'
                  for i, (lbl, expr, ty) in enumerate(inputs))
    outs = "".join(f'<output id="Out_{dec_id}_{i}" label="{a(lbl)}" name="{nm}" typeRef="{ty}" />' for i, (lbl, nm, ty) in enumerate(outputs))
    rs = []
    for r, (rid, ie, oe, ann) in enumerate(rules):
        rs.append(f'<rule id="{rid}"><description>{t(ann)}</description>' +
                  "".join(f'<inputEntry id="{rid}_i{i}"><text>{t(v)}</text></inputEntry>' for i, v in enumerate(ie)) +
                  "".join(f'<outputEntry id="{rid}_o{i}"><text>{t(v)}</text></outputEntry>' for i, v in enumerate(oe)) + "</rule>")
    ag = f' aggregation="{aggregation}"' if aggregation else ""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" xmlns:dmndi="https://www.omg.org/spec/DMN/20191111/DMNDI/" xmlns:dc="http://www.omg.org/spec/DMN/20180521/DC/" xmlns:modeler="http://camunda.org/schema/modeler/1.0" id="Defs_{dec_id}" name="{a(name)}" namespace="http://camunda.org/schema/1.0/dmn" modeler:executionPlatform="Camunda Cloud" modeler:executionPlatformVersion="8.10.0">
  <decision id="{dec_id}" name="{a(name)}">
    <decisionTable id="DT_{dec_id}" hitPolicy="{hit}"{ag}>
      {ins}
      {outs}
      {"".join(rs)}
    </decisionTable>
  </decision>
  <dmndi:DMNDI><dmndi:DMNDiagram id="DD_{dec_id}"><dmndi:DMNShape id="DS_{dec_id}" dmnElementRef="{dec_id}"><dc:Bounds height="80" width="180" x="160" y="100" /></dmndi:DMNShape></dmndi:DMNDiagram></dmndi:DMNDI>
</definitions>
'''
