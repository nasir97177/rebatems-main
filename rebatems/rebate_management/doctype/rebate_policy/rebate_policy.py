# Copyright (c) 2021, Aakvatech Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, add_days


class RebatePolicy(Document):
    def validate(self):
        if self.start_date >= self.end_date:
            frappe.throw(
                _("The start date cannot be equal to or less than the end date")
            )
        self.update_totals()
        self.update_status()

    def before_submit(self):
        now_date = nowdate()
        if str(self.end_date) > now_date:
            frappe.throw(_("Not allowed to Submit before the period ends"))
        if self.status not in ["Achieved", "Missed"]:
            frappe.throw(
                _(
                    "Submition can only be done when the status is 'Missed' or 'Achieved'"
                )
            )
        self.create_voucher()

    def on_submit(self):
        pass

    def update_totals(self):
        self.total_qty_achieved = 0
        self.total_amount_achieved = 0
        for item in self.items:
            self.total_qty_achieved += item.qty_achieved or 0
            self.total_amount_achieved += item.amount_achieved or 0
        if (
            self.target_type == "Quantity"
            and self.total_qty_achieved
            and self.target_qty
        ):
            self.percentage = self.total_qty_achieved / self.target_qty * 100
            self.total_amount = self.total_qty_achieved * self.rebate_per_qty
        elif (
            self.target_type == "Amount"
            and self.total_amount_achieved
            and self.target_amount
        ):
            self.percentage = self.total_amount_achieved / self.target_amount * 100
            self.total_amount = (
                self.total_amount_achieved * self.rebate_percentage / 100
            )

    @property
    def accepted_items(self):
        return [i.item for i in self.items]

    def update_status(self):
        if self.status in ["Completed", "Missed"]:
            return
        now_date = nowdate()
        if str(self.start_date) > now_date and self.status != "Setup":
            self.status = "Setup"
        elif (
            str(self.start_date) <= now_date
            and now_date <= str(self.end_date)
            and self.status != "Running"
        ):
            self.status = "Running"
        if str(self.end_date) < now_date:
            if (
                self.target_type == "Quantity"
                and self.total_qty_achieved >= self.target_qty
            ):
                if self.voucher:
                    self.status = "Completed"
                else:
                    self.status = "Achieved"
            elif (
                self.target_type == "Amount"
                and self.total_amount_achieved >= self.target_amount
            ):
                if self.voucher:
                    self.status = "Completed"
                else:
                    self.status = "Achieved"
            else:
                self.status = "Missed"

    def create_voucher(self):
        now_date = nowdate()
        if (
            self.docstatus == 0
            and self.status == "Achieved"
            and self.type == "Purchase"
            and self.create_lcv
        ):
            purchase_receipts, items_totals = get_purchases_for_rebate(self)
            voucher = frappe.new_doc("Landed Cost Voucher")
            voucher.company = self.company
            voucher.posting_date = now_date
            voucher.purchase_receipts = []
            for purcahse in purchase_receipts:
                row = voucher.append("purchase_receipts", {})
                row.update(purcahse)
            voucher.distribute_charges_based_on = "Qty"
            voucher.total_taxes_and_charges = self.total_amount * -1
            voucher.get_items_from_purchase_receipts()
            items = []
            for item in voucher.items:
                if item.item_code in self.accepted_items:
                    items.append(item)
            voucher.items = items
            voucher.taxes = []
            expense = voucher.append("taxes")
            expense.amount = self.total_amount * -1
            expense.expense_account = self.rebate_account
            expense.description = self.rebate_name
            expense.account_currency = frappe.get_value(
                "Account", self.rebate_account, "account_currency"
            )
            expense.exchange_rate = 1
            voucher.insert(ignore_permissions=True, ignore_mandatory=True)
            self.voucher = voucher.name
            frappe.msgprint(
                _("Landed Cost Voucher {0} Created").format(self.voucher), alert=True
            )
            frappe.db.commit()


def process_rebates():
    now_date = nowdate()
    rebates_list = frappe.get_all(
        "Rebate Policy",
        filters={
            "docstatus": 0,
            "start_date": ["<=", now_date],
            "end_date": [">=", add_days(now_date, -1)],
            "rebate_status": ["not in", ["Completed", "Missed"]],
        },
    )
    for reb in rebates_list:
        process_rebate(reb.name, now_date)


