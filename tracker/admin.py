from django.contrib import admin
from .models import Organization, Sale, SalesImport


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'owner', 'currency')
    search_fields = ('name', 'owner__username')


@admin.register(SalesImport)
class SalesImportAdmin(admin.ModelAdmin):
    list_display = ('id', 'organization', 'filename', 'inserted_count', 'duplicate_count', 'created_at')
    list_filter = ('organization',)


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'organization', 'sale_date', 'revenue', 'cost')
    list_filter = ('organization', 'sale_date')
    search_fields = ('transaction_id', 'customer_id')
