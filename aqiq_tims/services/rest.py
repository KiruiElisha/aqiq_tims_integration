import frappe
import json
import requests
from frappe.utils import today
from datetime import datetime
import re

@frappe.whitelist()
def send_request(invoice):
    try:
        device_setup = frappe.get_single('TIMS Device Setup')
        doc = frappe.get_doc("Sales Invoice", invoice)

        if device_setup.status == 'Active':
            if is_valid_posting_date(doc, device_setup):
                payload = build_payload(doc, device_setup)
                send_payload(payload, invoice, doc)
            else:
                frappe.msgprint(
                    msg="Invoice Posting Date Must be Today's Date",
                    title='Error Message',
                    indicator='red',
                )
        else:
            frappe.msgprint(
                msg='TIMS Device Setup for Sending Invoices is not Active.',
                title='Error Message',
                indicator='red',
            )
    except Exception as e:
        handle_exception(e)


def is_valid_posting_date(doc, device_setup):
    today = datetime.now().strftime("%d-%m-%Y")
    posting_date = doc.posting_date.strftime("%d-%m-%Y")
    return posting_date == today or device_setup.allow_other_day_posting


def validate_customer_pin(pin):
    """Validate KRA PIN format"""
    if not pin:
        return ""
    # KRA PINs are typically A000000000X format
    if not re.match(r'^[A-Z][0-9]{9}[A-Z]$', pin):
        frappe.log_error(f"Invalid KRA PIN format: {pin}", "TIMS Validation")
    return pin


def build_payload(doc, device_setup):
    payment_method = "Cash" if doc.status == 'Paid' else 'Credit'
    till_no = ''
    rct_no = format_invoice_number(doc.name)
    customer_pin = validate_customer_pin(frappe.db.get_value("Customer", doc.customer, "tax_id") or '')
    invoice_items = get_invoice_items(doc.name)
    tax_category = get_tax_category(doc.name)
    
    vat_values = initialize_vat_values()
    items = []

    for item in invoice_items:
        new_item, taxable_amount, tax_amount = calculate_tax(item, tax_category)
        
        vat_values = update_vat_values(vat_values, item.title, taxable_amount, tax_amount)
        items.append(new_item)

    payload = create_payload(doc, vat_values, items, payment_method, customer_pin, till_no, rct_no)
    validate_payload(payload, doc)
    return payload


def get_invoice_items(invoice):
    query = """
        SELECT DISTINCT sii.name, sii.item_code, sii.item_name, sii.rate, sii.base_rate, sii.base_amount,
        sii.base_net_rate, sii.base_net_amount, sii.qty, sii.item_tax_template, 
        item_tax.item_tax_template, it_template.title, it_template_detail.tax_rate
        FROM `tabSales Invoice Item` sii
        LEFT JOIN `tabItem Tax` item_tax ON item_tax.parent = sii.item_code 
        LEFT JOIN `tabItem Tax Template` it_template ON it_template.name = item_tax.item_tax_template
        LEFT JOIN `tabItem Tax Template Detail` it_template_detail ON it_template_detail.parent = item_tax.item_tax_template
        WHERE sii.parent = %s
    """
    return frappe.db.sql(query, invoice, as_dict=True)


def get_tax_category(invoice):
    is_inclusive_or_exclusive = frappe.db.get_value('Sales Taxes and Charges', {'parenttype': 'Sales Invoice', 'parent': invoice}, 'included_in_print_rate')
    return "Inclusive" if is_inclusive_or_exclusive == 1 else "Exclusive"


def initialize_vat_values():
    return {
        "VAT_A_NET": 0,
        "VAT_A": 0,
        "VAT_B_NET": 0,
        "VAT_B": 0,
        "VAT_C_NET": 0,
        "VAT_C": 0,
        "VAT_D_NET": 0,
        "VAT_D": 0,
        "VAT_E_NET": 0,
        "VAT_E": 0,
        "VAT_F_NET": 0,
        "VAT_F": 0,
    }


def calculate_tax(item, tax_category):
    """Calculate tax for an item based on its tax template"""
    if not item.item_tax_template:
        frappe.log_error(
            f"Missing tax template for item {item.item_code}",
            "TIMS Tax Calculation"
        )
        return create_zero_rated_item(item)
        
    # Get tax rate from template
    tax_rate = get_tax_rate_from_template(item.item_tax_template)
    
    tax_value = 1 + (tax_rate / 100)
    qty = abs(item.qty)
    unit_price = abs(item.rate)
    discount = 0.0

    new_item = {
        "productCode": item.item_code,
        "productDesc": item.item_name,
        "quantity": qty,
        "unitPrice": unit_price,
        "discount": discount,
        "taxtype": int(tax_rate)
    }

    # Calculate tax based on tax category
    if tax_rate == 0:
        taxable_amount = unit_price * qty
        tax_amount = 0
    else:
        if tax_category == "Inclusive":
            taxable_amount = (unit_price * qty) / tax_value
            tax_amount = taxable_amount * (tax_rate / 100)
        else:
            taxable_amount = unit_price * qty
            tax_amount = taxable_amount * (tax_rate / 100)

    return new_item, taxable_amount, tax_amount


