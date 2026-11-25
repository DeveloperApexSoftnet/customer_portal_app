"""Customer portal API: order creation for portal (Website) users.

Why this is an app and not a Server Script:
ERPNext 16 checks Item read permission (item.check_permission()) and Account
permission for the *session user* while it validates a Sales Order, even when
the order is saved with ignore_permissions. Portal users must not get read
access to Item or Account, because that exposes buying rates, suppliers and the
chart of accounts. So this endpoint checks everything as the portal user first,
then saves the Draft as an internal user, the same pattern Frappe's own webshop
uses.

Security rules kept here:
- The customer is derived from the session (Contact.user -> Dynamic Link ->
  Customer) and cross-checked against the user's User Permission. Request
  parameters are never used for identity.
- Customer, company, currency, price list and rates come from the server. Any
  such values sent by the client are ignored.
- Only items set up for the customer's company with a valid Standard Selling
  price can be ordered.
- The order is saved as a Draft (docstatus 0) and never submitted.
"""

from contextlib import contextmanager

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, escape_html, flt, getdate, nowdate, strip_html

PRICE_LIST = "Standard Selling"
MAX_LINES = 50
MAX_QTY = 10000
MAX_DELIVERY_DAYS = 90
SOURCE_TAG = "customer_portal_app"


class PortalError(Exception):
	def __init__(self, error_code, message, http_status=400):
		super().__init__(message)
		self.error_code = error_code
		self.message = message
		self.http_status = http_status


def _error_response(err):
	frappe.local.response["http_status_code"] = err.http_status
	return {"error_code": err.error_code, "message": err.message}


def _resolve_customer():
	"""Return (user, customer, contact) for the logged-in portal user, or raise PortalError."""
	user = frappe.session.user
	if not user or user == "Guest":
		raise PortalError("NOT_LOGGED_IN", "Please log in first.", 401)
	if frappe.db.get_value("User", user, "user_type") != "Website User" or "Customer" not in frappe.get_roles(
		user
	):
		raise PortalError("NOT_PORTAL_USER", "This endpoint is only for customer portal users.", 403)

	rows = frappe.db.sql(
		"""select dl.link_name, c.name from `tabContact` c
		join `tabDynamic Link` dl on dl.parent = c.name and dl.parenttype = 'Contact'
			and dl.link_doctype = 'Customer'
		where c.user = %(u)s order by c.is_primary_contact desc, c.creation asc""",
		{"u": user},
	)
	customers = []
	for customer, _contact in rows:
		if customer not in customers:
			customers.append(customer)
	perms = frappe.get_all(
		"User Permission", filters={"user": user, "allow": "Customer"}, pluck="for_value"
	)

	if not customers:
		raise PortalError("NO_CUSTOMER", "Your login is not linked to a customer account.", 403)
	if len(customers) > 1:
		raise PortalError(
			"MULTIPLE_CUSTOMERS", "Your login is linked to more than one customer. Please contact us.", 403
		)
	if len(perms) != 1 or perms[0] != customers[0]:
		raise PortalError(
			"PERMISSION_MISMATCH", "Your account permissions are not set up correctly. Please contact us.", 403
		)
	customer = customers[0]
	if frappe.db.get_value("Customer", customer, "disabled"):
		raise PortalError("CUSTOMER_DISABLED", "This customer account is disabled.", 403)
	contact = next((c for cust, c in rows if cust == customer), None)
	return user, customer, contact


def _resolve_company(customer):
	"""Company rule (a): the single company in the customer's submitted orders and invoices."""
	companies = {
		r[0]
		for r in frappe.db.sql(
			"""select distinct company from `tabSales Order` where customer = %(c)s and docstatus = 1
			union select distinct company from `tabSales Invoice` where customer = %(c)s and docstatus = 1""",
			{"c": customer},
		)
	}
	if len(companies) != 1:
		raise PortalError(
			"COMPANY_UNDETERMINED",
			"We could not determine which company serves your account. Please contact us.",
			409,
		)
	return companies.pop()


