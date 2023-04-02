// Copyright (c) 2021, Aakvatech Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('Rebate Policy', {
	refresh: function (frm) {
		frm.set_query('cost_center', () => {
			return {
				filters: {
					company: frm.doc.company
				}
			};
		});
		frm.set_query('rebate_account', () => {
			return {
				filters: {
					company: frm.doc.company,
					account_type: ['in', ["Tax", "Chargeable", "Income Account", "Expenses Included In Valuation", "Expenses Included In Asset Valuation"]],
				}
			};
		});
		frm.set_query('item', 'items', () => {
			return {
				query: 'rebatems.rebate_management.doctype.rebate_policy.rebate_policy.get_supplier_items',
				filters: { 'supplier': frm.doc.supplier }
			};
		});
	}
});
