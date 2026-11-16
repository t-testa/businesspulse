# BusinessPulse — product definition, v0.2

## Who is it for?

SMB owners and operators who want to see simple business KPIs from CSV exports without owning a BI stack. This is a *single-workspace-per-account* beta, not enterprise BI.

## Problem / job to be done

"I exported sales transactions from my system. Show my monthly revenue, cost, margin, sales volume and purchasing-customer count without learning Power BI."

## User journey

1. Sign up with username, email, business name and single currency.
2. Select a UTF-8 CSV, preview the first 10 rows locally, and review/correct suggested column mappings or choose a saved workspace mapping.
3. Submit the CSV and mapping once; the server validates the mapping and all rows before DB persistence. Optionally save the mapping after successful import.
4. See metrics and monthly revenue trend; filter all time / 30 days / 90 days / YTD.
5. Show/hide predefined KPI cards.
6. Export stored sale rows if needed.

## KPI definitions (v0.1)

**Data grain:** one row per unique, completed transaction (not a line-item; not one invoice plus its associated payments). Each transaction has one customer and one sale date, one amount, and one direct cost. All transaction amounts are in the workspace currency and exclude tax. Refunds and cancellations are out of scope.

- Revenue = SUM(sale.revenue).
- Direct cost = SUM(sale.cost).
- Gross profit = SUM(revenue) − SUM(direct_cost). Can be negative.
- Gross margin % = gross profit / revenue × 100; show N/A where revenue = 0 (undefined denominator).
- Transactions = COUNT(unique imported transaction_id) for the selected period.
- Average transaction value = SUM(revenue) / number of transactions, or 0 if no transactions.
- Purchasing customers = COUNT(DISTINCT customer_id) over the selected period.
- Revenue by month = SUM(revenue) grouped by transaction month for the selected period.

All filters are based on **sale date**. No comparison metrics or growth rates are claimed in v0.1. Transaction IDs are unique *within each organization*. Reuploading identical IDs is a no-op, while conflicts are rejected without modifying data.

## CSV contract

Required fields: transaction_id, date (YYYY-MM-DD), customer_id, revenue, cost. Source columns may have different names; each field must map to one distinct existing source column. Common alternate names are suggested (see `tracker/services.py`); ambiguous suggestions require a choice. Only UTF-8 comma-separated input, up to 2 MB / 5,000 rows / 100 columns, with headers at most 200 characters. Empty or duplicate/equivalent headers are rejected. Numeric values must be positive or zero with maximum 2 decimals. Negative refunds are not yet handled. No PII is required: customer_id should be a pseudonymous internal ID.

Saved mappings contain an organization, a name, five source-header references, and
an update timestamp, never CSV contents. They are reusable by header name rather
than column position. An existing name is replaced only as part of a successful
atomic import. Mappings are neither listed nor selectable across organizations.
All rows, not just the preview, are validated on the server; client-side checks
are only usability aids.

## Acceptance criteria for first working vertical slice

- New user can create and sign into a workspace.
- User can import example data and see correct totals; importing again does not inflate totals.
- All invalid or conflicting files fail with actionable error messages and no partial writes.
- A second user's data is never visible via dashboard, export, or upload routes.
- User can toggle KPI cards without changing metric formulas.
- CI passes and a deployed copy can serve `/health/` and an HTTPS login page.
- Database persists across deployment. Backups and restore procedure must be verified before accepting real client data.

## Out of scope (intentional)

Excel/XLSX, date/number-format conversion, mapping deletion/renaming management, per-industry templates, custom formulas, joins across files, permissioned multi-user organizations, automated ERP integrations, refunds, tax, multi-currency conversion, billing, AI insights, email verification, self-serve password resets, and production privacy/legal terms.

## Next releases

- **v0.2 (this release):** CSV-only local preview, mapping dropdowns, and saved workspace mappings. Excel support is deferred.
- **v0.3:** add expenses and invoice/receivables templates, industry KPI packs, richer date filters, import reconciliation.
- **v0.4:** custom KPI builder with a constrained safe expression language (no arbitrary Python/SQL), roles, alerting, secure sharing.
- **v1.0:** compliance/privacy workflows, billing, audited multi-tenant isolation, backups/restore drills, real customer onboarding.
