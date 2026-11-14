import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from pydantic import BaseModel

import db
from agent import MODEL, build_graph
from tools import POLICY

app = FastAPI(title="Invoice exception agent")
db.init()
checkpointer = PostgresSaver(db.pool)
checkpointer.setup()
_graph = None


def graph():
    global _graph
    if _graph is None:
        if not os.environ.get("GOOGLE_API_KEY"):
            raise HTTPException(503, "The server has no GOOGLE_API_KEY, so the agent cannot run.")
        _graph = build_graph(checkpointer)
    return _graph


class Decision(BaseModel):
    approved: bool
    comment: str | None = None


def advance(run_id, payload):
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 20}
    started = time.perf_counter()
    try:
        result = graph().invoke(payload, config)
    except HTTPException:
        db.query("delete from runs where id = %s and status = 'running'", (run_id,))
        raise
    except Exception as e:
        db.query("update runs set status = 'failed', summary = %s where id = %s", (str(e)[:500], run_id))
        raise HTTPException(502, f"The agent failed: {e}")
    elapsed = int((time.perf_counter() - started) * 1000)

    messages = result["messages"]
    steps = [
        {"tool": call["name"], "args": call["args"]}
        for m in messages
        for call in getattr(m, "tool_calls", [])
    ]
    tokens = sum((getattr(m, "usage_metadata", None) or {}).get("total_tokens", 0) for m in messages)

    interrupts = result.get("__interrupt__")
    pending = interrupts[0].value if interrupts else None
    status = "awaiting_approval" if pending else "done"
    summary = None if pending else messages[-1].text
    db.query(
        "update runs set status = %s, steps = %s, pending = %s, summary = %s, tokens = %s,"
        " latency_ms = latency_ms + %s where id = %s",
        (status, json.dumps(steps), json.dumps(pending) if pending else None, summary, tokens, elapsed, run_id),
    )
    return {"run_id": run_id, "status": status, "pending_action": pending, "summary": summary}


@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/config")
def config():
    return {"agent_ready": bool(_graph or os.environ.get("GOOGLE_API_KEY")), "model": MODEL, "policy": POLICY}


@app.get("/invoices")
def list_invoices():
    """Every invoice with its purchase order, vendor and most recent agent run."""
    invoices = db.query(
        "select i.id, i.invoice_number, i.po_number, i.item, i.quantity, i.unit_price::float,"
        " i.status, i.resolution_note, v.name as vendor, v.status as vendor_status,"
        " p.item as po_item, p.quantity as po_quantity, p.unit_price::float as po_unit_price,"
        " pv.name as po_vendor"
        " from invoices i join vendors v on v.id = i.vendor_id"
        " left join purchase_orders p on p.po_number = i.po_number"
        " left join vendors pv on pv.id = p.vendor_id order by i.id"
    )
    runs = db.query("select distinct on (invoice_id) * from runs order by invoice_id, created_at desc")
    latest = {r["invoice_id"]: r for r in runs}
    for inv in invoices:
        inv["run"] = latest.get(inv["id"])
    return invoices


@app.post("/invoices/{invoice_id}/process")
def process_invoice(invoice_id: int):
    if not db.query("select 1 from invoices where id = %s and status = 'exception'", (invoice_id,)):
        raise HTTPException(404, "no open exception for this invoice")
    if db.query("select 1 from runs where invoice_id = %s and status = 'awaiting_approval'", (invoice_id,)):
        raise HTTPException(409, "this invoice already has an action waiting for review")
    run_id = str(uuid4())
    db.query("insert into runs (id, invoice_id, status) values (%s, %s, 'running')", (run_id, invoice_id))
    return advance(run_id, {"messages": [("user", f"Invoice id {invoice_id} needs review.")]})


@app.post("/runs/{run_id}/decision")
def review(run_id: UUID, decision: Decision):
    rows = db.query("select status from runs where id = %s", (run_id,))
    if not rows:
        raise HTTPException(404, "run not found")
    if rows[0]["status"] != "awaiting_approval":
        raise HTTPException(409, "run is not waiting for a decision")
    return advance(str(run_id), Command(resume=decision.model_dump()))


@app.get("/runs/{run_id}")
def get_run(run_id: UUID):
    rows = db.query("select * from runs where id = %s", (run_id,))
    if not rows:
        raise HTTPException(404, "run not found")
    return rows[0]


@app.post("/reset")
def reset():
    """Put the six sample invoices back so the demo can be run again."""
    db.reset()
    return {"ok": True}
