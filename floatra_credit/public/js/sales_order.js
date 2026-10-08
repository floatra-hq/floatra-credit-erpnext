// -*- coding: utf-8 -*-
// Sales Order client script — wires the "Request Financing"
// button + credit status badge.
//
// Loaded via hooks.py::doctype_js.

frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        render_credit_button(frm);
        render_credit_badge(frm);
    },

    customer(frm) {
        // Re-render when the customer changes — the eligibility
        // signal depends on `customer.floatra_merchant_id`.
        render_credit_button(frm);
    },
});


function render_credit_button(frm) {
    if (frm.doc.docstatus !== 0) return;
    if (!frm.doc.customer) return;

    frappe.db.get_value(
        "Customer",
        frm.doc.customer,
        ["floatra_merchant_id", "floatra_credit_status"],
        (r) => {
            if (!r || !r.floatra_merchant_id) return;
            const eligible = !["locked", "suspended"].includes(
                r.floatra_credit_status,
            );
            const decision = frm.doc.floatra_decision || "not_requested";

            if (
                eligible &&
                ["not_requested", "declined"].includes(decision)
            ) {
                frm.add_custom_button(
                    __("Request Financing"),
                    () => trigger_credit_request(frm),
                    __("Floatra"),
                );
            }

            // A draft cannot be cancelled (only deleted), so an approved but
            // unfunded reservation is released from here.
            if (decision === "approved" && !frm.doc.floatra_disbursed) {
                frm.add_custom_button(
                    __("Cancel Floatra Credit"),
                    () => frappe.confirm(
                        __("Cancel the Floatra credit on this order? The lender's reservation is released."),
                        () => frappe.call({
                            method: "floatra_credit.api.action_cancel_floatra_credit",
                            args: { sales_order_name: frm.doc.name },
                            freeze: true,
                            callback() {
                                frm.reload_doc();
                            },
                        }),
                    ),
                    __("Floatra"),
                );
            }

            if (
                frm.doc.floatra_disbursed &&
                !frm.doc.floatra_delivery_confirmed
            ) {
                frm.add_custom_button(
                    __("Confirm Delivery"),
                    () => open_delivery_dialog(frm),
                    __("Floatra"),
                );
            }

            // Cash-repayment wizard — available once the loan has
            // been disbursed (no point recording a repayment against
            // a pending disbursement). The wizard form lives at the
            // "Floatra Cash Repayment Wizard" transient doctype.
            if (frm.doc.floatra_disbursed) {
                frm.add_custom_button(
                    __("Record Cash Repayment"),
                    () => open_cash_repayment_wizard(frm),
                    __("Floatra"),
                );
            }

            // Initiate Return: funded + delivered orders. Floatra takes a
            // return only against a loan with an open delivery dispute
            // (RETURN_015 otherwise), which today only offline orders
            // can have; the refusal is shown as a plain message.
            if (
                frm.doc.floatra_disbursed &&
                frm.doc.floatra_delivery_confirmed
            ) {
                frm.add_custom_button(
                    __("Initiate Return"),
                    () => open_initiate_return_dialog(frm),
                    __("Floatra"),
                );
            }
        },
    );
}


function open_initiate_return_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Initiate Return to Floatra"),
        fields: [
            {
                fieldtype: "Currency",
                fieldname: "return_amount_naira",
                label: __("Return Amount (₦)"),
                reqd: 1,
                description: __(
                    "Amount being returned. Floatra reverses inventory + " +
                    "adjusts the loan balance.",
                ),
            },
            {
                fieldtype: "Select",
                fieldname: "reason_code",
                label: __("Reason"),
                reqd: 1,
                options: [
                    "DAMAGED",
                    "WRONG_ITEM",
                    "MERCHANT_REQUESTED",
                    "EXPIRED",
                    "OTHER",
                ].join("\n"),
            },
            {
                fieldtype: "Small Text",
                fieldname: "reason_notes",
                label: __("Notes (optional)"),
                description: __("Max 500 characters."),
            },
        ],
        primary_action_label: __("Submit Return"),
        primary_action(values) {
            // Naira, as entered: the server builds Floatra's decimal
            // string (no float maths, no kobo on the wire).
            const amount = values.return_amount_naira;
            if (!(parseFloat(amount) > 0)) {
                frappe.msgprint({
                    title: __("Invalid amount"),
                    message: __("Return amount must be at least ₦0.01."),
                    indicator: "red",
                });
                return;
            }
            frappe.call({
                method: "floatra_credit.api.action_initiate_return",
                args: {
                    sales_order_name: frm.doc.name,
                    return_amount: String(amount),
                    reason_code: values.reason_code,
                    reason_notes: values.reason_notes || "",
                },
                freeze: true,
                freeze_message: __("Submitting return to Floatra..."),
                callback() {
                    d.hide();
                    frm.reload_doc();
                    frappe.show_alert({
                        message: __(
                            "Return submitted. Floatra will update the loan balance.",
                        ),
                        indicator: "green",
                    });
                },
            });
        },
    });
    d.show();
}


function render_credit_badge(frm) {
    if (!frm.doc.customer) return;
    frappe.db.get_value(
        "Customer",
        frm.doc.customer,
        "floatra_credit_status",
        (r) => {
            if (!r) return;
            const status = r.floatra_credit_status;
            if (!status || status === "not_enrolled") return;
            const colour = {
                available: "green",
                pending_kyc: "orange",
                locked: "red",
                suspended: "red",
            }[status] || "grey";
            frm.dashboard.add_indicator(
                __("Floatra: {0}", [status]),
                colour,
            );
        },
    );
}


