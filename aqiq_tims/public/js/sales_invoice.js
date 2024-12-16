frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        // Only show button if invoice is submitted and not already sent to KRA
        if (frm.doc.docstatus === 1 && !frm.doc.custom_sent_to_kra) {
            frm.add_custom_button(__('Send to TIMS'), function() {
                send_to_tims(frm);
            }, __('TIMS'));
        }

        // Show TIMS status in the dashboard
        if (frm.doc.custom_sent_to_kra) {
            let status_color = frm.doc.custom_tims_response_code === '000' ? 'green' : 'red';
            let status_message = frm.doc.custom_tims_response_code === '000' ? 
                'Successfully sent to TIMS' : 
                'Failed to send to TIMS';

            frm.dashboard.add_indicator(
                __(`TIMS Status: ${status_message}`),
                status_color
            );

            // Show TIMS details section
            show_tims_details(frm);
        }
    }
});

function send_to_tims(frm) {
    frappe.call({
        method: 'aqiq_tims.services.rest.send_request',
        args: {
            invoice: frm.doc.name
        },
        freeze: true,
        freeze_message: __('Sending to TIMS...'),
        callback: function(r) {
            frm.reload_doc();
        }
    });
}

function show_tims_details(frm) {
    if (frm.doc.custom_sent_to_kra) {
        let html = `
            <div class="tims-details" style="padding: 10px; margin-top: 10px;">
                <div class="row">
                    <div class="col-sm-6">
                        <strong>TIMS Response Code:</strong> ${frm.doc.custom_tims_response_code || ''}
                    </div>
                    <div class="col-sm-6">
                        <strong>Signing Time:</strong> ${frm.doc.custom_signing_time || ''}
                    </div>
                </div>
                <div class="row" style="margin-top: 10px;">
                    <div class="col-sm-4">
                        <strong>TSIN:</strong> ${frm.doc.custom_tsin || ''}
                    </div>
                    <div class="col-sm-4">
                        <strong>CUSN:</strong> ${frm.doc.custom_cusn || ''}
                    </div>
                    <div class="col-sm-4">
                        <strong>CUIN:</strong> ${frm.doc.custom__cuin || ''}
                    </div>
                </div>
                ${frm.doc.custom_kra_qr_code ? `
                <div class="row" style="margin-top: 10px;">
                    <div class="col-sm-12">
                        <strong>QR Code Data:</strong>
                        <div style="word-break: break-all; margin-top: 5px;">
                            ${frm.doc.custom_kra_qr_code}
                        </div>
                    </div>
                </div>
                ` : ''}
            </div>
        `;

        $(frm.dashboard.wrapper).find('.tims-details').remove();
        $(frm.dashboard.wrapper).append(html);
    }
} 