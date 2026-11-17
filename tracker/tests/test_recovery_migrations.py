"""Recovery adds tables only, without changing users or financial history."""
from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RecoveryMigrationTests(TransactionTestCase):
    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([('tracker', target)])
        return executor.loader.project_state([('tracker', target)]).apps

    def test_upgrade_preserves_existing_user_hash_workspace_and_audit(self):
        try:
            old = self.migrate('0004_import_management')
            user = old.get_model('auth','User').objects.create(username='legacy-owner',email='owner@example.invalid',password='preserve-existing-hash')
            org = old.get_model('tracker','Organization').objects.create(owner_id=user.pk,name='Preserve workspace',data_revision=7)
            batch = old.get_model('tracker','SalesImport').objects.create(organization_id=org.pk,uploaded_by_id=user.pk,filename='synthetic.csv')
            old.get_model('tracker','Sale').objects.create(organization_id=org.pk,import_batch_id=batch.pk,
                transaction_id='S1',sale_date=date(2026,11,1),customer_id='C',revenue=100,cost=40,is_active=False)
            new = self.migrate('0005_account_recovery')
            self.migrate('0005_account_recovery')
            self.assertEqual(new.get_model('auth','User').objects.get().password,'preserve-existing-hash')
            self.assertEqual(new.get_model('tracker','Organization').objects.get().data_revision,7)
            self.assertFalse(new.get_model('tracker','Sale').objects.get().is_active)
            self.assertEqual(new.get_model('tracker','SalesImport').objects.get().filename,'synthetic.csv')
            self.assertFalse(new.get_model('tracker','RecoveryRequest').objects.exists())
            self.migrate('0004_import_management')
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
