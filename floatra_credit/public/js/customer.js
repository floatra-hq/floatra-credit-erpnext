// -*- coding: utf-8 -*-
// Customer client script — surfaces the Floatra tab + quick actions
// for ops users.
//
// Buttons (under the "Floatra" group):
//   - "Onboard with Floatra" — visible when not enrolled. Retries the
//     onboarding that runs when the Customer is created
//     (`floatra_credit.api.action_onboard_customer`).
//   - "Sync from Floatra" — visible when enrolled. Pulls this Customer's
//     credit status (`floatra_credit.api.action_sync_customer`).
//   - "Get KYC Link" — visible when enrolled. Calls the whitelisted
//     `floatra_credit.api.action_get_kyc_link`, which asks Floatra
//     for a hosted KYC link (no BVN/NIN/selfie is sent — the merchant
//     enters them on Floatra's page) and stores it on the Customer.
//     The link is shown in a dialog with Copy and Email actions.
//     With a sandbox key Floatra returns a simulated result instead.
//   - "Copy KYC Link" / "Email KYC Link" — visible while an unexpired
//     link is stored. Email opens Frappe's standard email composer.
//   - "Update Bank Account" — visible when enrolled. Opens a
//     dialog to upsert the merchant's primary bank account on
//     Floatra. The Floatra side encrypts at rest and returns the
//     masked form for the chatter audit (P2-13 batch 11).

frappe.ui.form.on("Customer", {
    refresh(frm) {
        if (frm.is_new()) return;
        if (!frm.doc.floatra_merchant_id) {
            // Onboarding runs automatically when the Customer is created;
            // this retries it (e.g. after adding a Nigerian mobile number).
            frm.add_custom_button(
                __("Onboard with Floatra"),
                () => {
                    frappe.call({
                        method: "floatra_credit.api.action_onboard_customer",
                        args: { customer: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Onboarding with Floatra..."),
                        callback() {
                            frm.reload_doc();
                        },
                    });
                },
                __("Floatra"),
            );
            return;
        }

        frm.add_custom_button(
            __("Sync from Floatra"),
            () => {
                frappe.call({
                    method: "floatra_credit.api.action_sync_customer",
                    args: { customer: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Syncing from Floatra..."),
                    callback() {
                        frm.reload_doc();
                    },
                });
            },
            __("Floatra"),
        );

        frm.add_custom_button(
            __("Get KYC Link"),
            () => {
                frappe.confirm(
                    __("Request a new Floatra KYC link for this customer? Any link sent earlier stops working."),
                    () => request_kyc_link(frm),
                );
            },
            __("Floatra"),
        );

        if (kyc_link_active(frm)) {
            frm.add_custom_button(
                __("Copy KYC Link"),
                () => frappe.utils.copy_to_clipboard(frm.doc.floatra_kyc_url),
                __("Floatra"),
            );
            frm.add_custom_button(
                __("Email KYC Link"),
                () => email_kyc_link(
                    frm,
                    frm.doc.floatra_kyc_url,
                    frm.doc.floatra_kyc_url_expires_at,
                ),
                __("Floatra"),
            );
        }

        frm.add_custom_button(
            __("Update Bank Account"),
            () => open_bank_account_dialog(frm),
            __("Floatra"),
        );
    },
});


function open_bank_account_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Update Floatra Bank Account"),
        fields: [
            {
                fieldtype: "Data",
                fieldname: "bank_code",
                label: __("Bank Code"),
                reqd: 1,
                description: __(
                    "3-10 digit CBN bank code (e.g. 058 for GTBank).",
                ),
            },
            {
                fieldtype: "Data",
                fieldname: "account_number",
                label: __("Account Number"),
                reqd: 1,
                description: __("Exactly 10 digits."),
            },
            {
                fieldtype: "Data",
                fieldname: "account_name",
                label: __("Account Name"),
                reqd: 1,
                description: __(
                    "As registered with the bank (≤ 100 characters).",
                ),
            },
            {
                fieldtype: "Check",
                fieldname: "is_primary",
                label: __("Set as primary"),
                default: 1,
            },
        ],
        primary_action_label: __("Save to Floatra"),
        primary_action(values) {
            frappe.call({
                method: "floatra_credit.api.action_set_bank_account",
                args: {
                    customer: frm.doc.name,
                    bank_code: (values.bank_code || "").trim(),
                    account_number: (values.account_number || "").trim(),
                    account_name: (values.account_name || "").trim(),
                    is_primary: values.is_primary ? 1 : 0,
                },
                freeze: true,
                freeze_message: __("Saving to Floatra..."),
                callback() {
                    d.hide();
                    frm.reload_doc();
                    frappe.show_alert({
                        message: __(
                            "Bank account saved. Floatra encrypts the account number at rest.",
                        ),
                        indicator: "green",
                    });
                },
            });
        },
    });
    d.show();
}


