"""v0.3 upgrades retain data; used audit schema cannot be silently rolled back."""
from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import IrreversibleError
from django.test import TransactionTestCase
from django.utils import timezone


class ImportManagementMigrationTests(TransactionTestCase):
    def migrate(self,target):
        executor=MigrationExecutor(connection)
        executor.migrate([('tracker',target)])
        return executor.loader.project_state([('tracker',target)]).apps

    def test_upgrade_preserves_both_datasets_history_profiles_and_defaults(self):
        try:
            old=self.migrate('0003_expense_intelligence')
            user=old.get_model('auth','User').objects.create(username='migration-synthetic')
            org=old.get_model('tracker','Organization').objects.create(owner_id=user.pk,name='Synthetic migration')
            for batch_name,row_name,fields in [
                ('SalesImport','Sale',{'transaction_id':'S1','sale_date':date(2026,11,1),'customer_id':'C','revenue':100,'cost':40}),
                ('ExpenseImport','Expense',{'expense_id':'E1','date':date(2026,11,1),'category':'Rent','amount':10})]:
                batch=old.get_model('tracker',batch_name).objects.create(organization_id=org.pk,uploaded_by_id=user.pk,filename='keep.csv',inserted_count=1)
                old.get_model('tracker',row_name).objects.create(organization_id=org.pk,import_batch_id=batch.pk,**fields)
            for name in ['ColumnMapping','ExpenseColumnMapping']:
                old.get_model('tracker',name).objects.create(organization_id=org.pk,name='Keep',columns={'date':'Date'})
            new=self.migrate('0004_import_management');self.migrate('0004_import_management')
            for name in ['Sale','Expense']:
                self.assertTrue(new.get_model('tracker',name).objects.get().is_active)
            for name in ['SalesImport','ExpenseImport']:
                batch=new.get_model('tracker',name).objects.get()
                self.assertIsNone(batch.reversed_at);self.assertEqual(batch.inserted_count,1)
            for name in ['ColumnMapping','ExpenseColumnMapping']:
                self.assertEqual(new.get_model('tracker',name).objects.get().columns,{'date':'Date'})
            self.assertEqual(new.get_model('tracker','Organization').objects.get().data_revision,0)
            self.migrate('0003_expense_intelligence')  # Unused feature can still be reversed.
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())

    def test_rollback_guard_preserves_reversal_audit_and_historical_versions(self):
        from tracker.models import Organization,ExpenseImport,Expense
        from django.contrib.auth import get_user_model
        user=get_user_model().objects.create_user(username='audit-owner')
        org=Organization.objects.create(owner=user,name='Audit fixture')
        batch=ExpenseImport.objects.create(organization=org,uploaded_by=user,filename='old.csv',
            reversed_at=timezone.now(),reversed_by=user,reversal_reason='Correct amount')
        Expense.objects.create(organization=org,import_batch=batch,expense_id='E1',date=date(2026,11,1),category='Rent',amount=10,is_active=False)
        try:
            with self.assertRaises(IrreversibleError):self.migrate('0003_expense_intelligence')
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
        self.assertEqual(ExpenseImport.objects.get().reversal_reason,'Correct amount')
        self.assertFalse(Expense.objects.get().is_active)
