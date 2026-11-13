from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .kpis import default_kpis


class Organization(models.Model):
    CURRENCIES = [('CAD', 'Canadian dollar'), ('USD', 'US dollar')]
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='organization')
    name = models.CharField(max_length=140)
    currency = models.CharField(max_length=3, choices=CURRENCIES, default='CAD')
    visible_kpis = models.JSONField(default=default_kpis)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class SalesImport(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='imports')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    inserted_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']


class Sale(models.Model):
    """One row per completed sale, NOT an invoice line or a monthly summary."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='sales')
    import_batch = models.ForeignKey(SalesImport, on_delete=models.PROTECT, related_name='sales')
    transaction_id = models.CharField(max_length=100)
    sale_date = models.DateField()
    customer_id = models.CharField(max_length=100)
    revenue = models.DecimalField(max_digits=14, decimal_places=2)
    cost = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'transaction_id'], name='unique_org_transaction'),
            models.CheckConstraint(condition=Q(revenue__gte=0), name='sale_revenue_nonnegative'),
            models.CheckConstraint(condition=Q(cost__gte=0), name='sale_cost_nonnegative'),
        ]
        indexes = [models.Index(fields=['organization', 'sale_date'], name='sales_org_date_idx')]

    def clean(self):
        if self.import_batch_id and self.organization_id and self.import_batch.organization_id != self.organization_id:
            raise ValidationError('Import batch must belong to the same organization.')
