from django.contrib import admin
from .models import Expense, ExpenseImport, Organization, Sale, SalesImport


class ReadOnlyFinancialAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Organization)
class OrganizationAdmin(ReadOnlyFinancialAdmin):
    list_display = ('id', 'name', 'owner', 'currency')
    search_fields = ('name', 'owner__username')


@admin.register(SalesImport)
class SalesImportAdmin(ReadOnlyFinancialAdmin):
    list_display = ('id', 'organization', 'filename', 'inserted_count', 'duplicate_count', 'created_at')
    list_filter = ('organization',)


@admin.register(Sale)
class SaleAdmin(ReadOnlyFinancialAdmin):
    list_display = ('transaction_id', 'organization', 'sale_date', 'revenue', 'cost')
    list_filter = ('organization', 'sale_date')
    search_fields = ('transaction_id', 'customer_id')

admin.site.register(Expense, ReadOnlyFinancialAdmin)
admin.site.register(ExpenseImport, ReadOnlyFinancialAdmin)