def get_tax_rate_from_template(template_name):
    """Get tax rate from template"""
    if not template_name:
        return 0
        
    tax_rate = frappe.db.get_value(
        "Item Tax Template Detail",
        {"parent": template_name},
        "tax_rate"
    )
    return tax_rate or 0


def create_zero_rated_item(item):
    """Create item entry with zero tax"""
    new_item = {
        "productCode": item.item_code,
        "productDesc": item.item_name,
        "quantity": abs(item.qty),
        "unitPrice": abs(item.rate),
        "discount": 0.0,
        "taxtype": 0
    }
    taxable_amount = new_item["quantity"] * new_item["unitPrice"]
    return new_item, taxable_amount, 0


def update_vat_values(vat_values, tax_type, taxable_amount, tax_amount):
    """
    Update VAT values based on tax type. Current implementation has issues with mapping.
    We need to properly map the tax templates to KRA categories.
    """
    # Map tax templates to KRA VAT categories
    kra_tax_mapping = {
        "VAT 16%": ("VAT_A_NET", "VAT_A"),
        "VAT - IG": ("VAT_A_NET", "VAT_A"),  # Add this mapping
        "Zero Rated": ("VAT_E_NET", "VAT_E"),
        "Exempt": ("VAT_F_NET", "VAT_F")
    }
    
    # Get template title from tax_type
    if not tax_type:
        # Default to VAT_E for items without tax template
        vat_values["VAT_E_NET"] += taxable_amount
        return vat_values
        
    # Find matching KRA category
    for template_name, (net_key, tax_key) in kra_tax_mapping.items():
        if template_name in tax_type:
            vat_values[net_key] += taxable_amount
            if tax_key != "VAT_E" and tax_key != "VAT_F":
                vat_values[tax_key] += tax_amount
            return vat_values
            
    # Log error for unknown tax template
    frappe.log_error(
        f"Unknown tax template mapping: {tax_type}",
        "TIMS Tax Mapping Error"
    )
    # Default to VAT_E
    vat_values["VAT_E_NET"] += taxable_amount
    return vat_values


def create_payload(doc, vat_values, items, payment_method, customer_pin, till_no, rct_no):
    total = sum([
        vat_values["VAT_A_NET"] + vat_values["VAT_A"],
        vat_values["VAT_B_NET"] + vat_values["VAT_B"],
        vat_values["VAT_C_NET"] + vat_values["VAT_C"],
        vat_values["VAT_D_NET"] + vat_values["VAT_D"],
        vat_values["VAT_E_NET"],
        vat_values["VAT_F_NET"]
    ])

    payload_type = "sales" if not doc.is_return else "refund"
    cuin = "" if not doc.is_return else frappe.db.get_value("KRA Response", {"invoice_number": doc.name}, "cuin")

    payload = {
        "saleType": payload_type,
        "cuin": cuin,
        "till": till_no,
        "rctNo": rct_no,
        "total": round(float(total), 2),
        "Paid": round(float(total), 2),
        "Payment": payment_method,
        "CustomerPIN": customer_pin,
        "VAT_A_Net": round(float(vat_values["VAT_A_NET"]), 2),
        "VAT_A": round(float(vat_values["VAT_A"]), 2),
        "VAT_B_Net": round(float(vat_values["VAT_B_NET"]), 2),
        "VAT_B": round(float(vat_values["VAT_B"]), 2),
        "VAT_C_Net": round(float(vat_values["VAT_C_NET"]), 2),
        "VAT_C": round(float(vat_values["VAT_C"]), 2),
        "VAT_D_Net": round(float(vat_values["VAT_D_NET"]), 2),
        "VAT_D": round(float(vat_values["VAT_D"]), 2),
        "VAT_E_Net": round(float(vat_values["VAT_E_NET"]), 2),
        "VAT_E": round(float(vat_values["VAT_E"]), 2),
        "VAT_F_Net": round(float(vat_values["VAT_F_NET"]), 2),
        "VAT_F": round(float(vat_values["VAT_F"]), 2),
        "data": items
    }

    return payload


def send_payload(payload, invoice, doc):
    try:
        device_setup = frappe.get_single('TIMS Device Setup')
        if not device_setup.ip or not device_setup.port:
            raise ValueError("Invalid device setup configuration")
            
        # Add timeout and verify SSL
        response = requests.post(
            f"http://{device_setup.ip}:{device_setup.port}/api/values/PostTims",  # Change to http
            json=payload,
            timeout=60,
            verify=False  # Disable SSL verification for internal IP
        )
        handle_response(response, invoice, doc, payload)
    except requests.Timeout:
        frappe.log_error("TIMS request timeout", "TIMS Error")
        frappe.throw(
            "Request timed out. Please verify TIMS/ETR Machine is active.",
            title="Timeout Error"
        )
    except requests.RequestException as e:
        frappe.log_error(f"TIMS request failed: {str(e)}", "TIMS Error")
        frappe.throw(
            "Failed to communicate with TIMS server. Please check logs.",
            title="Connection Error"
        )
    except Exception as e:
        handle_exception(e)


