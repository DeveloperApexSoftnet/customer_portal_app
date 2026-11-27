app_name = "customer_portal"
app_title = "Customer Portal"
app_publisher = "Apex Softnet"
app_description = "Hari Bhog customer portal: server-side order creation for portal (Website) users"
app_email = "developer@apexsoftnet.com"
app_license = "mit"

required_apps = ["erpnext"]

# Keep the URL the mobile app already uses (/api/method/customer_portal_create_order).
# Frappe checks this override before Server Scripts, so the old Server Script of the
# same name is no longer reached once this app is installed.
override_whitelisted_methods = {
	"customer_portal_create_order": "customer_portal.api.create_order",
	"customer_portal_update_order": "customer_portal.api.update_order",
	"customer_portal_cancel_order": "customer_portal.api.cancel_order",
}
