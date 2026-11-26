# customer_portal

A small Frappe app for the Hari Bhog customer portal (ERPNext 16 / Frappe 16).

It provides one endpoint, `customer_portal.api.create_order`. A hook in `hooks.py` also serves it at the URL the
mobile app already uses, so `POST /api/method/customer_portal_create_order` keeps working unchanged.

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
- Saved as a Draft, or **submitted right away** when auto-submit is on (see below). Orders are tagged
  `utm_content = customer_portal_app` and get a comment naming the portal user and their notes.
- Duplicate protection: the app sends one `client_request_key` per cart. A repeat of the same key from the same
  user (for example a retry after a timeout) returns the order already created. Keys are kept for 24 hours.
- POST only. Rate limit is 120 requests per client IP per hour.
- Failures are rolled back, written to the **Error Log** ("Customer portal order failed") and returned as
  `{"error_code": "ORDER_FAILED", "message": ...}` with HTTP 422.

## Auto-submit (site config)
Off by default: orders stay **Draft** for staff review. To submit orders straight from the app, open Frappe Cloud
→ Site → **Site Config** → add the key `customer_portal_auto_submit` with value `1`, or run:

    bench --site <site> set-config customer_portal_auto_submit 1

It takes effect immediately; no redeploy is needed. If ERPNext's submit checks fail (credit limit, frozen
customer, …), the order is kept as a Draft. The app shows "awaiting review" with the reason, and the Error Log
gets "Customer portal auto-submit failed (kept as Draft)". Set the key to `0` to go back to Draft-only.

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
