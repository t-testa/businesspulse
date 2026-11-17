"""Validated CSV ingestion and repeatable, source-backed KPI calculations."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import io
import re

from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .kpis import KPI_DEFINITIONS
from .models import Expense, ExpenseImport, Organization, Sale, SalesImport

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 100
MAX_HEADER_LENGTH = 200

ALIASES = {
    'transaction_id': ('transaction_id', 'order_id', 'sale_id', 'invoice_id', 'id'),
    'date': ('date', 'sale_date', 'transaction_date', 'order_date', 'invoice_date'),
    'customer_id': ('customer_id', 'client_id', 'customer_ref', 'account_id'),
    'revenue': ('revenue', 'net_sales', 'sales_amount', 'amount', 'net_revenue', 'total'),
    'cost': ('cost', 'cogs', 'direct_cost', 'cost_of_goods_sold'),
}


EXPENSE_ALIASES = {
    'expense_id': ('expense_id', 'expense_ref', 'payment_id', 'id'),
    'date': ('date', 'expense_date', 'payment_date', 'transaction_date'),
    'category': ('category', 'expense_category', 'expense_type'),
    'amount': ('amount', 'expense_amount', 'total'),
}


@dataclass(frozen=True)
class ExpenseRow:
    expense_id: str
    date: date
    category: str
    amount: Decimal


class CSVValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SaleRow:
    transaction_id: str
    sale_date: date
    customer_id: str
    revenue: Decimal
    cost: Decimal


def _norm(header: str) -> str:
    return re.sub(r'[^a-z0-9]', '', header.strip().casefold())


def _columns(headers, mapping=None, aliases=ALIASES):
    if len(headers) > MAX_COLUMNS:
        raise CSVValidationError(f'CSV has too many columns (maximum {MAX_COLUMNS}).')
    norm_to_original = {}
    for header in headers:
        if header is None or not header.strip():
            raise CSVValidationError('CSV contains an empty column header.')
        if len(header) > MAX_HEADER_LENGTH:
            raise CSVValidationError(f'CSV column headers must be at most {MAX_HEADER_LENGTH} characters.')
        normalized = _norm(header) or header.strip().casefold()
        if normalized in norm_to_original:
            raise CSVValidationError(f'Duplicate or equivalent CSV headers: {header!r}.')
        norm_to_original[normalized] = header

    if mapping is not None:
        if not isinstance(mapping, dict) or set(mapping) != set(aliases):
            raise CSVValidationError('Map exactly these required fields: ' + ', '.join(aliases) + '.')
        if any(not isinstance(value, str) or value not in headers for value in mapping.values()):
            raise CSVValidationError('Every mapped column must exist in this CSV. Review the selected mapping.')
        if len(set(mapping.values())) != len(aliases):
            raise CSVValidationError('Each required field must use a different source column.')
        return dict(mapping)

    mapped = {}
    for canonical, candidates in aliases.items():
        hits = [norm_to_original[_norm(alias)] for alias in candidates if _norm(alias) in norm_to_original]
        if not hits:
            raise CSVValidationError(f'Missing required column: {canonical}. Use the sample CSV or a supported header alias.')
        # Never silently choose between two possible sources for a required field.
        if len(hits) > 1:
            raise CSVValidationError(f'Ambiguous columns for {canonical}: {", ".join(hits)}.')
        else:
            mapped[canonical] = hits[0]
    if len(set(mapped.values())) != len(mapped):
        raise CSVValidationError('A source column cannot be mapped to more than one required field.')
    return mapped


_AMOUNT_PATTERN = re.compile(r'(?:\d+(?:\.\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?)\Z')


def _amount(raw: str, row_number: int, label: str) -> Decimal:
    value = (raw or '').strip()
    if not _AMOUNT_PATTERN.fullmatch(value):
        raise CSVValidationError(f'Row {row_number}: {label} must be a nonnegative number with at most two decimal places.')
    try:
        amount = Decimal(value.replace(',', ''))
    except InvalidOperation as exc:
        raise CSVValidationError(f'Row {row_number}: invalid {label}.') from exc
    if amount >= Decimal('1000000000000'):
        raise CSVValidationError(f'Row {row_number}: {label} exceeds the supported amount.')
    return amount.quantize(Decimal('0.01'))


def parse_sales_csv(uploaded_file, mapping=None) -> list[SaleRow]:
    return parse_sales_upload(uploaded_file, mapping)[0]


def parse_sales_upload(uploaded_file, mapping=None) -> tuple[list[SaleRow], dict[str, str]]:
    return _parse_upload(uploaded_file, mapping, ALIASES, expense=False)


def parse_expense_upload(uploaded_file, mapping=None):
    return _parse_upload(uploaded_file, mapping, EXPENSE_ALIASES, expense=True)


def _parse_upload(uploaded_file, mapping, aliases, *, expense):
    """Validate the complete bounded CSV before either dataset can write rows."""
    if uploaded_file.size > MAX_CSV_BYTES:
        raise CSVValidationError('File too large (maximum 2 MB).')
    raw_bytes = uploaded_file.read(MAX_CSV_BYTES + 1)
    if len(raw_bytes) > MAX_CSV_BYTES:
        raise CSVValidationError('File too large (maximum 2 MB).')
    if b'\x00' in raw_bytes:
        raise CSVValidationError('This does not look like a CSV file.')
    try:
        text = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise CSVValidationError('Save the file as UTF-8 CSV and try again.') from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
        if not reader.fieldnames:
            raise CSVValidationError('CSV is empty or missing a header row.')
        columns = _columns(reader.fieldnames, mapping, aliases)
        rows = []
        seen = set()
        for n, record in enumerate(reader, start=2):
            if n > MAX_ROWS + 1:
                raise CSVValidationError(f'Too many rows (maximum {MAX_ROWS}).')
            if None in record:
                raise CSVValidationError(f'Row {n}: too many cells. Check separators and quotation marks.')
            if any(value is None for value in record.values()):
                raise CSVValidationError(f'Row {n}: missing cells. Check separators.')
            if not any((value or '').strip() for value in record.values()):
                continue
            id_field, label_field = ('expense_id', 'category') if expense else ('transaction_id', 'customer_id')
            transaction_id = record[columns[id_field]].strip()
            customer_id = record[columns[label_field]].strip()
            for label, value in [(id_field, transaction_id), (label_field, customer_id)]:
                if not value or len(value) > 100:
                    raise CSVValidationError(f'Row {n}: {label} is required and must be at most 100 characters.')
            if transaction_id in seen:
                raise CSVValidationError(f'Row {n}: {id_field} {transaction_id!r} occurs twice in the file.')
            seen.add(transaction_id)
            raw_date = (record[columns['date']] or '').strip()
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
                raise CSVValidationError(f'Row {n}: date must use YYYY-MM-DD format.')
            try:
                sale_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise CSVValidationError(f'Row {n}: invalid calendar date {raw_date!r}.') from exc
            if expense:
                rows.append(ExpenseRow(transaction_id, sale_date, customer_id,
                                        _amount(record[columns['amount']], n, 'amount')))
            else:
                rows.append(SaleRow(transaction_id, sale_date, customer_id,
                                     _amount(record[columns['revenue']], n, 'revenue'),
                                     _amount(record[columns['cost']], n, 'cost')))
    except csv.Error as exc:
        raise CSVValidationError('Malformed CSV: ' + str(exc)) from exc
    if not rows:
        raise CSVValidationError('No data rows found in CSV.')
    return rows, columns


@transaction.atomic
def save_sales_import(*, organization, user, file_name: str, rows: list[SaleRow]):
    """Reuploads are idempotent, but conflicting transaction IDs are rejected."""
    # Serialize imports before reading existing sales. Locking Sale rows alone
    # cannot protect IDs which have not been inserted yet. PostgreSQL holds this
    # workspace lock until commit/rollback; other workspaces remain independent.
    # SQLite ignores select_for_update and remains for single-user development.
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    if organization.owner_id != user.pk:
        raise CSVValidationError('Only the workspace owner can import sales.')
    existing = {
        row.transaction_id: row for row in
        Sale.objects.filter(organization=organization, transaction_id__in=[item.transaction_id for item in rows])
    }
    to_add = []
    duplicates = 0
    for row in rows:
        found = existing.get(row.transaction_id)
        if found:
            if (found.sale_date, found.customer_id, found.revenue, found.cost) != (
                row.sale_date, row.customer_id, row.revenue, row.cost
            ):
                raise CSVValidationError(
                    f'Transaction {row.transaction_id!r} already exists with different data. '
                    'No changes were made; use a fresh ID or resolve the source conflict.'
                )
            duplicates += 1
        else:
            to_add.append(row)
    batch = SalesImport.objects.create(
        organization=organization, uploaded_by=user, filename=file_name[:200],
        inserted_count=len(to_add), duplicate_count=duplicates,
    )
    Sale.objects.bulk_create([
        Sale(organization=organization, import_batch=batch,
             transaction_id=row.transaction_id, sale_date=row.sale_date,
             customer_id=row.customer_id, revenue=row.revenue, cost=row.cost)
        for row in to_add
    ])
    return batch


@transaction.atomic
def save_expense_import(*, organization, user, file_name, rows):
    # Use the same organization lock as sales, including when there are no rows yet.
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    if organization.owner_id != user.pk:
        raise CSVValidationError('Only the workspace owner can import expenses.')
    existing = {row.expense_id: row for row in Expense.objects.filter(
        organization=organization, expense_id__in=[item.expense_id for item in rows])}
    to_add = []
    duplicates = 0
    for row in rows:
        found = existing.get(row.expense_id)
        if found:
            if (found.date, found.category, found.amount) != (row.date, row.category, row.amount):
                raise CSVValidationError(f'Expense {row.expense_id!r} already exists with different data. '
                                         'No changes were made; resolve the source conflict.')
            duplicates += 1
        else:
            to_add.append(row)
    batch = ExpenseImport.objects.create(organization=organization, uploaded_by=user,
        filename=file_name[:200], inserted_count=len(to_add), duplicate_count=duplicates)
    Expense.objects.bulk_create([Expense(organization=organization, import_batch=batch,
        expense_id=row.expense_id, date=row.date, category=row.category, amount=row.amount) for row in to_add])
    return batch


def _in_period(qs, field, period, today):
    if period in ('30', '90'):
        start = today - timedelta(days=int(period) - 1)
    elif period == 'ytd':
        start = date(today.year, 1, 1)
    else:
        return qs
    return qs.filter(**{field + '__gte': start, field + '__lte': today})


def format_currency(value: Decimal, code: str) -> str:
    return f'{code} {value:,.2f}'


def report_for(organization, period='all'):
    """Metrics are computed solely from this organization's rows, at transaction grain."""
    qs = Sale.objects.filter(organization=organization)
    today = timezone.localdate()
    period_options = {'all': 'All time', '30': 'Last 30 days', '90': 'Last 90 days', 'ytd': 'Year to date'}
    if period not in period_options:
        period = 'all'
    qs = _in_period(qs, 'sale_date', period, today)

    summary = qs.aggregate(revenue=Sum('revenue'), cost=Sum('cost'),
                           transactions=Count('pk'), customers=Count('customer_id', distinct=True))
    revenue = summary['revenue'] or Decimal('0.00')
    cost = summary['cost'] or Decimal('0.00')
    count = summary['transactions']
    gross_profit = revenue - cost
    margin = gross_profit / revenue * 100 if revenue else None
    aov = revenue / count if count else Decimal('0')
    code = organization.currency
    values = {
        'revenue': format_currency(revenue, code),
        'gross_profit': format_currency(gross_profit, code),
        'margin': f'{margin:.1f}%' if margin is not None else 'N/A',
        'transactions': f'{count:,}',
        'aov': format_currency(aov, code),
        'customers': f"{summary['customers']:,}",
    }
    selected = [slug for slug in organization.visible_kpis if slug in KPI_DEFINITIONS]
    cards = [dict(slug=slug, value=values[slug], **KPI_DEFINITIONS[slug]) for slug in selected]

    monthly = list(qs.annotate(month=TruncMonth('sale_date'))
                   .values('month').annotate(revenue=Sum('revenue'))
                   .order_by('month'))[-12:]
    max_revenue = max((entry['revenue'] for entry in monthly), default=Decimal('0'))
    chart_rows = [
        {
            'label': entry['month'].strftime('%b %Y'),
            'revenue': format_currency(entry['revenue'], code),
            'width': round(entry['revenue'] / max_revenue * 100) if max_revenue else 0,
        }
        for entry in monthly
    ]
    expense_qs = _in_period(organization.expenses.all(), 'date', period, today)
    # Import history establishes availability, not the selected period's row count.
    expense_available = organization.expense_imports.exists()
    expense_total = expense_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    operating_result = gross_profit - expense_total if expense_available else None
    operating_margin = operating_result / revenue * 100 if expense_available and revenue else None
    expense_cards = [
        {'label': 'Total operating expenses', 'value': format_currency(expense_total, code) if expense_available else 'Not imported'},
        {'label': 'Estimated operating result', 'value': format_currency(operating_result, code) if expense_available else 'N/A'},
        {'label': 'Estimated operating margin', 'value': f'{operating_margin:.1f}%' if operating_margin is not None else 'N/A'},
    ]
    categories = [{'label': row['category'], 'amount': format_currency(row['total'], code)}
                  for row in expense_qs.values('category').annotate(total=Sum('amount')).order_by('-total', 'category')]
    expense_months = list(expense_qs.annotate(month=TruncMonth('date')).values('month')
                          .annotate(total=Sum('amount')).order_by('month'))[-12:]
    max_expense = max((row['total'] for row in expense_months), default=Decimal('0'))
    expense_chart = [{'label': row['month'].strftime('%b %Y'), 'amount': format_currency(row['total'], code),
                      'width': round(row['total'] / max_expense * 100) if max_expense else 0}
                     for row in expense_months]
    return {
        'expense_cards': expense_cards, 'expense_available': expense_available,
        'expense_count': expense_qs.count(), 'expense_categories': categories, 'expense_chart': expense_chart,
        'cards': cards, 'has_data': count > 0, 'chart_rows': chart_rows,
        'period': period, 'period_name': period_options[period],
        'period_options': period_options,
        'transaction_count': count,
    }


def safe_csv_cell(value: str) -> str:
    """Prevent spreadsheet formula execution in exported identifiers."""
    text = str(value)
    if text.startswith(('\t', '\r', '\n')) or text.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + text
    return text
