import os
import re
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core import mail
from django.test import override_settings
from tracker.models import Organization
from tracker.recovery import deliver_one
from .test_imports import TEST_STORAGES
from .test_recovery import OLD_PASSWORD, NEW_PASSWORD


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1','Enable Playwright Chromium tests')
@override_settings(STORAGES=TEST_STORAGES, ACCOUNT_RECOVERY_ENABLED=True,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class RecoveryBrowserTests(StaticLiveServerTestCase):
    def test_request_email_reset_login_and_password_change(self):
        from playwright.sync_api import expect, sync_playwright
        user=get_user_model().objects.create_user(username='browser-recovery',email='browser@example.invalid',password=OLD_PASSWORD)
        org=Organization.objects.create(owner=user,name='Preserved recovery workspace')
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            page=browser.new_page()
            page.goto(self.live_server_url+'/accounts/login/')
            page.get_by_role('link',name='Forgot password?').click()
            page.locator('#id_email').fill(user.email)
            page.get_by_role('button',name='Request reset link').click()
            expect(page.get_by_role('heading',name='Check your email')).to_be_visible()
            browser.close()
        # ORM work outside Playwright's event loop; no actual email leaves the test.
        with override_settings(RECOVERY_ORIGIN=self.live_server_url):
            self.assertTrue(deliver_one())
        link=re.search(r'http://\S+',mail.outbox[0].body).group()
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            page=browser.new_page()
            page.goto(link)
            page.locator('#id_new_password1').fill(NEW_PASSWORD)
            page.locator('#id_new_password2').fill(NEW_PASSWORD)
            page.get_by_role('button',name='Reset password',exact=True).click()
            expect(page.get_by_role('heading',name='Password reset complete')).to_be_visible()
            page.goto(self.live_server_url+'/accounts/login/')
            page.locator('#id_username').fill(user.username)
            page.locator('#id_password').fill(NEW_PASSWORD)
            page.get_by_role('button',name='Sign in',exact=True).click()
            expect(page.get_by_role('heading',name=org.name)).to_be_visible()
            page.get_by_role('link',name='Password',exact=True).click()
            page.locator('#id_old_password').fill(NEW_PASSWORD)
            page.locator('#id_new_password1').fill('Changed-Synthetic-Password-940!')
            page.locator('#id_new_password2').fill('Changed-Synthetic-Password-940!')
            page.get_by_role('button',name='Change password',exact=True).click()
            expect(page.get_by_role('heading',name='Password changed')).to_be_visible()
            browser.close()
        user.refresh_from_db()
        self.assertTrue(user.check_password('Changed-Synthetic-Password-940!'))
        self.assertEqual(Organization.objects.get(owner=user).pk,org.pk)