function kyc_link_active(frm) {
    if (!frm.doc.floatra_kyc_url || !frm.doc.floatra_kyc_url_expires_at) {
        return false;
    }
    // Stored in the system timezone, like every Frappe Datetime.
    return frappe.datetime.get_diff(
        frm.doc.floatra_kyc_url_expires_at,
        frappe.datetime.now_datetime(),
    ) >= 0;
}


function request_kyc_link(frm) {
    frappe.call({
        method: "floatra_credit.api.action_get_kyc_link",
        args: { customer: frm.doc.name },
        freeze: true,
        freeze_message: __("Asking Floatra for a KYC link..."),
        callback(r) {
            const out = r.message || {};
            frm.reload_doc();
            if (out.kind === "hosted_link") {
                show_kyc_link_dialog(frm, out.kyc_url, out.expires_at);
            } else {
                frappe.show_alert({
                    message: __("Sandbox: simulated KYC result {0}", [
                        out.status || "",
                    ]),
                    indicator: "blue",
                });
            }
        },
    });
}


function show_kyc_link_dialog(frm, url, expires_at) {
    const d = new frappe.ui.Dialog({
        title: __("Floatra KYC Link"),
        fields: [
            {
                fieldtype: "Data",
                fieldname: "kyc_url",
                label: __("KYC Link"),
                default: url,
                read_only: 1,
            },
            {
                fieldtype: "HTML",
                fieldname: "help",
                options: `<p class="text-muted">${__(
                    "Send this link to the merchant (SMS, WhatsApp or email). They open it on their phone, confirm a code sent to their phone number on Floatra, enter their NIN or BVN and take a selfie. It works once and expires on {0}. Getting a new link revokes this one.",
                    [frappe.utils.escape_html(frappe.datetime.str_to_user(expires_at))],
                )}</p>`,
            },
        ],
        primary_action_label: __("Copy Link"),
        primary_action() {
            frappe.utils.copy_to_clipboard(url);
        },
        secondary_action_label: __("Email to Customer"),
        secondary_action() {
            d.hide();
            email_kyc_link(frm, url, expires_at);
        },
    });
    d.show();
}


function email_kyc_link(frm, url, expires_at) {
    if (!url) return;
    const expires = frappe.datetime.str_to_user(expires_at);
    // Staff review and send it themselves; nothing is sent automatically.
    new frappe.views.CommunicationComposer({
        doc: frm.doc,
        frm: frm,
        recipients: frm.doc.email_id || "",
        subject: __("Verify your identity with Floatra"),
        message: __(
            "<p>Hello,</p><p>To use credit on your orders, please verify your identity with Floatra. Open this link on your phone:</p><p><a href=\"{0}\">{0}</a></p><p>You will get a code by SMS on the phone number we have for you, then enter your NIN or BVN and take a selfie. The link works once and expires on {1}.</p>",
            [frappe.utils.escape_html(url), frappe.utils.escape_html(expires)],
        ),
    });
}
