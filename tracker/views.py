import csv
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import SignupForm, UploadForm
from .kpis import KPI_DEFINITIONS
from .models import Organization, Sale
from .services import CSVValidationError, parse_sales_csv, report_for, safe_csv_cell, save_sales_import


def signup(request):
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
        'imports': organization.imports.select_related('uploaded_by')[:5],
    })


@login_required
@require_http_methods(['GET', 'POST'])
def upload_sales(request):
    organization = get_object_or_404(Organization, owner=request.user)
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        uploaded = form.cleaned_data['file']
        try:
            rows = parse_sales_csv(uploaded)
            batch = save_sales_import(organization=organization, user=request.user,
                                      file_name=uploaded.name, rows=rows)
        except CSVValidationError as exc:
            form.add_error('file', str(exc))
        else:
            messages.success(request, f'Import complete: {batch.inserted_count} new sales, '
                                      f'{batch.duplicate_count} identical duplicates skipped.')
            return redirect('dashboard')
    return render(request, 'tracker/upload.html', {'organization': organization, 'form': form})


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
    for sale in Sale.objects.filter(organization=organization).order_by('sale_date', 'pk').iterator():
        writer.writerow([safe_csv_cell(sale.transaction_id), sale.sale_date.isoformat(),
                         safe_csv_cell(sale.customer_id), str(sale.revenue), str(sale.cost)])
    return response
