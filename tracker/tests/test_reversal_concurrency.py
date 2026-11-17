"""Real PostgreSQL races in both lock orders, for both datasets."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from queue import Queue
from time import monotonic, sleep
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from tracker.import_management import preview_import, reverse_import
from tracker.models import Organization, Sale, Expense
from tracker.services import CSVValidationError, SaleRow, ExpenseRow, save_sales_import, save_expense_import


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
class ReversalConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='concurrent-owner')
        self.org=Organization.objects.create(owner=self.user,name='Synthetic concurrency')

    def importer(self,dataset,amount='100'):
        row = (SaleRow('SHARED',date(2026,11,1),'C',Decimal(amount),Decimal('40')) if dataset=='sales'
               else ExpenseRow('SHARED',date(2026,11,1),'Rent',Decimal(amount)))
        service=save_sales_import if dataset=='sales' else save_expense_import
        return service(organization=self.org,user=self.user,file_name='synthetic.csv',rows=[row])

    def reversal(self,dataset,batch,token):
        return reverse_import(organization=self.org,user=self.user,dataset=dataset,batch_id=batch.pk,token=token,reason='Concurrent correction')

    def token(self,dataset,batch):
        return preview_import(organization=self.org,user=self.user,dataset=dataset,batch_id=batch.pk)['token']

    def worker(self,call,ready):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '10s'")
                cursor.execute('SELECT pg_backend_pid()');ready.put(cursor.fetchone()[0])
            try:
                return call()
            except CSVValidationError:
                return 'stale'
        finally:
            connections.close_all()

    def overlap(self,first,second,rollback=False):
        ready=Queue()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                first()
                future=executor.submit(self.worker,second,ready)
                pid=ready.get(timeout=10); deadline=monotonic()+8
                while monotonic()<deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT cardinality(pg_blocking_pids(%s))',[pid])
                        if cursor.fetchone()[0]:break
                    if future.done():self.fail('Second operation did not wait: '+repr(future.result()))
                    sleep(.01)
                else:self.fail('Expected organization lock was not reached')
                if rollback:transaction.set_rollback(True)
            return future.result(timeout=12)

    def test_reversal_first_allows_waiting_corrected_import(self):
        for dataset,model in [('sales',Sale),('expenses',Expense)]:
            with self.subTest(dataset=dataset):
                batch=self.importer(dataset);token=self.token(dataset,batch)
                result=self.overlap(lambda:self.reversal(dataset,batch,token),lambda:self.importer(dataset,'200'))
                self.assertEqual((result.inserted_count,result.duplicate_count),(1,0))
                self.assertEqual(model.objects.count(),2);self.assertEqual(model.objects.filter(is_active=True).count(),1)
                batch.refresh_from_db();self.assertIsNotNone(batch.reversed_at)

    def test_import_first_rejects_stale_reversal_without_deactivation(self):
        for dataset,model in [('sales',Sale),('expenses',Expense)]:
            with self.subTest(dataset=dataset):
                batch=self.importer(dataset);token=self.token(dataset,batch)
                result=self.overlap(lambda:self.importer(dataset),lambda:self.reversal(dataset,batch,token))
                self.assertEqual(result,'stale');self.assertTrue(model.objects.get().is_active)
                batch.refresh_from_db();self.assertIsNone(batch.reversed_at)

    def test_concurrent_repeated_reversal_is_noop(self):
        for dataset,model in [('sales',Sale),('expenses',Expense)]:
            with self.subTest(dataset=dataset):
                batch=self.importer(dataset);token=self.token(dataset,batch)
                result=self.overlap(lambda:self.reversal(dataset,batch,token),lambda:self.reversal(dataset,batch,token))
                self.assertIs(result,False);self.assertFalse(model.objects.get().is_active)
                batch.refresh_from_db();self.assertEqual(batch.reversal_reason,'Concurrent correction')

    def test_rolled_back_reversal_leaves_waiting_upload_a_duplicate(self):
        for dataset,model in [('sales',Sale),('expenses',Expense)]:
            with self.subTest(dataset=dataset):
                batch=self.importer(dataset);token=self.token(dataset,batch)
                result=self.overlap(lambda:self.reversal(dataset,batch,token),lambda:self.importer(dataset),rollback=True)
                self.assertEqual((result.inserted_count,result.duplicate_count),(0,1))
                self.assertTrue(model.objects.get().is_active)
                batch.refresh_from_db();self.assertIsNone(batch.reversed_at)
