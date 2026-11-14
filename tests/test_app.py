"""End-to-end tests with a scripted model in place of Gemini.

Needs the database from `docker compose up -d`. The tests reset the demo tables.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

import main
from agent import build_graph

client = TestClient(main.app)


class Scripted(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def call(name, **args):
    return {"name": name, "args": args, "id": f"call-{name}-{len(args)}", "type": "tool_call"}


def use_script(*messages):
    main._graph = build_graph(main.checkpointer, Scripted(responses=list(messages)))


@pytest.fixture(autouse=True)
def fresh():
    client.post("/reset")
    yield
    main._graph = None


def status(invoice_id):
    return next(i for i in client.get("/invoices").json() if i["id"] == invoice_id)


def test_lists_invoices_with_purchase_order_and_vendor():
    invoices = client.get("/invoices").json()
    assert len(invoices) == 6 and all(i["status"] == "exception" and i["run"] is None for i in invoices)
    assert invoices[0]["po_unit_price"] == 540 and invoices[0]["unit_price"] == 545
    assert invoices[3]["po_item"] is None          # PO-1009 does not exist
    assert invoices[2]["vendor_status"] == "on_hold"


def test_approval_waits_for_the_reviewer():
    use_script(
        AIMessage("", tool_calls=[call("get_invoice", invoice_id=1), call("get_policy")]),
        AIMessage("", tool_calls=[call("get_purchase_order", po_number="PO-1001")]),
        AIMessage("", tool_calls=[call("resolve_invoice", invoice_id=1, status="approved", reason="0.9% over, Rs 1,000")],
                  usage_metadata={"input_tokens": 900, "output_tokens": 40, "total_tokens": 940}),
        AIMessage("Approved: within the 2% tolerance."),
    )
    r = client.post("/invoices/1/process").json()
    assert r["status"] == "awaiting_approval" and r["pending_action"]["status"] == "approved"
    assert status(1)["status"] == "exception"                       # nothing written yet
    assert status(1)["run"]["pending"]["reason"] == "0.9% over, Rs 1,000"
    assert client.post("/invoices/1/process").status_code == 409    # one pending action at a time

    done = client.post(f"/runs/{r['run_id']}/decision", json={"approved": True}).json()
    assert done == {"run_id": r["run_id"], "status": "done", "pending_action": None,
                    "summary": "Approved: within the 2% tolerance."}
    inv = status(1)
    assert inv["status"] == "approved" and inv["run"]["tokens"] == 940
    assert [s["tool"] for s in inv["run"]["steps"]] == ["get_invoice", "get_policy", "get_purchase_order", "resolve_invoice"]
    assert client.post(f"/runs/{r['run_id']}/decision", json={"approved": True}).status_code == 409


def test_declined_action_is_escalated_instead():
    use_script(
        AIMessage("", tool_calls=[call("resolve_invoice", invoice_id=2, status="rejected", reason="billed 5,600 of 5,000")]),
        AIMessage("", tool_calls=[call("escalate", invoice_id=2, reason="reviewer wants the goods receipt checked")]),
        AIMessage("Escalated."),
    )
    r = client.post("/invoices/2/process").json()
    client.post(f"/runs/{r['run_id']}/decision", json={"approved": False, "comment": "check the goods receipt"})
    inv = status(2)
    assert inv["status"] == "escalated" and "goods receipt" in inv["resolution_note"]


def test_pending_approval_survives_a_restart():
    script = [
        AIMessage("", tool_calls=[call("resolve_invoice", invoice_id=5, status="rejected", reason="duplicate")]),
        AIMessage("Rejected the duplicate."),
    ]
    use_script(*script)
    r = client.post("/invoices/5/process").json()
    use_script(script[1])                           # a new graph object, as after a redeploy
    done = client.post(f"/runs/{r['run_id']}/decision", json={"approved": True}).json()
    assert done["status"] == "done" and status(5)["status"] == "rejected"


def test_errors():
    assert client.post("/invoices/99/process").status_code == 404
    assert client.post("/runs/not-a-uuid/decision", json={"approved": True}).status_code == 422
    main._graph = None
    os.environ.pop("GOOGLE_API_KEY", None)
    assert client.post("/invoices/1/process").status_code == 503
    assert status(1)["run"] is None                 # the aborted run leaves nothing behind
