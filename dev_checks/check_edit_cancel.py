import sys; import os; here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.join(here, "fake_frappe"), os.path.abspath(os.path.join(here, ".."))]
import frappe
from customer_portal import api
def reset():
    frappe.DB.clear(); frappe.local.response.clear()
    frappe.DB.update({
        "SO-DRAFT": dict(name="SO-DRAFT", customer="CUST-A", docstatus=0, utm_content="customer_portal_app", modified="2026-11-27 12:00:00", status="Draft", grand_total=1, rounded_total=1, currency="INR", delivery_date=None),
        "SO-SUB":   dict(name="SO-SUB", customer="CUST-A", docstatus=1, utm_content="customer_portal_app", modified="m"),
        "SO-STAFF": dict(name="SO-STAFF", customer="CUST-A", docstatus=0, utm_content=None, modified="m"),
        "SO-OTHER": dict(name="SO-OTHER", customer="CUST-B", docstatus=0, utm_content="customer_portal_app", modified="m"),
    })
items = [{"item_code": "I1", "qty": 2}]
def code(r): return (r.get("error_code"), frappe.local.response.get("http_status_code"))
checks = []
for nm, exp in [("SO-SUB", ("ORDER_NOT_EDITABLE", 409)), ("SO-STAFF", ("ORDER_NOT_FROM_APP", 409)),
                ("SO-OTHER", ("NOT_FOUND", 404)), ("NOPE", ("NOT_FOUND", 404))]:
    reset(); checks.append(("update "+nm, code(api.update_order(name=nm, delivery_date="2026-11-30", items=items)), exp))
    reset(); checks.append(("cancel "+nm, code(api.cancel_order(name=nm)), exp))
reset(); checks.append(("update stale", code(api.update_order(name="SO-DRAFT", delivery_date="2026-11-30", items=items, expected_modified="old")), ("ORDER_CHANGED", 409)))
reset(); checks.append(("update bad item", code(api.update_order(name="SO-DRAFT", delivery_date="2026-11-30", items=[{"item_code":"BAD","qty":1}])), ("ITEM_NOT_AVAILABLE", 400)))
reset(); checks.append(("update past date", code(api.update_order(name="SO-DRAFT", delivery_date="2026-11-01", items=items)), ("INVALID_DELIVERY_DATE", 400)))
reset(); r = api.update_order(name="SO-DRAFT", delivery_date="2026-11-30", items=items, po_no="PO1", notes="ring bell", expected_modified="2026-11-27 12:00:00")
checks.append(("update ok", (r["data"]["updated"], r["data"]["docstatus"], frappe.SAVED[-1]["po_no"], len(frappe.SAVED[-1]["items"])), (True, 0, "PO1", 1)))
checks.append(("session restored", frappe.session.user, "buyer@x"))
reset(); r = api.cancel_order(name="SO-DRAFT")
checks.append(("cancel ok", (r["data"]["cancelled"], frappe.DELETED[-1]), (True, "SO-DRAFT")))
bad = [c for c in checks if c[1] != c[2]]
for c in checks: print("PASS" if c[1]==c[2] else "FAIL", c)
print(len(checks)-len(bad), "/", len(checks))