function trigger_credit_request(frm) {
    frappe.call({
        method: "floatra_credit.api.request_credit",
        args: { sales_order_name: frm.doc.name },
        freeze: true,
        freeze_message: __("Requesting financing..."),
        callback(r) {
            if (r.message) {
                frappe.show_alert({
                    message: __("Floatra: {0}", [
                        r.message.decision || "PENDING",
                    ]),
                    indicator: "green",
                });
                frm.reload_doc();
            }
        },
    });
}


function open_delivery_dialog(frm) {
    // Inline dialog (same pattern as the cash-repayment wizard) —
    // the Sales Order is the natural anchor, so we keep the user
    // here rather than navigating to a separate transient doctype.
    const dialog = new frappe.ui.Dialog({
        title: __("Confirm Floatra Delivery"),
        fields: [
            {
                fieldname: "customer_display",
                label: __("Customer"),
                fieldtype: "Read Only",
                default: frm.doc.customer,
            },
            {
                fieldname: "sales_order_display",
                label: __("Sales Order"),
                fieldtype: "Read Only",
                default: frm.doc.name,
            },
            { fieldtype: "Section Break" },
            {
                fieldname: "delivered_at",
                label: __("Delivered At"),
                fieldtype: "Datetime",
                default: frappe.datetime.now_datetime(),
                reqd: 1,
                description: __(
                    "When the merchant actually received the goods. " +
                    "Starts the repayment clock — confirm only after " +
                    "physical delivery.",
                ),
            },
            {
                fieldname: "notes",
                label: __("Notes"),
                fieldtype: "Small Text",
                description: __(
                    "Optional. Agent name, vehicle, photo URL, or any " +
                    "other audit detail. Max 500 chars.",
                ),
            },
        ],
        primary_action_label: __("Confirm Delivery"),
        primary_action(values) {
            frappe.call({
                method:
                    "floatra_credit.api.confirm_delivery_for_sales_order",
                args: {
                    sales_order_name: frm.doc.name,
                    delivered_at: values.delivered_at,
                    notes: values.notes || null,
                },
                freeze: true,
                freeze_message: __("Recording delivery..."),
                callback(r) {
                    if (r.message) {
                        dialog.hide();
                        if (r.message.already_confirmed) {
                            frappe.show_alert({
                                message: __(
                                    "Delivery already confirmed for " +
                                    "this order — no action needed.",
                                ),
                                indicator: "blue",
                            });
                        } else {
                            frappe.show_alert({
                                message: __(
                                    "✅ Delivery confirmed. Repayment " +
                                    "clock started.",
                                ),
                                indicator: "green",
                            });
                        }
                        frm.reload_doc();
                    }
                },
            });
        },
    });
    dialog.show();
}


function open_cash_repayment_wizard(frm) {
    // Inline dialog rather than a full Transient doctype form —
    // simpler than `frappe.new_doc("Floatra Cash Repayment Wizard")`
    // because we want the customer + sales_order pre-filled and
    // the form submission to go straight through the whitelisted
    // method (no intermediate Transient row created).
    const dialog = new frappe.ui.Dialog({
        title: __("Record Cash Repayment"),
        fields: [
            {
                fieldname: "customer_display",
                label: __("Customer"),
                fieldtype: "Read Only",
                default: frm.doc.customer,
            },
            {
                fieldname: "sales_order_display",
                label: __("Sales Order"),
                fieldtype: "Read Only",
                default: frm.doc.name,
            },
            { fieldtype: "Column Break" },
            {
                fieldname: "amount",
                label: __("Amount Paid"),
                fieldtype: "Currency",
                reqd: 1,
                default: frm.doc.floatra_repayment_amount,
            },
            {
                fieldname: "reference",
                label: __("Reference"),
                fieldtype: "Data",
                reqd: 1,
                description: __(
                    "Your own deduplication key (receipt number, " +
                    "settlement batch ID, bank transfer reference). " +
                    "Floatra dedupes on this.",
                ),
            },
            { fieldtype: "Section Break" },
            {
                fieldname: "source",
                label: __("Source"),
                fieldtype: "Select",
                options: [
                    "MANUAL_CASH",
                    "SETTLEMENT_DEDUCTION",
                    "BANK_TRANSFER",
                    "OTHER",
                ],
                default: "MANUAL_CASH",
                reqd: 1,
            },
            {
                fieldname: "collected_at",
                label: __("Collected At"),
                fieldtype: "Datetime",
                default: frappe.datetime.now_datetime(),
                reqd: 1,
            },
            {
                fieldname: "notes",
                label: __("Notes"),
                fieldtype: "Small Text",
            },
        ],
        primary_action_label: __("Record Payment"),
        primary_action(values) {
            frappe.call({
                method:
                    "floatra_credit.api.record_cash_repayment",
                args: {
                    sales_order_name: frm.doc.name,
                    amount: values.amount,
                    reference: values.reference,
                    source: values.source,
                    collected_at: values.collected_at,
                    notes: values.notes || null,
                },
                freeze: true,
                freeze_message: __("Recording repayment..."),
                callback(r) {
                    if (r.message) {
                        dialog.hide();
                        frappe.show_alert({
                            message: __(
                                "Repayment recorded — Floatra " +
                                "balance updated.",
                            ),
                            indicator: "green",
                        });
                        frm.reload_doc();
                    }
                },
            });
        },
    });
    dialog.show();
}
