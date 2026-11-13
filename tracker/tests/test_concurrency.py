"""Real PostgreSQL connections: the losing upload must wait, then re-read sales."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from queue import Queue
from time import monotonic, sleep
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from tracker.models import Organization, Sale, SalesImport
from tracker.services import CSVValidationError, SaleRow, save_sales_import


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
class ConcurrentImportTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='owner')
        self.organization = Organization.objects.create(owner=self.user, name='Synthetic workspace')
        self.row = SaleRow('SHARED', date(2026, 11, 2), 'CUSTOMER', Decimal('100'), Decimal('40'))

    def worker(self, organization, user, rows, ready):
        # Django connections are thread-local. Never pass a connection between threads.
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '10s'")
                cursor.execute('SELECT pg_backend_pid()')
                ready.put(cursor.fetchone()[0])
            try:
                batch = save_sales_import(organization=organization, user=user, file_name='parallel.csv', rows=rows)
                return ('ok', batch.inserted_count, batch.duplicate_count)
            except CSVValidationError:
                return ('conflict',)
        finally:
            connections.close_all()

    def overlapping_import(self, rows):
        ready = Queue()
        with ThreadPoolExecutor(max_workers=1) as executor:
            # Hold the first import uncommitted while a second connection tries to import.
            with transaction.atomic():
                save_sales_import(organization=self.organization, user=self.user,
                                  file_name='first.csv', rows=[self.row])
                future = executor.submit(self.worker, self.organization, self.user, rows, ready)
                pid = ready.get(timeout=10)
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [pid])
                        if cursor.fetchone()[0]:
                            break
                    if future.done():
                        self.fail('Second import finished before the first committed: ' + repr(future.result()))
                    sleep(0.01)
                else:
                    self.fail('Second import did not reach a database lock within 8 seconds')
            # Commit first, releasing the row lock. The second import can now re-read.
            return future.result(timeout=12)

    def test_identical_concurrent_upload_is_idempotent(self):
        self.assertEqual(self.overlapping_import([self.row]), ('ok', 0, 1))
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(SalesImport.objects.count(), 2)

    def test_conflicting_concurrent_upload_is_atomic(self):
        new = SaleRow('NEW', self.row.sale_date, 'OTHER', Decimal('50'), Decimal('20'))
        conflicting = SaleRow('SHARED', self.row.sale_date, 'CUSTOMER', Decimal('999'), Decimal('40'))
        self.assertEqual(self.overlapping_import([new, conflicting]), ('conflict',))
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(Sale.objects.get().revenue, Decimal('100'))
        self.assertEqual(SalesImport.objects.count(), 1)

    def test_other_workspace_does_not_wait_for_locked_workspace(self):
        user = get_user_model().objects.create_user(username='other')
        organization = Organization.objects.create(owner=user, name='Other synthetic workspace')
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                save_sales_import(organization=self.organization, user=self.user,
                                  file_name='first.csv', rows=[self.row])
                future = executor.submit(self.worker, organization, user, [self.row], Queue())
                self.assertEqual(future.result(timeout=8), ('ok', 1, 0))
        self.assertEqual(Sale.objects.filter(transaction_id='SHARED').count(), 2)
