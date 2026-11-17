"""Opt-in real-browser regression tests; CI runs these on both database backends."""
import os
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from tracker.models import ColumnMapping, Organization, Sale
from .test_imports import TEST_STORAGES


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1', 'Set RUN_BROWSER_TESTS=1 and install Playwright Chromium')
@override_settings(STORAGES=TEST_STORAGES)
class ColumnMappingBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='browser-owner')
        self.organization = Organization.objects.create(owner=self.user, name='Browser synthetic workspace')
        self.client.force_login(self.user)

    def open_page(self, browser):
        context = browser.new_context()
        context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value,
                             'url': self.live_server_url}])
        page = context.new_page()
        page.goto(self.live_server_url + '/upload/')
        return context, page

    def test_preview_correct_save_and_reuse_without_preview_upload(self):
        from playwright.sync_api import expect, sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context, page = self.open_page(browser)
            posts = []
            page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
            source = 'Receipt,When,Buyer,Net,Expense,Note\n' + ''.join(
                f'X-{n},2026-11-01,C,100,40,<img src=x onerror=alert(1)>\n' for n in range(12))
            page.locator('#id_file').set_input_files({'name': 'synthetic.csv', 'mimeType': 'text/csv', 'buffer': source.encode()})
            expect(page.locator('#csv-preview')).to_be_visible()
            expect(page.locator('#preview-table tbody tr')).to_have_count(10)
            expect(page.locator('#preview-table th')).to_have_count(6)
            expect(page.locator('#preview-table img')).to_have_count(0)
            expect(page.locator('#import-button')).to_be_disabled()
            self.assertEqual(posts, [])
            for field, column in zip(['transaction_id', 'date', 'customer_id', 'revenue', 'cost'],
                                     ['Receipt', 'When', 'Buyer', 'Net', 'Expense']):
                page.locator('#map-' + field).select_option(column)
            page.locator('#map-cost').select_option('Net')
            expect(page.locator('#import-button')).to_be_disabled()
            page.locator('#map-cost').select_option('Expense')
            page.locator('#id_mapping_name').fill('Reusable export')
            expect(page.locator('#import-button')).to_be_enabled()
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            page.locator('#import-button').click()
            expect(page).to_have_url(self.live_server_url + '/')
            expect(page.get_by_text('CAD 1,200.00', exact=True).first).to_be_visible()
            self.assertEqual(len(posts), 1)
            page.goto(self.live_server_url + '/upload/')
            page.locator('#id_saved_mapping').select_option(label='Reusable export')
            page.locator('#id_file').set_input_files({'name': 'synthetic.csv', 'mimeType': 'text/csv', 'buffer': source.encode()})
            expect(page.locator('#map-revenue')).to_have_value('Net')
            expect(page.locator('#import-button')).to_be_enabled()
            page.locator('#import-button').click()
            expect(page).to_have_url(self.live_server_url + '/')
            expect(page.get_by_text('Import complete: 0 new sales, 12 identical duplicates skipped.')).to_be_visible()
            context.close()
            browser.close()
        self.assertEqual(Sale.objects.count(), 12)
        self.assertEqual(ColumnMapping.objects.count(), 1)

    def test_suggestions_stale_profile_and_invalid_later_row(self):
        from playwright.sync_api import expect, sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context, page = self.open_page(browser)
            header = 'Order ID,Sale Date,Client ID,Net Sales,COGS\n'
            source = header + ''.join(f'X-{n},2026-11-01,C,100,40\n' for n in range(10)) + 'BAD,2026-02-30,C,100,40\n'
            page.locator('#id_file').set_input_files({'name': 'late-invalid.csv', 'mimeType': 'text/csv', 'buffer': source.encode()})
            expect(page.locator('#map-revenue')).to_have_value('Net Sales')
            expect(page.locator('#import-button')).to_be_enabled()
            page.locator('#id_mapping_name').fill('Must not save')
            page.locator('#import-button').click()
            expect(page.get_by_text("Row 12: invalid calendar date '2026-02-30'.", exact=True)).to_be_visible()
            expect(page.locator('#import-button')).to_be_disabled()
            # A new file cannot retain a mapping to a column that no longer exists.
            source = (header + 'X,2026-11-01,C,100,40\n').replace('COGS', 'New cost')
            page.locator('#id_file').set_input_files({'name': 'changed.csv', 'mimeType': 'text/csv', 'buffer': source.encode()})
            expect(page.locator('#map-cost')).to_have_value('')
            expect(page.locator('#import-button')).to_be_disabled()
            page.locator('#map-cost').select_option('New cost')
            expect(page.locator('#import-button')).to_be_enabled()
            context.close()
            browser.close()
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(ColumnMapping.objects.count(), 0)

    def test_expense_preview_four_fields_save_reuse_and_report(self):
        from playwright.sync_api import expect, sync_playwright
        from tracker.models import Expense, ExpenseColumnMapping
        from tracker.services import parse_sales_csv, save_sales_import
        from .test_imports import HEADER, csv_upload
        save_sales_import(organization=self.organization, user=self.user, file_name='synthetic.csv',
                          rows=parse_sales_csv(csv_upload(HEADER+'S1,2026-11-01,C,3700,2200\n')))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context, page = self.open_page(browser)
            page.goto(self.live_server_url + '/expenses/upload/')
            posts = []
            page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
            source = b'Reference,When,Group,Value\nE1,2026-11-01,Rent,600\nE2,2026-11-02,Software,100\nE3,2026-11-03,Utilities,50\n'
            page.locator('#id_file').set_input_files({'name':'synthetic-expense.csv', 'mimeType':'text/csv', 'buffer':source})
            expect(page.locator('#preview-table tbody tr')).to_have_count(3)
            expect(page.locator('[data-mapping-field]')).to_have_count(4)
            self.assertEqual(posts, [])
            for field, column in [('expense_id','Reference'), ('date','When'), ('category','Group'), ('amount','Value')]:
                page.locator('#map-'+field).select_option(column)
            page.locator('#map-amount').select_option('Group')
            expect(page.locator('#import-button')).to_be_disabled()
            page.locator('#map-amount').select_option('Value')
            page.locator('#id_mapping_name').fill('Expense export')
            page.set_viewport_size({'width':390, 'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            page.locator('#import-button').click()
            expect(page).to_have_url(self.live_server_url+'/')
            expect(page.get_by_text('20.3%', exact=True)).to_be_visible()
            expect(page.get_by_text('CAD 750.00', exact=True).first).to_be_visible()
            self.assertEqual(len(posts), 1)
            page.goto(self.live_server_url+'/expenses/upload/')
            page.locator('#id_saved_mapping').select_option(label='Expense export')
            page.locator('#id_file').set_input_files({'name':'synthetic-expense.csv', 'mimeType':'text/csv', 'buffer':source})
            expect(page.locator('#map-amount')).to_have_value('Value')
            page.locator('#import-button').click()
            expect(page.get_by_text('Import complete: 0 new expenses, 3 identical duplicates skipped.')).to_be_visible()
            context.close()
            browser.close()
        self.assertEqual(Expense.objects.count(), 3)
        self.assertEqual(ExpenseColumnMapping.objects.count(), 1)