def _parse_items(raw_items):
	if isinstance(raw_items, str):
		try:
			raw_items = frappe.parse_json(raw_items)
		except Exception:
			raw_items = None
	if not isinstance(raw_items, list) or not raw_items:
		raise PortalError("INVALID_ITEMS", "items must be a non-empty list of {item_code, qty}.")
	if len(raw_items) > MAX_LINES:
		raise PortalError("TOO_MANY_ITEMS", f"An order can have at most {MAX_LINES} lines.")

	wanted = {}
	for it in raw_items:
		if not isinstance(it, dict):
			raise PortalError("INVALID_ITEMS", "Each item must be an object with item_code and qty.")
		code = it.get("item_code")
		if not isinstance(code, str) or not code.strip() or len(code) > 140:
			raise PortalError("INVALID_ITEM_CODE", "Each item needs a valid item_code.")
		code = code.strip()
		try:
			qty = float(it.get("qty"))
		except (TypeError, ValueError):
			qty = None
		if qty is None or not qty > 0:
			raise PortalError("INVALID_QTY", f"Quantity for {code} must be greater than 0.")
		if qty != int(qty):
			raise PortalError("INVALID_QTY", f"Quantity for {code} must be a whole number.")
		wanted[code] = wanted.get(code, 0) + int(qty)
		if wanted[code] > MAX_QTY:
			raise PortalError("QTY_TOO_LARGE", f"Quantity for {code} cannot exceed {MAX_QTY}.")
	return wanted


def _parse_delivery_date(value):
	today = getdate(nowdate())
	try:
		dd = getdate(value) if value else None
	except Exception:
		dd = None
	if not dd:
		raise PortalError("INVALID_DELIVERY_DATE", "delivery_date is required (YYYY-MM-DD).")
	if dd < today or dd > getdate(add_days(today, MAX_DELIVERY_DAYS)):
		raise PortalError(
			"INVALID_DELIVERY_DATE",
			f"delivery_date must be between today and {MAX_DELIVERY_DAYS} days from today.",
		)
	return dd


def _orderable_items(customer, company, codes):
	"""Sellable stock items of the company with a valid Standard Selling price (same rule as list_items)."""
	rows = frappe.db.sql(
		"""select i.name as item_code, i.stock_uom as uom, idf.default_warehouse,
			coalesce(
				(select ip.price_list_rate from `tabItem Price` ip
					where ip.item_code = i.name and ip.price_list = %(pl)s and ip.uom = i.stock_uom
					and ip.customer = %(cust)s
					and (ip.valid_from is null or ip.valid_from <= %(today)s)
					and (ip.valid_upto is null or ip.valid_upto >= %(today)s)
					order by ip.valid_from desc limit 1),
				(select ip.price_list_rate from `tabItem Price` ip
					where ip.item_code = i.name and ip.price_list = %(pl)s and ip.uom = i.stock_uom
					and (ip.customer is null or ip.customer = '')
					and (ip.valid_from is null or ip.valid_from <= %(today)s)
					and (ip.valid_upto is null or ip.valid_upto >= %(today)s)
					order by ip.valid_from desc limit 1)
			) as rate
		from `tabItem` i
		join `tabItem Default` idf on idf.parent = i.name and idf.parenttype = 'Item' and idf.company = %(co)s
		where i.disabled = 0 and i.is_sales_item = 1 and i.is_stock_item = 1 and i.has_variants = 0
			and (i.end_of_life is null or i.end_of_life > %(today)s)
			and i.name in %(codes)s""",
		{"pl": PRICE_LIST, "cust": customer, "co": company, "today": nowdate(), "codes": tuple(codes)},
		as_dict=True,
	)
	return {r.item_code: r for r in rows}


def _order_user():
	"""Internal user that saves the Draft. Set `customer_portal_order_user` in site config to use a
	dedicated System User instead of Administrator."""
	user = frappe.conf.get("customer_portal_order_user") or "Administrator"
	if user != "Administrator":
		info = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
		if not info or not info.enabled or info.user_type != "System User":
			frappe.log_error(
				title="Customer portal: invalid customer_portal_order_user",
				message=f"{user} is missing, disabled or not a System User.",
			)
			raise PortalError("ORDER_FAILED", "Ordering is temporarily unavailable. Please contact us.", 503)
	return user


@contextmanager
def _as_user(user):
	"""Run a block as another user and restore the caller's session exactly afterwards."""
	session = frappe.local.session
	saved_user, saved_sid, saved_data = session.user, session.sid, session.data
	saved_form_dict = frappe.local.form_dict
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(saved_user)
		frappe.local.session.sid = saved_sid
		frappe.local.session.data = saved_data
		frappe.local.form_dict = saved_form_dict


