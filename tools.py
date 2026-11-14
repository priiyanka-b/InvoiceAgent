from pathlib import Path
from typing import Literal

from langchain_core.tools import tool
from langgraph.types import interrupt

from db import query

POLICY = (Path(__file__).parent / "policy.md").read_text()


@tool
def get_invoice(invoice_id: int) -> dict:
    """Fetch an invoice, plus ids of other invoices from the same vendor with the same invoice number."""
    rows = query(
        "select id, invoice_number, vendor_id, po_number, item, quantity,"
        " unit_price::float, status from invoices where id = %s",
        (invoice_id,),
    )
    if not rows:
        return {"error": "invoice not found"}
    inv = rows[0]
    dupes = query(
        "select id from invoices where invoice_number = %s and vendor_id = %s and id <> %s",
        (inv["invoice_number"], inv["vendor_id"], invoice_id),
    )
    inv["duplicate_ids"] = [d["id"] for d in dupes]
    return inv


@tool
def get_purchase_order(po_number: str) -> dict:
    """Fetch a purchase order by its number."""
    rows = query(
        "select po_number, vendor_id, item, quantity, unit_price::float"
        " from purchase_orders where po_number = %s",
        (po_number,),
    )
    return rows[0] if rows else {"error": "purchase order not found"}


@tool
def get_vendor(vendor_id: int) -> dict:
    """Fetch a vendor's name and status."""
    rows = query("select id, name, status from vendors where id = %s", (vendor_id,))
    return rows[0] if rows else {"error": "vendor not found"}


@tool
def get_policy() -> str:
    """Return the invoice matching policy."""
    return POLICY


@tool
def resolve_invoice(invoice_id: int, status: Literal["approved", "rejected"], reason: str) -> str:
    """Approve or reject an invoice. A human reviewer has to confirm before it is applied."""
    decision = interrupt({"invoice_id": invoice_id, "status": status, "reason": reason})
    if not decision["approved"]:
        return f"Reviewer declined this action. Comment: {decision.get('comment') or 'none'}"
    query(
        "update invoices set status = %s, resolution_note = %s where id = %s",
        (status, reason, invoice_id),
    )
    return f"Invoice {invoice_id} marked {status}."


@tool
def escalate(invoice_id: int, reason: str) -> str:
    """Hand the invoice to a human with the reason it could not be resolved."""
    query(
        "update invoices set status = 'escalated', resolution_note = %s where id = %s",
        (reason, invoice_id),
    )
    return f"Invoice {invoice_id} escalated."


TOOLS = [get_invoice, get_purchase_order, get_vendor, get_policy, resolve_invoice, escalate]
