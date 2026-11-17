import csv
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import CharField, Value
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import ReversalForm, SignupForm, UploadForm
from .kpis import KPI_DEFINITIONS
from .models import ColumnMapping, ExpenseColumnMapping, ExpenseImport, Organization, Sale, SalesImport
from .services import (ALIASES, MAX_COLUMNS, MAX_CSV_BYTES, MAX_HEADER_LENGTH, CSVValidationError,
                       parse_sales_upload, report_for, safe_csv_cell, save_sales_import,
                       EXPENSE_ALIASES, parse_expense_upload, save_expense_import)


def signup(request):
    if not settings.PUBLIC_SIGNUP_ENABLED:
        raise Http404
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = form.save(commit=False)
            user.email = form.cleaned_data['email']
            user.save()
            Organization.objects.create(owner=user, name=form.cleaned_data['organization_name'],
                                        currency=form.cleaned_data['currency'])
        login(request, user)
        messages.success(request, 'Your workspace is ready. Start with the sample CSV.')
        return redirect('dashboard')
    return render(request, 'tracker/signup.html', {'form': form})


@login_required
def dashboard(request):
    organization = get_object_or_404(Organization, owner=request.user)
    report = report_for(organization, period=request.GET.get('period', 'all'))
    return render(request, 'tracker/dashboard.html', {
        'organization': organization, 'report': report,
        'expense_imports': organization.expense_imports.select_related('uploaded_by')[:5],
        'imports': organization.imports.select_related('uploaded_by')[:5],
    })


@login_required
@require_http_methods(['GET', 'POST'])
def upload_sales(request):
    return _upload(request, expense=False)


@login_required
@require_http_methods(['GET', 'POST'])
def upload_expenses(request):
    return _upload(request, expense=True)


def _upload(request, *, expense):
    aliases = EXPENSE_ALIASES if expense else ALIASES
    parser = parse_expense_upload if expense else parse_sales_upload
    importer = save_expense_import if expense else save_sales_import
    mapping_model = ExpenseColumnMapping if expense else ColumnMapping
    dataset = 'expenses' if expense else 'sales'
    organization = get_object_or_404(Organization, owner=request.user)
    form = UploadForm(request.POST if request.method == 'POST' else None, request.FILES or None,
                      organization=organization, expense=expense)
    if getattr(request, 'csv_upload_error', None):
        form.is_valid()
        form.add_error(None, request.csv_upload_error)
    if request.method == 'POST' and form.is_valid():
        uploaded = form.cleaned_data['file']
        try:
            mapping = form.cleaned_data['mapping']
            saved = form.cleaned_data['saved_mapping']
            if mapping is None and saved is not None:
                mapping = saved.columns
            rows, columns = parser(uploaded, mapping)
            # Keep saved metadata and imported records in the same transaction. The import
            # service's organization lock also serializes same-name mapping updates.
            with transaction.atomic():
                batch = importer(organization=organization, user=request.user,
                                 file_name=uploaded.name, rows=rows)
                if form.cleaned_data['mapping_name']:
                    mapping_model.objects.update_or_create(
                        organization=organization, name=form.cleaned_data['mapping_name'],
                        defaults={'columns': columns},
                    )
        except CSVValidationError as exc:
            form.add_error('file', str(exc))
        else:
            messages.success(request, f'Import complete: {batch.inserted_count} new {dataset}, '
                                      f'{batch.duplicate_count} identical duplicates skipped.')
            return redirect('dashboard')
    return render(request, 'tracker/upload.html', {
        'organization': organization, 'form': form, 'dataset': dataset, 'expense': expense,
        'upload_url': 'upload_expenses' if expense else 'upload_sales',
        'mapping_fields': [(key, key.replace('_', ' ').capitalize()) for key in aliases],
        'preview_config': {'aliases': aliases, 'maxBytes': MAX_CSV_BYTES,
                           'maxColumns': MAX_COLUMNS, 'maxHeaderLength': MAX_HEADER_LENGTH},
        'saved_mappings': list(mapping_model.objects.filter(organization=organization).values('id', 'columns')),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def preferences(request):
    organization = get_object_or_404(Organization, owner=request.user)
    if request.method == 'POST':
        chosen = request.POST.getlist('kpis')
        if not chosen or any(slug not in KPI_DEFINITIONS for slug in chosen):
            messages.error(request, 'Select at least one valid KPI.')
        else:
            organization.visible_kpis = [slug for slug in KPI_DEFINITIONS if slug in chosen]
            organization.save(update_fields=['visible_kpis'])
            messages.success(request, 'Dashboard preferences saved.')
            return redirect('dashboard')
    choices = [dict(slug=slug, selected=slug in organization.visible_kpis, **meta)
               for slug, meta in KPI_DEFINITIONS.items()]
    return render(request, 'tracker/preferences.html', {'organization': organization, 'choices': choices})


@login_required
def export_sales(request):
    organization = get_object_or_404(Organization, owner=request.user)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="businesspulse_sales.csv"'
    writer = csv.writer(response)
    writer.writerow(['transaction_id', 'date', 'customer_id', 'revenue', 'cost'])
    for sale in Sale.objects.filter(organization=organization, is_active=True).order_by('sale_date', 'pk').iterator():
        writer.writerow([safe_csv_cell(sale.transaction_id), sale.sale_date.isoformat(),
                         safe_csv_cell(sale.customer_id), str(sale.revenue), str(sale.cost)])
    return response


@login_required
@require_http_methods(['GET'])
def manage_imports(request):
    organization = get_object_or_404(Organization, owner=request.user)
    fields = ('id', 'filename', 'created_at', 'inserted_count', 'duplicate_count', 'reversed_at', 'dataset')
    sales = SalesImport.objects.filter(organization=organization).order_by().annotate(
        dataset=Value('sales', output_field=CharField())).values(*fields)
    expenses = ExpenseImport.objects.filter(organization=organization).order_by().annotate(
        dataset=Value('expenses', output_field=CharField())).values(*fields)
    entries = sales.union(expenses, all=True).order_by('-created_at', 'dataset', '-id')
    return render(request, 'tracker/imports.html', {
        'organization': organization, 'page': Paginator(entries, 25).get_page(request.GET.get('page'))})


@login_required
@require_http_methods(['GET', 'POST'])
def import_detail(request, dataset, batch_id):
    from .import_management import preview_import, reverse_import
    organization = get_object_or_404(Organization, owner=request.user)
    status = 200
    error = None
    if request.method == 'POST':
        form = ReversalForm(request.POST)
        if form.is_valid():
            try:
                changed = reverse_import(organization=organization, user=request.user, dataset=dataset,
                    batch_id=batch_id, token=form.cleaned_data['preview_token'], reason=form.cleaned_data['reason'])
            except CSVValidationError as exc:
                error = str(exc)
                status = 409
            else:
                messages.success(request, 'Import reversed. Original records retained.' if changed else 'Import was already reversed; no changes made.')
                return redirect('import_detail', dataset=dataset, batch_id=batch_id)
        else:
            error = 'Provide a reason and explicitly confirm after reviewing the impact.'
            status = 400
    preview = preview_import(organization=organization, user=request.user, dataset=dataset, batch_id=batch_id)
    # New preview means a fresh, unchecked confirmation, including on stale/invalid POSTs.
    form = ReversalForm(initial={'preview_token': preview['token']})
    return render(request, 'tracker/import_detail.html', {**preview, 'form': form, 'error': error}, status=status)