def handle_response(response, invoice, doc, payload):
    try:
        if not response.ok:
            frappe.throw(f"TIMS server returned error: {response.status_code} - {response.text}")
            
        data = json.loads(response.text)
        required_fields = ["ResponseCode", "Message", "TSIN", "CUSN", "CUIN", "QRCode", "dtStmp"]
        
        if not all(field in data for field in required_fields):
            frappe.throw(f"Missing required fields in response: {response.text}")

        # Log successful response
        frappe.log_error(
            f"TIMS Response for {invoice}: {response.text}",
            "TIMS Success"
        )

        kra_response = frappe.get_doc({
            "doctype": "KRA Response",
            "response_code": data.get("ResponseCode", ''),
            "message": data.get("Message", ''),
            "tin": data.get("TSIN", ''),
            "cusn": data.get("CUSN", ''),
            "cuin": data.get("CUIN", ''),
            "qr_code": data.get("QRCode", ''),
            "signing_time": data.get("dtStmp", ''),
            "invoice_number": invoice,
            "payload_sent": json.dumps(payload, indent=2)  # Pretty print payload
        })

        kra_response.insert()
        frappe.db.commit()

        if data.get('ResponseCode') == '000':
            update_doc_with_response(doc, data)
        else:
            frappe.throw(
                f"KRA submission failed: {data.get('Message', 'Unknown error')}",
                title="KRA Error"
            )
    except json.JSONDecodeError:
        frappe.throw(f"Invalid JSON response: {response.text}")


def update_doc_with_response(doc, data):
    doc.custom_tims_code = data["ResponseCode"]
    doc.custom_tsin = data["TSIN"]
    doc.custom_cusn = data["CUSN"]
    doc.custom__cuin = data["CUIN"]
    doc.custom_kra_qr_code = data["QRCode"]
    doc.custom_signing_time = data["dtStmp"]
    doc.custom_sent_to_kra = 1
    doc.save(ignore_permissions=True)

    if doc.docstatus == 0:
        doc.submit()
        doc.reload()


def handle_exception(exception):
    error_message = "TIMS KRA Error.\n{}".format(frappe.get_traceback())
    frappe.log_error(error_message, "TIMS KRA Error.")
    frappe.msgprint(
        msg="Something Wrong, Please try again or check the "+"<a style='color: red; font-weight: bold;' href='/app/error-log'>Error Logs</a>",
        title="Error Message",
        indicator='red',
    )
    return exception


def format_invoice_number(invoice_number):
    """Format invoice number to comply with KRA's 18 character limit"""
    if len(invoice_number) <= 18:
        return invoice_number
        
    # Remove the 'ACC-' prefix and keep the important parts
    # ACC-SINV-2024-00007 -> SINV240007
    parts = invoice_number.replace('ACC-', '').split('-')
    if len(parts) == 3:  # Expected format: SINV-2024-00007
        year = parts[1][-2:]  # Take last 2 digits of year
        number = parts[2].lstrip('0')  # Remove leading zeros
        formatted = f"{parts[0]}{year}{number}"
        return formatted[:18]
    
    # Fallback: just truncate to 18 chars if format is different
    return invoice_number[-18:]


def validate_tax_totals(vat_values, doc):
    """Validate tax totals match invoice totals"""
    total_tax = sum([
        vat_values["VAT_A"],
        vat_values["VAT_B"],
        vat_values["VAT_C"],
        vat_values["VAT_D"]
    ])
    
    invoice_tax = doc.total_taxes_and_charges
    
    if abs(total_tax - invoice_tax) > 0.01:  # Allow small rounding difference
        frappe.log_error(
            f"Tax total mismatch: VAT sum={total_tax}, Invoice tax={invoice_tax}",
            "TIMS Validation"
        )


def validate_payload(payload, doc):
    """Validate payload before sending to TIMS"""
    # Validate totals
    calculated_total = sum([
        payload["VAT_A_Net"] + payload["VAT_A"],
        payload["VAT_B_Net"] + payload["VAT_B"],
        payload["VAT_C_Net"] + payload["VAT_C"],
        payload["VAT_D_Net"] + payload["VAT_D"],
        payload["VAT_E_Net"],
        payload["VAT_F_Net"]
    ])
    
    if abs(calculated_total - doc.grand_total) > 0.01:
        frappe.throw(
            f"Total mismatch: Calculated={calculated_total}, Invoice={doc.grand_total}"
        )
        
    # Validate tax amounts
    total_tax = sum([
        payload["VAT_A"],
        payload["VAT_B"],
        payload["VAT_C"],
        payload["VAT_D"]
    ])
    
    if abs(total_tax - doc.total_taxes_and_charges) > 0.01:
        frappe.throw(
            f"Tax mismatch: Calculated={total_tax}, Invoice={doc.total_taxes_and_charges}"
        )
        
    # Validate items
    for item in payload["data"]:
        if item["quantity"] <= 0:
            frappe.throw(f"Invalid quantity for item {item['productCode']}")
        if item["unitPrice"] <= 0:
            frappe.throw(f"Invalid unit price for item {item['productCode']}")
