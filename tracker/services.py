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
from .models import Sale, SalesImport

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000

ALIASES = {
    'transaction_id': ('transaction_id', 'order_id', 'sale_id', 'invoice_id', 'id'),
    'date': ('date', 'sale_date', 'transaction_date', 'order_date', 'invoice_date'),
    'customer_id': ('customer_id', 'client_id', 'customer_ref', 'account_id'),
    'revenue': ('revenue', 'net_sales', 'sales_amount', 'amount', 'net_revenue', 'total'),
    'cost': ('cost', 'cogs', 'direct_cost', 'cost_of_goods_sold'),
}


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


def _columns(headers):
    norm_to_original = {}
    for header in headers:
        if header is None or not header.strip():
            raise CSVValidationError('CSV contains an empty column header.')
        normalized = _norm(header)
        if normalized in norm_to_original:
            raise CSVValidationError(f'Duplicate or equivalent CSV headers: {header!r}.')
        norm_to_original[normalized] = header

    mapped = {}
    for canonical, aliases in ALIASES.items():
        hits = [norm_to_original[_norm(alias)] for alias in aliases if _norm(alias) in norm_to_original]
        if not hits:
            raise CSVValidationError(f'Missing required column: {canonical}. Use the sample CSV or a supported header alias.')
        # Prefer canonical names; if alternative aliases coexist, require manual cleanup
        if len(hits) > 1:
            canonical_key = _norm(canonical)
            if canonical_key in norm_to_original:
                mapped[canonical] = norm_to_original[canonical_key]
            else:
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


def parse_sales_csv(uploaded_file) -> list[SaleRow]:
    """Parse the complete input before any database write; reject invalid imports atomically."""
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
        columns = _columns(reader.fieldnames)
        sales = []
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
            transaction_id = (record[columns['transaction_id']] or '').strip()
            customer_id = (record[columns['customer_id']] or '').strip()
            if not transaction_id or len(transaction_id) > 100:
                raise CSVValidationError(f'Row {n}: transaction_id is required and must be at most 100 characters.')
            if not customer_id or len(customer_id) > 100:
                raise CSVValidationError(f'Row {n}: customer_id is required and must be at most 100 characters.')
            if transaction_id in seen:
                raise CSVValidationError(f'Row {n}: transaction_id {transaction_id!r} occurs twice in the file.')
            seen.add(transaction_id)
            raw_date = (record[columns['date']] or '').strip()
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
                raise CSVValidationError(f'Row {n}: date must use YYYY-MM-DD format.')
            try:
                sale_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise CSVValidationError(f'Row {n}: invalid calendar date {raw_date!r}.') from exc
            sales.append(SaleRow(
                transaction_id=transaction_id,
                sale_date=sale_date,
                customer_id=customer_id,
                revenue=_amount(record[columns['revenue']], n, 'revenue'),
                cost=_amount(record[columns['cost']], n, 'cost'),
            ))
    except csv.Error as exc:
        raise CSVValidationError('Malformed CSV: ' + str(exc)) from exc
    if not sales:
        raise CSVValidationError('No data rows found in CSV.')
    return sales


@transaction.atomic
def save_sales_import(*, organization, user, file_name: str, rows: list[SaleRow]):
    """Reuploads are idempotent, but conflicting transaction IDs are rejected."""
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


def format_currency(value: Decimal, code: str) -> str:
    return f'{code} {value:,.2f}'


def report_for(organization, period='all'):
    """Metrics are computed solely from this organization's rows, at transaction grain."""
    qs = Sale.objects.filter(organization=organization)
    today = timezone.localdate()
    period_options = {'all': 'All time', '30': 'Last 30 days', '90': 'Last 90 days', 'ytd': 'Year to date'}
    if period not in period_options:
        period = 'all'
    if period in ('30', '90'):
        qs = qs.filter(sale_date__gte=today - timedelta(days=int(period) - 1), sale_date__lte=today)
    elif period == 'ytd':
        qs = qs.filter(sale_date__gte=date(today.year, 1, 1), sale_date__lte=today)

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
    return {
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
