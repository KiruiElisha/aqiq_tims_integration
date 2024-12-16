// Copyright (c) 2024, RONOH and contributors
// For license information, please see license.txt

 frappe.ui.form.on("TIMS Device Setup", {
    refresh(frm) {
        // Show current connection status on dashboard
        if (frm.doc.status === "Active") {
            frm.dashboard.set_headline_alert(
                `<div class="row">
                    <div class="col"><span class="indicator green">Connected</span></div>
                </div>`
            );
        } else {
            frm.dashboard.set_headline_alert(
                `<div class="row">
                    <div class="col"><span class="indicator red">Disconnected</span></div>
                </div>`
            );
        }

        frm.add_custom_button(__('Test Connection'), function() {
            frm.disable_save();
            frappe.call({
                method: "aqiq_tims.aqiq_tims_integration.doctype.tims_device_setup.tims_device_setup.test_connection",
                args: {
                    ip: frm.doc.ip,
                    port: frm.doc.port,
                    name: frm.doc.name
                },
                freeze: true,
                freeze_message: __('Testing Connection...'),
                callback: function(r) {
                    frm.enable_save();
                    if (r.message && r.message.success) {
                        frm.dashboard.set_headline_alert(
                            `<div class="row">
                                <div class="col"><span class="indicator green">Connected</span></div>
                            </div>`
                        );
                        frappe.show_alert({
                            message: __('Connection Successful'),
                            indicator: 'green'
                        }, 5);
                        frm.reload_doc();
                    } else {
                        frm.dashboard.set_headline_alert(
                            `<div class="row">
                                <div class="col"><span class="indicator red">Disconnected</span></div>
                            </div>`
                        );
                        frappe.show_alert({
                            message: __('Connection Failed: ') + (r.message.error || 'Unknown Error'),
                            indicator: 'red'
                        }, 5);
                        frm.reload_doc();
                    }
                }
            });
        }, __('Actions'));
    },
});