@frappe.whitelist()
def process_rebate(reb_name, now_date=None):
    if not now_date:
        now_date = nowdate()
    doc = frappe.get_doc("Rebate Policy", reb_name)
    if str(doc.end_date) < now_date:
        doc.update_status()
        doc.save()
    if (
        str(doc.start_date) > now_date
        or str(doc.end_date) < now_date
        or doc.rebate_status in ["Completed", "Missed"]
    ):
        return
    try:
        if doc.type == "Purchase":
            process_purchase_rebate(doc)
        elif doc.type == "Sales":
            process_sales_rebate(doc)
        elif doc.type == "Promotional Sales":
            process_promotional_rebate(doc)
        frappe.db.commit()
    except Exception as e:
        frappe.msgprint(str(e))
        frappe.log_error(frappe.get_traceback(), str(e))


def process_purchase_rebate(doc):
    purchase_receipts, items_totals = get_purchases_for_rebate(doc)
    for item in doc.items:
        if items_totals.get(item.item):
            totals = frappe._dict(items_totals.get(item.item))
            item.qty_achieved = totals.qty
            item.amount_achieved = totals.amount
    doc.save(ignore_permissions=True)


def get_purchases_for_rebate(doc):
    accepted_items = doc.accepted_items
    purchase_receipts = []
    items_liens = []
    items_totals = frappe._dict()
    init_list = []

    in_list = frappe.get_all(
        "Purchase Invoice",
        filters={
            "docstatus": 1,
            "update_stock": 1,
            "supplier": doc.supplier,
            "posting_date": ["between", [doc.start_date, doc.end_date]],
        },
        fields=["name"],
    )
    for i in in_list:
        i["doctype"] = "Purchase Invoice"
        init_list.append(i)

    res_list = frappe.get_all(
        "Purchase Receipt",
        filters={
            "docstatus": 1,
            "supplier": doc.supplier,
            "posting_date": ["between", [doc.start_date, doc.end_date]],
        },
        fields=["name"],
    )
    for r in res_list:
        r["doctype"] = "Purchase Receipt"
        init_list.append(r)

    for el in init_list:
        accepted = False
        doc = frappe.get_doc(el.doctype, el.name)
        for item in doc.items:
            if item.item_code in accepted_items:
                accepted = True
                item_dict = frappe._dict()
                item_dict.item_code = item.item_code
                item_dict.qty = item.stock_qty
                item_dict.description = item.description
                item_dict.receipt_document_type = el.doctype
                item_dict.receipt_document = el.name
                item_dict.purchase_receipt_item = item.name
                item_dict.base_net_amount = item.base_net_amount
                items_liens.append(item_dict)
        if accepted:
            doc_dict = frappe._dict()
            doc_dict.receipt_document_type = doc.doctype
            doc_dict.receipt_document = doc.name
            doc_dict.supplier = doc.supplier
            doc_dict.posting_date = doc.posting_date
            doc_dict.grand_total = doc.grand_total
            purchase_receipts.append(doc_dict)

    for item in items_liens:
        items_totals.setdefault(item.item_code, {"qty": 0, "amount": 0})
        items_totals[item.item_code]["qty"] += item.qty
        items_totals[item.item_code]["amount"] += item.base_net_amount

    return purchase_receipts, items_totals


def process_sales_rebate(doc):
    pass


def process_promotional_rebate(doc):
    pass


def get_sales_for_rebate(doc):
    pass


@frappe.whitelist()
def get_supplier_items(doctype, txt, searchfield, start, page_len, filters):
    res = []
    cust_filters = {}
    if txt:
        cust_filters["parent"] = ["like", f"%{txt}%"]
    if filters.get("supplier"):
        cust_filters["supplier"] = filters.get("supplier")
    data = frappe.get_list(
        "Item Supplier",
        filters=cust_filters,
        fields=["parent as name"],
        page_length=page_len,
        start=start,
        as_list=True,
    )
    for item in data:
        i = [item[0]]
        i.append(frappe.get_value("Item", item[0], "item_name"))
        res.append(i)
    return res
