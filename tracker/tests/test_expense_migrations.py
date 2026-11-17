"""Exercise upgrade/repeat/rollback on Django's disposable test database only."""
from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ExpenseMigrationTests(TransactionTestCase):
    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([('tracker', target)])
        return executor.loader.project_state([('tracker', target)]).apps

    def test_additive_upgrade_preserves_sales_and_profiles_and_is_repeatable(self):
        try:
            old = self.migrate('0002_column_mapping')
            user = old.get_model('auth', 'User').objects.create(username='migration-owner')
            org = old.get_model('tracker', 'Organization').objects.create(owner_id=user.pk, name='Synthetic migration')
            batch = old.get_model('tracker', 'SalesImport').objects.create(organization_id=org.pk, uploaded_by_id=user.pk, filename='synthetic.csv')
            old.get_model('tracker', 'Sale').objects.create(organization_id=org.pk, import_batch_id=batch.pk,
                transaction_id='PRESERVE', sale_date=date(2026, 11, 1), customer_id='C', revenue=3700, cost=2200)
            old.get_model('tracker', 'ColumnMapping').objects.create(organization_id=org.pk, name='Preserve', columns={'date':'When'})
            new = self.migrate('0003_expense_intelligence')
            self.assertEqual(new.get_model('tracker', 'Sale').objects.get().transaction_id, 'PRESERVE')
            self.assertEqual(new.get_model('tracker', 'ColumnMapping').objects.get().columns, {'date':'When'})
            self.assertFalse(new.get_model('tracker', 'ExpenseImport').objects.exists())
            eb = new.get_model('tracker', 'ExpenseImport').objects.create(organization_id=org.pk, uploaded_by_id=user.pk, filename='expense.csv')
            new.get_model('tracker', 'Expense').objects.create(organization_id=org.pk, import_batch_id=eb.pk,
                expense_id='E1', date=date(2026, 11, 1), category='Rent', amount=600)
            repeated = self.migrate('0003_expense_intelligence')
            self.assertEqual(repeated.get_model('tracker', 'Expense').objects.count(), 1)
            rollback = self.migrate('0002_column_mapping')
            self.assertEqual(rollback.get_model('tracker', 'Sale').objects.count(), 1)
            self.assertEqual(rollback.get_model('tracker', 'ColumnMapping').objects.count(), 1)
        finally:
            self.migrate('0003_expense_intelligence')