def _clean_error_message(exc):
	messages = []
	for m in frappe.local.message_log or []:
		try:
			msg = m.get("message") if isinstance(m, dict) else frappe.parse_json(m).get("message")
		except Exception:
			msg = str(m)
		if msg:
			messages.append(strip_html(str(msg)))
	text = "; ".join(messages) or strip_html(str(exc)) or exc.__class__.__name__
	return text[:300]


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=60 * 60)  # per client IP (Frappe default); generous for shared mobile IPs
def create_order(delivery_date=None, items=None, po_no=None, notes=None, **ignored):
	"""Create a Draft Sales Order for the logged-in portal customer.

	Body (JSON): {delivery_date, po_no?, notes?, items: [{item_code, qty}]}
	Anything else (customer, company, currency, rate, price_list, ...) is ignored.
	Returns {"data": {name, status, docstatus, grand_total, rounded_total, currency, delivery_date, lines}}
	or {"error_code", "message"} with an HTTP 4xx/5xx status.
	"""
	try:
		user, customer, contact = _resolve_customer()
		company = _resolve_company(customer)
		wanted = _parse_items(items)
		dd = _parse_delivery_date(delivery_date)

		po_no = po_no or ""
		notes = notes or ""
		if not isinstance(po_no, str) or len(po_no) > 140:
			raise PortalError("INVALID_PO_NO", "po_no must be text of at most 140 characters.")
		if not isinstance(notes, str) or len(notes) > 1000:
			raise PortalError("INVALID_NOTES", "notes must be text of at most 1000 characters.")

		found = _orderable_items(customer, company, list(wanted))
		for code in wanted:
			if code not in found:
				raise PortalError("ITEM_NOT_AVAILABLE", f"Item {code} is not available for ordering.")
			if found[code].rate is None:
				raise PortalError("ITEM_NOT_PRICED", f"Item {code} has no price. Please contact us.")

		company_currency = frappe.db.get_value("Company", company, "default_currency")
		pl_currency = frappe.db.get_value("Price List", PRICE_LIST, "currency")
		currency = frappe.db.get_value("Customer", customer, "default_currency") or company_currency
		if currency != company_currency or pl_currency != company_currency:
			raise PortalError(
				"CURRENCY_NOT_SUPPORTED", f"Online ordering is only available in {company_currency}.", 409
			)

		lines = [
			{
				"item_code": code,
				"qty": qty,
				"uom": found[code].uom,
				"conversion_factor": 1,
				"price_list_rate": flt(found[code].rate),
				"rate": flt(found[code].rate),
				"delivery_date": dd,
				"warehouse": found[code].default_warehouse,
			}
			for code, qty in wanted.items()
		]
		order_user = _order_user()
	except PortalError as err:
		return _error_response(err)

	try:
		with _as_user(order_user):
			so = frappe.get_doc(
				{
					"doctype": "Sales Order",
					"customer": customer,
					"company": company,
					"order_type": "Sales",
					"transaction_date": nowdate(),
					"delivery_date": dd,
					"currency": currency,
					"conversion_rate": 1,
					"selling_price_list": PRICE_LIST,
					"price_list_currency": pl_currency,
					"plc_conversion_rate": 1,
					"po_no": po_no.strip() or None,
					"contact_person": contact,
					"utm_content": SOURCE_TAG,
					"items": lines,
				}
			)
			so.flags.ignore_permissions = True
			so.insert()  # Draft only: never submitted here.
			note = f"<b>Placed via customer portal app</b> by {escape_html(user)}"
			if notes.strip():
				note += "<br>Customer notes: " + escape_html(notes.strip()).replace("\n", "<br>")
			so.add_comment("Comment", note)
			result = {
				"name": so.name,
				"status": so.status,
				"docstatus": so.docstatus,
				"grand_total": flt(so.grand_total),
				"rounded_total": flt(so.rounded_total),
				"currency": so.currency,
				"delivery_date": str(dd),
				"lines": len(lines),
			}
	except Exception as exc:
		frappe.db.rollback()
		message = _clean_error_message(exc)
		frappe.log_error(title="Customer portal order failed", message=f"user={user}\n{frappe.get_traceback()}")
		frappe.clear_messages()
		return _error_response(PortalError("ORDER_FAILED", f"The order could not be created: {message}", 422))

	return {"data": result}
