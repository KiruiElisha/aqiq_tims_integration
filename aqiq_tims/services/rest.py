import frappe
import json
import requests
from frappe.utils import today
from datetime import datetime

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


def build_payload(doc, device_setup):
    payment_method = "Cash" if doc.status == 'Paid' else 'Credit'
    till_no = ''
    rct_no = format_invoice_number(doc.name)
    customer_pin = frappe.db.get_value("Customer", doc.customer, "tax_id") or ''
    invoice_items = get_invoice_items(doc.name)
    tax_category = get_tax_category(doc.name)
    
    vat_values = initialize_vat_values()
    items = []

    for item in invoice_items:
        new_item, taxable_amount, tax_amount = calculate_tax(item, tax_category)
        
        vat_values = update_vat_values(vat_values, item.title, taxable_amount, tax_amount)
        items.append(new_item)

    payload = create_payload(doc, vat_values, items, payment_method, customer_pin, till_no, rct_no)
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
    """Calculate tax for an item based on its tax template
    
    Args:
        item: Sales Invoice Item document
        tax_category: "Inclusive" or "Exclusive"
        
    Returns:
        tuple: (new_item dict, taxable_amount, tax_amount)
    """
    # Get tax rate from item_tax_rate if available
    tax_rate = 0
    if item.item_tax_rate:
        try:
            tax_rates = frappe.parse_json(item.item_tax_rate)
            # Get first tax rate value
            tax_rate = next(iter(tax_rates.values())) if tax_rates else 0
        except Exception:
            frappe.log_error("Failed to parse item_tax_rate", "Tax Calculation Error")
    
    # Default to 16% if no valid tax rate found and not zero-rated
    if tax_rate == 0 and not item.item_tax_template.upper().startswith("ZERO"):
        tax_rate = 16

    tax_value = 1 + (tax_rate / 100)
    qty = abs(item.qty)  # Use actual quantity from item
    base_net_rate = abs(item.base_net_amount / qty if qty else 0)

    unit_price = round(base_net_rate * tax_value, 2)
    discount = 0.0  # Can be enhanced to use item.discount_amount

    new_item = {
        "productCode": item.item_code,
        "productDesc": item.item_name,
        "quantity": qty,
        "unitPrice": abs(unit_price),
        "discount": abs(discount),
        "taxtype": int(tax_rate)  # This will be 0 for zero-rated items
    }

    # Calculate amounts based on tax category
    if tax_category == "Inclusive":
        taxable_amount = (unit_price * qty - discount) / tax_value
        tax_amount = taxable_amount * (tax_rate / 100)
    else:
        taxable_amount = unit_price * qty - discount
        tax_amount = taxable_amount * (tax_rate / 100)

    return new_item, taxable_amount, tax_amount


def update_vat_values(vat_values, tax_type, taxable_amount, tax_amount):
    frappe.logger().debug(f"""
        VAT Update:
        Tax Type: {tax_type}
        Taxable Amount: {taxable_amount}
        Tax Amount: {tax_amount}
    """)

    if not tax_type:
        vat_values["VAT_A_NET"] += taxable_amount
        vat_values["VAT_A"] += tax_amount
        return vat_values

    tax_mapping = {
        "VAT 16%": ("VAT_A_NET", "VAT_A"),
        "16%": ("VAT_A_NET", "VAT_A"),
        "VAT 8%": ("VAT_B_NET", "VAT_B"),
        "8%": ("VAT_B_NET", "VAT_B"),
        "VAT 10%": ("VAT_C_NET", "VAT_C"),
        "10%": ("VAT_C_NET", "VAT_C"),
        "VAT 2%": ("VAT_D_NET", "VAT_D"),
        "2%": ("VAT_D_NET", "VAT_D"),
        "Zero Rated": ("VAT_E_NET", "VAT_E"),
        "0%": ("VAT_E_NET", "VAT_E"),
        "Exempt": ("VAT_F_NET", "VAT_F")
    }

    if tax_type in tax_mapping:
        net_key, tax_key = tax_mapping[tax_type]
        vat_values[net_key] += taxable_amount
        if tax_key != "VAT_E" and tax_key != "VAT_F":  # Don't add tax amount for zero-rated and exempt
            vat_values[tax_key] += tax_amount
    else:
        vat_values["VAT_A_NET"] += taxable_amount
        vat_values["VAT_A"] += tax_amount

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
            
        response = requests.post(
            f"https://{device_setup.ip}:{device_setup.port}/api/values/PostTims",
            json=payload,
            timeout=60,
            verify=True  # SSL verification
        )
        handle_response(response, invoice, doc, payload)
    except requests.Timeout:
        frappe.log_error("TIMS request timeout", "TIMS Error")
        frappe.msgprint(
            msg="Request timed out. Please verify TIMS/ETR Machine is active.",
            title="Timeout Error",
            indicator='red',
        )
    except requests.RequestException as e:
        frappe.log_error(f"TIMS request failed: {str(e)}", "TIMS Error")
        frappe.msgprint(
            msg="Failed to communicate with TIMS server. Please check logs.",
            title="Connection Error",
            indicator='red',
        )
    except Exception as e:
        handle_exception(e)


def handle_response(response, invoice, doc, payload):
    try:
        data = json.loads(response.text)
        required_fields = ["ResponseCode", "Message", "TSIN", "CUSN", "CUIN", "QRCode", "dtStmp"]
        
        if not all(field in data for field in required_fields):
            raise ValueError(f"Missing required fields in response: {response.text}")

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
            "payload_sent": str(payload)
        })

        kra_response.insert()
        frappe.db.commit()

        if data.get('ResponseCode') == '000':
            update_doc_with_response(doc, data)
        else:
            frappe.log_error(
                f"KRA submission failed: {data.get('Message', 'Unknown error')}",
                "KRA Error"
            )
            frappe.msgprint(
                msg="Invoice Submission to KRA Failed. Please Check KRA Response Generated.",
                title='Error Message',
                indicator='red',
            )
    except json.JSONDecodeError:
        frappe.log_error(f"Invalid JSON response: {response.text}", "TIMS Error")
        raise


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
