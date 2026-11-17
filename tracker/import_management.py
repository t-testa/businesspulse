"""Read-only impact previews and serialized, auditable batch reversals."""
from django.core import signing
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Expense, ExpenseImport, Organization, Sale, SalesImport
from .services import CSVValidationError, report_for

TOKEN_SALT = 'tracker.import-reversal.v1'
TOKEN_MAX_AGE = 30 * 60
DATASETS = {'sales': (SalesImport, Sale), 'expenses': (ExpenseImport, Expense)}


def _owned_batch(organization, dataset, batch_id):
    if dataset not in DATASETS:
        raise Http404('Unknown dataset')
    batch_model, row_model = DATASETS[dataset]
    batch = get_object_or_404(batch_model.objects.select_related('reversed_by'),
                             organization=organization, pk=batch_id)
    return batch, row_model.objects.filter(organization=organization, import_batch=batch)


def _locked_owner(organization, user):
    return get_object_or_404(Organization.objects.select_for_update(), pk=organization.pk, owner=user)


@transaction.atomic
def preview_import(*, organization, user, dataset, batch_id):
    # Hold the same short-lived lock as writers so before/after and token are coherent.
    # This GET path never writes, and releases the lock before rendering the page.
    organization = _locked_owner(organization, user)
    batch, rows = _owned_batch(organization, dataset, batch_id)
    before = report_for(organization)
    after = report_for(organization, exclude_import=(dataset, batch.pk))
    impact = [{'label': old['label'], 'before': old['value'], 'after': new['value']}
              for old, new in zip(before['financial_totals'], after['financial_totals'])]
    token = signing.dumps({'organization': organization.pk, 'user': user.pk,
                           'dataset': dataset, 'batch': batch.pk, 'revision': organization.data_revision},
                          salt=TOKEN_SALT)
    return {'batch': batch, 'records': list(rows.order_by('pk')), 'impact': impact,
            'token': token, 'active_count': rows.filter(is_active=True).count(),
            'dataset': dataset, 'organization': organization}


@transaction.atomic
def reverse_import(*, organization, user, dataset, batch_id, token, reason):
    organization = _locked_owner(organization, user)
    batch, rows = _owned_batch(organization, dataset, batch_id)
    try:
        snapshot = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature as exc:
        raise CSVValidationError('Confirmation expired or is invalid. Review the preview again.') from exc
    binding = {'organization': organization.pk, 'user': user.pk, 'dataset': dataset, 'batch': batch.pk}
    if not isinstance(snapshot, dict) or any(snapshot.get(key) != value for key, value in binding.items()):
        raise CSVValidationError('Confirmation does not match this import. Review the preview again.')
    # A retry cannot overwrite the first audit record, or touch a corrected reimport.
    if batch.reversed_at is not None:
        return False
    if snapshot.get('revision') != organization.data_revision:
        raise CSVValidationError('Workspace data changed since this preview. Review the updated impact and confirm again.')
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise CSVValidationError('A reversal reason of 1 to 1000 characters is required.')
    rows.filter(is_active=True).update(is_active=False)
    batch.reversed_at = timezone.now()
    batch.reversed_by = user
    batch.reversal_reason = reason
    batch.save(update_fields=['reversed_at', 'reversed_by', 'reversal_reason'])
    Organization.objects.filter(pk=organization.pk).update(data_revision=F('data_revision') + 1)
    return True
