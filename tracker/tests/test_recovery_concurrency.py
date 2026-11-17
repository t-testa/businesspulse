"""Shared PostgreSQL counters, queue claims and concurrent reset redemption."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.db import connection, connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from tracker.models import RecoveryRequest, RecoveryLimit
from tracker.recovery import consume_limits, deliver_one
from .test_imports import TEST_STORAGES
from .test_recovery import OLD_PASSWORD, NEW_PASSWORD


@skipUnless(connection.vendor == 'postgresql', 'Requires shared PostgreSQL row locks')
@override_settings(STORAGES=TEST_STORAGES, ACCOUNT_RECOVERY_ENABLED=True,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   RECOVERY_ORIGIN='https://staging.example.invalid')
class RecoveryConcurrencyTests(TransactionTestCase):
    def run_parallel(self, calls):
        barrier = Barrier(len(calls))
        def worker(call):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                barrier.wait(timeout=10)
                return call()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            return [future.result(timeout=20) for future in futures]

    def test_new_counter_race_allows_exactly_one_across_connections(self):
        results = self.run_parallel([lambda: consume_limits([('parallel', 'all', 3600, 1)]) for _ in range(6)])
        self.assertEqual(results.count(True), 1)
        self.assertEqual(RecoveryLimit.objects.get().count, 1)

    def test_queue_claim_race_sends_only_once(self):
        get_user_model().objects.create_user(username='mail-owner',email='owner@example.invalid',password=OLD_PASSWORD)
        RecoveryRequest.objects.create(email='owner@example.invalid',expires_at=timezone.now()+timedelta(minutes=15))
        self.assertEqual(self.run_parallel([deliver_one, deliver_one]).count(True), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(RecoveryRequest.objects.count(), 0)

    def test_same_token_two_sessions_only_one_password_reset_succeeds(self):
        user = get_user_model().objects.create_user(username='reset-owner',email='owner@example.invalid',password=OLD_PASSWORD)
        link = reverse('password_reset_confirm', kwargs={'uidb64':urlsafe_base64_encode(force_bytes(user.pk)),
                                                       'token':default_token_generator.make_token(user)})
        clients = [Client(),Client()]
        targets = [client.get(link).url for client in clients]
        passwords = [NEW_PASSWORD, 'Another-Synthetic-Password-862!']
        calls = [lambda client=client,target=target,password=password: client.post(target, {
            'new_password1':password,'new_password2':password}).status_code
            for client,target,password in zip(clients,targets,passwords)]
        results = self.run_parallel(calls)
        self.assertEqual(sorted(results), [200,302])
        user.refresh_from_db()
        self.assertTrue(user.check_password(passwords[results.index(302)]))
