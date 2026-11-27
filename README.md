# customer_portal

A small Frappe app for the Hari Bhog customer portal (ERPNext 16 / Frappe 16).

It provides three endpoints. Hooks in `hooks.py` serve them at the URLs the mobile app uses:

| URL (`POST /api/method/...`) | Function | Since |
|---|---|---|
| `customer_portal_create_order` | `customer_portal.api.create_order`: create a **Draft** order | 1.0.0 |
| `customer_portal_update_order` | `customer_portal.api.update_order`: edit the customer's own app Draft | 1.2.0 |
| `customer_portal_cancel_order` | `customer_portal.api.cancel_order`: cancel (delete) the customer's own app Draft | 1.2.0 |

Why it exists: ERPNext 16 checks **Item** (and **Account**) read permission for the logged-in user while it
validates a Sales Order, even when the order is saved with `ignore_permissions`. Customer portal users must not
read Item or Account, because that would expose buying rates, suppliers and the chart of accounts. This endpoint
does all checks as the portal user, then saves the **Draft** as an internal user, so ERPNext's full validation
runs.

The other portal endpoints (`customer_portal_get_profile`, `_get_dashboard`, `_list_orders`, `_get_order`,
`_list_invoices`, `_get_invoice`, `_list_items`) stay as Server Scripts on the site.

## What the endpoint enforces
- Identity comes from the session: `Contact.user` → Dynamic Link → Customer, cross-checked with the user's User
  Permission on Customer. Only Website Users with the Customer role are accepted.
- Company rule (a): the single company in the customer's submitted Sales Orders and Sales Invoices.
- Items must be enabled, sellable stock items with an Item Default for that company and a valid **Standard
  Selling** price. Rates come from the server. Any customer, company, rate or price list sent by the client is
  ignored.
- 1 to 50 lines, whole-number qty from 1 to 10000, delivery_date between today and today + 90 days.
- Always saved as a **Draft** (1.2.0). Staff review and submit it in ERPNext. Orders are tagged
  `utm_content = customer_portal_app` and get a comment naming the portal user and their notes.
- Duplicate protection: the app sends one `client_request_key` per cart. A repeat of the same key from the same
  user (for example a retry after a timeout) returns the order already created. Keys are kept for 24 hours.
- POST only. Rate limit is 120 requests per client IP per hour.
- Failures are rolled back, written to the **Error Log** ("Customer portal order failed") and returned as
  `{"error_code": "ORDER_FAILED", "message": ...}` with HTTP 422.

## Draft only (1.2.0)
Orders from the app are always saved as **Draft**. The app never submits them. Staff open the Draft in ERPNext, check it
and submit it. Version 1.1.0's site config key `customer_portal_auto_submit` is no longer read, so you can remove it
from Site Config (leaving it there does nothing).

## Edit and cancel (1.2.0)
- They only work on the logged-in customer's **own Draft** orders that were **placed in the app**
  (`utm_content = customer_portal_app`):
  - Another customer's order returns 404 `NOT_FOUND`, so its existence isn't revealed.
  - Submitted or cancelled orders return 409 `ORDER_NOT_EDITABLE` ("This order has already been processed and
    can no longer be changed.").
  - Drafts created by staff return 409 `ORDER_NOT_FROM_APP`.
- **Edit** replaces the lines, delivery date and PO number with the same checks and server prices as create. The
  order stays Draft, and a comment "Edited via customer portal app" (with any notes) is added.
- **Cancel** deletes the Draft, because Frappe can't cancel a Draft. Frappe keeps a full copy under **Deleted
  Documents**, so you can see and restore it there.
- `expected_modified` (optional): if the order changed since the app loaded it, the server returns 409
  `ORDER_CHANGED`. The response of create/update now includes `modified`.
- Rate limits are 120 updates and 60 cancels per client IP per hour. Failures are rolled back and logged in
  the Error Log ("Customer portal order update failed" / "... cancel failed").
- Offline logic check without a bench: `python3 -I dev_checks/check_edit_cancel.py` (14 checks).

## Optional setting
The order is saved as `Administrator` by default. To use a dedicated internal user instead, which makes the audit
trail clearer, create an enabled **System User** that can create Sales Orders (for example with the roles Sales
User, Stock User and Accounts User). Then set it in site config:

    bench --site <site> set-config customer_portal_order_user "portal-orders@yourdomain.com"

On Frappe Cloud: Site → Site Config → add the key `customer_portal_order_user`.

## Deploy on Frappe Cloud
1. Create a GitHub repository (private is fine), for example `customer_portal`, and push this folder to its root:

       git init && git add . && git commit -m "customer_portal 1.0.0"
       git branch -M main
       git remote add origin git@github.com:<you>/customer_portal.git
       git push -u origin main

2. Frappe Cloud dashboard → **Benches** (bench group) of site `apex-khb-demo` → **Apps** → **Add App** →
   **GitHub**. Install the Frappe Cloud GitHub app on the repo if asked, then pick the repo and branch `main`.
3. Click **Deploy** on the bench group and wait for the build to finish. You need a private bench group;
   shared benches can't take custom apps.
4. Site → **Apps** → **Install App** → `customer_portal`. No migrations or doctypes are added.
5. Re-run step 10 of `verify_customer_portal.ps1`. Expect `200` with a Draft `SAL-ORD-…`.

## Uninstall / rollback
Site → Apps → uninstall `customer_portal`. The URL `customer_portal_create_order` then falls back to the
existing Server Script, which refuses safely because of the Item permission check. Draft orders created through
the app stay in ERPNext. You can find them by `utm_content = customer_portal_app`.
