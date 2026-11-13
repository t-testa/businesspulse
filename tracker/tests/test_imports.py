from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from tracker.models import Organization, Sale, SalesImport
from tracker.services import parse_sales_csv, report_for, safe_csv_cell, CSVValidationError


HEADER = 'transaction_id,date,customer_id,revenue,cost\n'


def csv_upload(text, name='sales.csv'):
    return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')


class SalesImportTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='strong-pass-983')
        self.bob = User.objects.create_user(username='bob', password='strong-pass-983')
        self.org_a = Organization.objects.create(owner=self.alice, name='Alice Corp', currency='CAD')
        self.org_b = Organization.objects.create(owner=self.bob, name='Bob Corp', currency='USD')
        self.client.force_login(self.alice)

    def upload(self, text, filename='sales.csv'):
        return self.client.post(reverse('upload_sales'), {'file': csv_upload(text, filename)})

    def test_import_and_exact_duplicate_are_idempotent(self):
        data = HEADER + 'TX-1,2026-11-02,C-1,100.00,40.00\nTX-2,2026-11-03,C-2,200.00,90.00\n'
        response = self.upload(data)
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(Sale.objects.filter(organization=self.org_a).count(), 2)
        self.assertEqual(self.upload(data).status_code, 302)
        self.assertEqual(Sale.objects.filter(organization=self.org_a).count(), 2)
        latest = SalesImport.objects.filter(organization=self.org_a).first()
        self.assertEqual(latest.duplicate_count, 2)
        self.assertEqual(latest.inserted_count, 0)

    def test_conflicting_duplicate_rejected_without_partial_updates(self):
        self.upload(HEADER + 'TX-1,2026-11-02,C-1,100.00,40.00\n')
        response = self.upload(HEADER + 'TX-NEW,2026-11-05,C-2,50.00,25.00\nTX-1,2026-11-02,C-1,999.00,40.00\n')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists with different data')
        self.assertEqual(Sale.objects.filter(organization=self.org_a).count(), 1)
        self.assertEqual(SalesImport.objects.filter(organization=self.org_a).count(), 1)

    def test_invalid_rows_reject_entire_file(self):
        response = self.upload(HEADER + 'TX-1,2026-11-02,C-1,100.00,40.00\nTX-2,11/03/2026,C-2,200.00,90.00\n')
        self.assertContains(response, 'date must use YYYY-MM-DD')
        self.assertEqual(Sale.objects.count(), 0)

    def test_duplicate_ids_within_one_file_are_rejected(self):
        response = self.upload(HEADER + 'TX-1,2026-11-02,C-1,100.00,40.00\nTX-1,2026-11-03,C-2,200.00,90.00\n')
        self.assertContains(response, 'occurs twice')
        self.assertEqual(Sale.objects.count(), 0)

    def test_alias_headers_work(self):
        alias_data = 'Order ID,Sale Date,Client ID,Net Sales,COGS\nTX-88,2026-11-01,C-9,120.00,80.00\n'
        self.assertRedirects(self.upload(alias_data), reverse('dashboard'))
        self.assertEqual(Sale.objects.get().revenue, Decimal('120.00'))

    def test_html_and_csv_views_only_show_own_organization(self):
        self.upload(HEADER + 'PRIVATE-A,2026-11-02,C-1,100.00,40.00\n')
        self.client.force_login(self.bob)
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, 'Bob Corp')
        self.assertNotContains(dashboard, 'PRIVATE-A')
        csv_data = self.client.get(reverse('export_sales')).content.decode('utf-8')
        self.assertNotIn('PRIVATE-A', csv_data)
        self.assertEqual(report_for(self.org_b)['transaction_count'], 0)

    def test_aggregations_follow_documented_kpi_formulas(self):
        self.upload(HEADER + 'TX-1,2026-11-02,C-1,100.00,40.00\nTX-2,2026-11-03,C-1,200.00,90.00\n')
        cards = {item['slug']: item['value'] for item in report_for(self.org_a)['cards']}
        self.assertEqual(cards['revenue'], 'CAD 300.00')
        self.assertEqual(cards['gross_profit'], 'CAD 170.00')
        self.assertEqual(cards['margin'], '56.7%')
        self.assertEqual(cards['transactions'], '2')
        self.assertEqual(cards['aov'], 'CAD 150.00')
        self.assertEqual(cards['customers'], '1')

    def test_preferences_show_only_selected_metric(self):
        self.client.post(reverse('preferences'), {'kpis': ['revenue']})
        self.org_a.refresh_from_db()
        self.assertEqual(self.org_a.visible_kpis, ['revenue'])
        self.assertEqual(len(report_for(self.org_a)['cards']), 1)
        response = self.client.post(reverse('preferences'), {'kpis': ['malicious-expression']})
        self.assertEqual(response.status_code, 200)
        self.org_a.refresh_from_db()
        self.assertEqual(self.org_a.visible_kpis, ['revenue'])

    def test_unsigned_user_redirected(self):
        self.client.logout()
        for route in ['dashboard', 'upload_sales', 'preferences', 'export_sales']:
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)

    def test_revenue_and_cost_reject_negative_values(self):
        with self.assertRaises(CSVValidationError):
            parse_sales_csv(csv_upload(HEADER + 'X,2026-11-03,C-1,-100.00,40.00\n'))

    def test_spreadsheet_formula_export_is_escaped(self):
        self.assertEqual(safe_csv_cell('=HYPERLINK("https://evil.invalid")'), "'=HYPERLINK(\"https://evil.invalid\")")
        self.assertEqual(safe_csv_cell('  +CMD'), "'  +CMD")
        self.assertEqual(safe_csv_cell('CUST-1'), 'CUST-1')

    def test_cross_org_same_transaction_id_is_allowed(self):
        data = HEADER + 'SHARED-ID,2026-11-02,C-1,100.00,40.00\n'
        self.upload(data)
        self.client.force_login(self.bob)
        self.assertRedirects(self.upload(data), reverse('dashboard'))
        self.assertEqual(Sale.objects.filter(transaction_id='SHARED-ID').count(), 2)

    def test_reject_non_csv_file(self):
        result = self.upload('not a csv', 'book.xlsx')
        self.assertContains(result, 'Please upload a .csv file')


class SignupTests(TestCase):
    def test_signup_creates_one_owned_workspace(self):
        response = self.client.post(reverse('signup'), {
            'username': 'newowner', 'email': 'new@example.com',
            'organization_name': 'New Company', 'currency': 'CAD',
            'password1': 'ExtraStrongPass398!','password2': 'ExtraStrongPass398!',
        })
        self.assertRedirects(response, reverse('dashboard'))
        user = User.objects.get(username='newowner')
        self.assertEqual(Organization.objects.get(owner=user).name, 'New Company')
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
