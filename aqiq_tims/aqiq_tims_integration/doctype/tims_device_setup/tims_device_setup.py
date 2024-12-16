# Copyright (c) 2024, RONOH and contributors
# For license information, please see license.txt

from frappe.model.document import Document
import frappe
import socket  # Example for testing connection
from frappe.utils import now_datetime

class TIMSDeviceSetup(Document):
	pass


@frappe.whitelist()
def test_connection(ip, port, name):
	try:
		# Test the connection
		with socket.create_connection((ip, int(port)), timeout=5) as sock:
			# Update the document status
			doc = frappe.get_doc("TIMS Device Setup", name)
			doc.status = "Active"
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			
			return {
				"success": True,
				"message": "Connection successful",
				"status": "Active"
			}
	except Exception as e:
		# Update status to inactive on failure
		doc = frappe.get_doc("TIMS Device Setup", name)
		
		doc.status = "Inactive"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		error_message = f"TIMS Device Connection Test Error: {str(e)}\nIP: {ip}, Port: {port}"
		frappe.log_error(
			message=error_message,
			title="TIMS Device Connection Test Error"
		)
		
		return {
			"success": False,
			"error": str(e),
			"status": "Inactive"
		}
