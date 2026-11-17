"""Synthetic recovery acceptance and abuse/security regressions."""
from datetime import datetime, timedelta, date
from decimal import Decimal
import logging
import re
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.db import connection
from django.test import Client, TestCase, SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from tracker.email_backend import DeliveryError, ResendBackend
from tracker.models import Organization, RecoveryLimit, RecoveryRequest, Sale, Expense, SalesImport, ExpenseImport
from tracker.recovery import consume_limits, deliver_one, queue_reset, cleanup_recovery
from tracker.recovery_logging import RedactRecovery
from tracker.services import SaleRow, ExpenseRow, save_sales_import, save_expense_import, report_for
from .test_imports import TEST_STORAGES

OLD_PASSWORD = 'Old-Synthetic-Password-194!'
NEW_PASSWORD = 'New-Synthetic-Password-275!'


@override_settings(STORAGES=TEST_STORAGES, ACCOUNT_RECOVERY_ENABLED=True,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   RECOVERY_ORIGIN='https://staging.example.invalid')
class RecoveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='recovery-owner', email='owner@example.invalid', password=OLD_PASSWORD)
        cls.other = get_user_model().objects.create_user(username='other-owner', email='other@example.invalid', password=OLD_PASSWORD)
        cls.org = Organization.objects.create(owner=cls.user, name='Synthetic recovery company')
        cls.other_org = Organization.objects.create(owner=cls.other, name='Other synthetic company')

    def link(self, user=None, token=None):
        user = user or self.user
        return reverse('password_reset_confirm', kwargs={'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
            'token': token or default_token_generator.make_token(user)})

    def request_reset(self, email='owner@example.invalid', **extra):
        return self.client.post(reverse('password_reset'), {'email': email}, **extra)

    def complete(self, link=None, client=None, password=NEW_PASSWORD):
        client = client or self.client
        response = client.get(link or self.link())
        return client.post(response.url, {'new_password1':password, 'new_password2':password})

    def test_reset_existing_account_preserves_workspace_financials_and_history(self):
        save_sales_import(organization=self.org, user=self.user, file_name='synthetic.csv',
            rows=[SaleRow('S1',date(2026,11,1),'C',Decimal('3700'),Decimal('2200'))])
        save_expense_import(organization=self.org,user=self.user,file_name='expense.csv',
            rows=[ExpenseRow('E1',date(2026,11,1),'Rent',Decimal('750'))])
        before = report_for(self.org)
        original = (self.user.pk, self.org.pk, self.org.owner_id, list(Sale.objects.values()),
                    list(Expense.objects.values()), list(SalesImport.objects.values()), list(ExpenseImport.objects.values()))
        response = self.request_reset()
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(deliver_one())
        self.assertEqual(len(mail.outbox), 1)
        url = re.search(r'https://\S+',mail.outbox[0].body).group()
        self.assertTrue(url.startswith('https://staging.example.invalid/accounts/reset/'))
        self.assertRedirects(self.complete(url), reverse('password_reset_complete'))
        self.user.refresh_from_db(); self.org.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(self.user.check_password(OLD_PASSWORD))
        self.assertEqual(original,(self.user.pk,self.org.pk,self.org.owner_id,list(Sale.objects.values()),
            list(Expense.objects.values()),list(SalesImport.objects.values()),list(ExpenseImport.objects.values())))
        self.assertEqual(before, report_for(self.org))
        self.assertTrue(self.client.login(username=self.user.username,password=NEW_PASSWORD))
        self.assertEqual(self.client.get(reverse('dashboard')).context['organization'],self.org)
        self.assertEqual(report_for(self.other_org)['transaction_count'],0)

    def test_known_unknown_inactive_unusable_ambiguous_have_same_http_response(self):
        inactive=get_user_model().objects.create_user(username='inactive',email='inactive@example.invalid',password=OLD_PASSWORD,is_active=False)
        unusable=get_user_model().objects.create_user(username='unusable',email='unusable@example.invalid')
        for name in ['duplicate1','duplicate2']:
            get_user_model().objects.create_user(username=name,email='duplicate@example.invalid',password=OLD_PASSWORD)
        for email in [self.user.email,'unknown@example.invalid',inactive.email,unusable.email,'duplicate@example.invalid']:
            with self.subTest(email=email), CaptureQueriesContext(connection) as queries:
                response=self.request_reset(email)
                self.assertEqual((response.status_code,response.url),(302,reverse('password_reset_done')))
            self.assertFalse(any('auth_user' in query['sql'] for query in queries))
        self.assertEqual(RecoveryRequest.objects.count(),5)
        while deliver_one():pass
        self.assertEqual([message.to for message in mail.outbox],[[self.user.email]])

    def test_expired_invalid_unknown_user_and_reused_tokens(self):
        with patch.object(default_token_generator,'_now',return_value=datetime(2026,1,1)):
            expired=self.link()
        for link in [expired,self.link(token='invalid'),'/accounts/reset/invalid/invalid/']:
            self.assertFalse(self.client.get(link).context['validlink'])
        first=self.link(); second=self.link()
        self.assertRedirects(self.complete(first),reverse('password_reset_complete'))
        for link in [first,second]:
            self.assertFalse(self.client.get(link).context['validlink'])

    def test_inactive_and_unusable_users_cannot_redeem_forged_valid_tokens(self):
        self.user.is_active=False;self.user.save(update_fields=['is_active'])
        self.assertFalse(self.client.get(self.link()).context['validlink'])
        self.user.is_active=True;self.user.set_unusable_password();self.user.save()
        self.assertFalse(self.client.get(self.link()).context['validlink'])

    def test_validation_csrf_and_no_automatic_login(self):
        link=self.link(); response=self.client.get(link)
        invalid=self.client.post(response.url,{'new_password1':'123','new_password2':'123'})
        self.assertTrue(invalid.context['form'].errors)
        self.user.refresh_from_db();self.assertTrue(self.user.check_password(OLD_PASSWORD))
        csrf=Client(enforce_csrf_checks=True)
        self.assertEqual(csrf.post(reverse('password_reset'),{'email':self.user.email}).status_code,403)
        target=csrf.get(link).url
        self.assertEqual(csrf.post(target,{'new_password1':NEW_PASSWORD,'new_password2':NEW_PASSWORD}).status_code,403)
        self.assertRedirects(self.complete(link),reverse('password_reset_complete'))
        self.assertNotIn('_auth_user_id',self.client.session)

    def test_reset_invalidates_logged_in_sessions(self):
        session=Client();session.force_login(self.user)
        self.complete()
        self.assertEqual(session.get(reverse('dashboard')).status_code,302)

    def test_change_requires_current_password_preserves_current_session_invalidates_others_and_links(self):
        other_session=Client();other_session.force_login(self.user)
        self.client.force_login(self.user);old_link=self.link()
        data={'old_password':'wrong','new_password1':NEW_PASSWORD,'new_password2':NEW_PASSWORD,'user':self.other.pk}
        self.assertTrue(self.client.post(reverse('password_change'),data).context['form'].errors)
        data['old_password']=OLD_PASSWORD
        self.assertRedirects(self.client.post(reverse('password_change'),data),reverse('password_change_done'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code,200)
        self.assertEqual(other_session.get(reverse('dashboard')).status_code,302)
        self.assertFalse(self.client.get(old_link).context['validlink'])
        self.other.refresh_from_db();self.assertTrue(self.other.check_password(OLD_PASSWORD))
        self.client.logout();self.assertEqual(self.client.get(reverse('password_change')).status_code,302)

    def test_reset_changes_only_link_owner_even_when_other_user_logged_in(self):
        self.client.force_login(self.other)
        self.complete(self.link())
        self.other.refresh_from_db();self.assertTrue(self.other.check_password(OLD_PASSWORD))
        self.assertEqual(self.client.get(reverse('dashboard')).context['organization'],self.other_org)

    def test_address_rate_limit_case_variants_and_no_header_bypass(self):
        for n in range(5):
            response=self.request_reset(' OWNER@EXAMPLE.INVALID ',HTTP_X_FORWARDED_FOR=f'192.0.2.{n}')
            self.assertEqual(response.status_code,302)
        self.assertEqual(RecoveryRequest.objects.count(),3)
        self.assertFalse(any('owner@' in key for key in RecoveryLimit.objects.values_list('key',flat=True)))
        self.assertFalse(any('127.0.0.1' in key for key in RecoveryLimit.objects.values_list('key',flat=True)))

    def test_ip_global_and_window_expiration_limits(self):
        for n in range(11):self.request_reset(f'unknown{n}@example.invalid')
        self.assertEqual(RecoveryRequest.objects.count(),10)
        now=timezone.now()
        with patch('tracker.recovery.timezone.now',return_value=now+timedelta(hours=2)):
            self.request_reset('unknown-new@example.invalid')
        self.assertEqual(RecoveryRequest.objects.count(),11)
        self.assertTrue(consume_limits([('test-global','all',3600,1)]))
        self.assertFalse(consume_limits([('test-global','all',3600,1)]))

    def test_login_change_and_confirmation_attempt_limits(self):
        with patch('tracker.recovery_views.allow_password_attempt',return_value=False):
            self.assertEqual(self.client.post(reverse('login'),{}).status_code,429)
            self.assertEqual(self.client.post(self.link(),{}).status_code,429)
            self.client.force_login(self.user)
            self.assertEqual(self.client.post(reverse('password_change'),{}).status_code,429)
        self.user.refresh_from_db();self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_host_header_never_controls_link_and_security_headers(self):
        with override_settings(ALLOWED_HOSTS=['testserver','attacker.invalid']):
            self.request_reset(HTTP_HOST='attacker.invalid')
        deliver_one()
        self.assertNotIn('attacker.invalid',mail.outbox[0].body)
        response=self.client.get(self.link())
        self.assertEqual(response['Referrer-Policy'],'no-referrer')
        self.assertIn('no-store',response['Cache-Control'])
        self.assertNotIn(default_token_generator.make_token(self.user),response.url)

    def test_public_signup_can_be_closed_without_affecting_existing_accounts(self):
        with override_settings(PUBLIC_SIGNUP_ENABLED=False):
            self.assertEqual(self.client.get(reverse('signup')).status_code,404)
            self.assertEqual(self.client.post(reverse('signup'),{'email': self.user.email}).status_code,404)
            self.assertEqual(self.request_reset().status_code,302)
        self.assertEqual(get_user_model().objects.count(),2)

    def test_referrer_protection_includes_invalid_and_csrf_rejected_tokens(self):
        client=Client(enforce_csrf_checks=True)
        for url in (self.link(), '/accounts/reset/invalid/secret-token/'):
            for response in (client.get(url), client.post(url,{})):
                self.assertEqual(response['Referrer-Policy'],'no-referrer')
        with override_settings(ACCOUNT_RECOVERY_ENABLED=False):
            self.assertContains(self.client.get(self.link()),'invalid')

    def test_queue_expiry_disabled_recovery_and_provider_failure_are_generic(self):
        with override_settings(ACCOUNT_RECOVERY_ENABLED=False):
            self.assertEqual(self.request_reset().status_code,302)
        self.assertEqual(RecoveryRequest.objects.count(),0)
        RecoveryRequest.objects.create(email=self.user.email,expires_at=timezone.now()-timedelta(seconds=1))
        self.assertFalse(deliver_one());cleanup_recovery();self.assertEqual(RecoveryRequest.objects.count(),0)
        self.request_reset()
        with patch('tracker.recovery.EmailMultiAlternatives.send',side_effect=RuntimeError('SECRET')):
            with self.assertLogs('tracker',level='WARNING') as logs:self.assertTrue(deliver_one())
        self.assertNotIn('SECRET',str(logs.output));self.assertNotIn(self.user.email,str(logs.output))
        self.assertEqual(RecoveryRequest.objects.count(),0)


class RecoveryBackendTests(SimpleTestCase):
    @override_settings(RESEND_API_KEY='test-only-key',EMAIL_TIMEOUT=10)
    def test_https_provider_payload_timeout_and_errors(self):
        from django.core.mail import EmailMessage
        message=EmailMessage('Reset','synthetic body','sender@example.invalid',['owner@example.invalid'])
        opener=MagicMock();opener.open.return_value.__enter__.return_value.status=200
        with patch('tracker.email_backend.urllib.request.build_opener',return_value=opener):
            self.assertEqual(ResendBackend().send_messages([message]),1)
            request=opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.resend.com/emails')
            self.assertEqual(opener.open.call_args.kwargs['timeout'],10)
            opener.open.side_effect=OSError('secret provider detail')
            with self.assertRaisesMessage(DeliveryError,'Email delivery failed'):
                ResendBackend().send_messages([message])

    def test_recovery_urls_redacted_in_logs(self):
        record=logging.LogRecord('django.request',logging.WARNING,'',1,'Forbidden: %s',('/accounts/reset/abc/SECRET/',),None)
        RedactRecovery().filter(record)
        self.assertNotIn('SECRET',record.getMessage())
