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
    data_revision = models.PositiveBigIntegerField(default=0)
    visible_kpis = models.JSONField(default=default_kpis)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ColumnMapping(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='column_mappings')
    name = models.CharField(max_length=100)
    columns = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(fields=['organization', 'name'], name='unique_org_mapping_name')]

    def __str__(self):
        return self.name


class SalesImport(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='imports')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    inserted_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)

    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    reversal_reason = models.CharField(max_length=1000, blank=True, default='')

    @property
    def status(self):
        return 'Reversed' if self.reversed_at else 'Active'

    class Meta:
        ordering = ['-created_at']


class Sale(models.Model):
    """One row per completed sale, NOT an invoice line or a monthly summary."""
    is_active = models.BooleanField(default=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='sales')
    import_batch = models.ForeignKey(SalesImport, on_delete=models.PROTECT, related_name='sales')
    transaction_id = models.CharField(max_length=100)
    sale_date = models.DateField()
    customer_id = models.CharField(max_length=100)
    revenue = models.DecimalField(max_digits=14, decimal_places=2)
    cost = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'transaction_id'], condition=Q(is_active=True), name='unique_active_org_transaction'),
            models.CheckConstraint(condition=Q(revenue__gte=0), name='sale_revenue_nonnegative'),
            models.CheckConstraint(condition=Q(cost__gte=0), name='sale_cost_nonnegative'),
        ]
        indexes = [models.Index(fields=['organization', 'sale_date'], name='sales_org_date_idx')]

    def clean(self):
        if self.import_batch_id and self.organization_id and self.import_batch.organization_id != self.organization_id:
            raise ValidationError('Import batch must belong to the same organization.')


class ExpenseColumnMapping(models.Model):
    """Separate namespace keeps expense profiles out of existing sales uploads."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='expense_mappings')
    name = models.CharField(max_length=100)
    columns = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(fields=['organization', 'name'], name='unique_org_expense_mapping')]

    def __str__(self):
        return self.name


class ExpenseImport(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='expense_imports')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    inserted_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)

    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    reversal_reason = models.CharField(max_length=1000, blank=True, default='')

    @property
    def status(self):
        return 'Reversed' if self.reversed_at else 'Active'

    class Meta:
        ordering = ['-created_at', '-pk']


class Expense(models.Model):
    """One operating expense transaction; excludes direct costs already in sales."""
    is_active = models.BooleanField(default=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='expenses')
    import_batch = models.ForeignKey(ExpenseImport, on_delete=models.PROTECT, related_name='expenses')
    expense_id = models.CharField(max_length=100)
    date = models.DateField()
    category = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'expense_id'], condition=Q(is_active=True), name='unique_active_org_expense'),
            models.CheckConstraint(condition=Q(amount__gte=0), name='expense_amount_nonnegative'),
        ]
        indexes = [models.Index(fields=['organization', 'date'], name='expense_org_date_idx')]

    def clean(self):
        if self.import_batch_id and self.organization_id and self.import_batch.organization_id != self.organization_id:
            raise ValidationError('Import batch must belong to the same organization.')


class RecoveryLimit(models.Model):
    """Shared fixed-window counters; keys contain HMACs, never raw email/IP values."""
    key = models.CharField(max_length=160, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class RecoveryRequest(models.Model):
    """Short-lived delivery intent, never a password or reset token."""
    email = models.EmailField(max_length=254)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['pk']
