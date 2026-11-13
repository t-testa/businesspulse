import csv
import io
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from tracker.models import Organization, Sale, SalesImport
from tracker.services import (
    MAX_CSV_BYTES, MAX_ROWS, CSVValidationError, parse_sales_csv,
    report_for, safe_csv_cell, save_sales_import,
)


HEADER = 'transaction_id,date,customer_id,revenue,cost\n'


def csv_upload(text, name='sales.csv'):
    return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')


# Request tests should work on a fresh checkout without collected static assets.
# Production manifest generation is verified separately with collectstatic.
TEST_STORAGES = {'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}


@override_settings(STORAGES=TEST_STORAGES)
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
        self.assertEqual(SalesImport.objects.count(), 0)

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

    def test_sample_fixture_matches_all_six_baseline_kpis(self):
        sample = Path(__file__).resolve().parents[2] / 'sample_data' / 'sales_example.csv'
        self.assertRedirects(self.upload(sample.read_text(encoding='utf-8')), reverse('dashboard'))
        response = self.client.get(reverse('dashboard'), {'period': 'all'})
        cards = {item['slug']: item['value'] for item in response.context['report']['cards']}
        self.assertEqual(cards, {
            'revenue': 'CAD 21,065.00', 'gross_profit': 'CAD 8,875.00',
            'margin': '42.1%', 'transactions': '12',
            'aov': 'CAD 1,755.42', 'customers': '7',
        })
        self.assertEqual(sum(Sale.objects.values_list('cost', flat=True)), Decimal('12190.00'))

    def test_populated_workspaces_ignore_forged_organization_parameters(self):
        self.upload(HEADER + 'ALICE,2026-11-02,C-A,100.00,40.00\n', 'alice-private.csv')
        self.client.force_login(self.bob)
        response = self.client.post(reverse('upload_sales'), {
            'organization': self.org_a.pk, 'uploaded_by': self.alice.pk,
            'file': csv_upload(HEADER + 'BOB,2026-11-02,C-B,900.00,300.00\n', 'bob-private.csv'),
        })
        self.assertRedirects(response, reverse('dashboard'))
        batch = SalesImport.objects.get(organization=self.org_b)
        self.assertEqual(batch.uploaded_by, self.bob)
        for user, own_name, other_name, revenue, own_id, other_id in [
            (self.alice, 'alice-private.csv', 'bob-private.csv', 'CAD 100.00', 'ALICE', 'BOB'),
            (self.bob, 'bob-private.csv', 'alice-private.csv', 'USD 900.00', 'BOB', 'ALICE'),
        ]:
            self.client.force_login(user)
            response = self.client.get(reverse('dashboard'), {'organization': self.org_a.pk})
            self.assertContains(response, own_name)
            self.assertNotContains(response, other_name)
            self.assertContains(response, revenue)
            exported = self.client.get(reverse('export_sales'), {'organization': self.org_a.pk})
            ids = [row['transaction_id'] for row in csv.DictReader(io.StringIO(exported.content.decode()))]
            self.assertEqual(ids, [own_id])
            self.assertNotIn(other_id, ids)
        self.client.post(reverse('preferences'), {'organization': self.org_a.pk, 'kpis': ['revenue']})
        self.org_a.refresh_from_db()
        self.org_b.refresh_from_db()
        self.assertEqual(len(self.org_a.visible_kpis), 6)
        self.assertEqual(self.org_b.visible_kpis, ['revenue'])

    def test_import_service_rejects_another_workspaces_user(self):
        rows = parse_sales_csv(csv_upload(HEADER + 'X,2026-11-02,C,10,2\n'))
        with self.assertRaises(CSVValidationError):
            save_sales_import(organization=self.org_a, user=self.bob, file_name='sales.csv', rows=rows)
        self.assertEqual(SalesImport.objects.count(), 0)
        self.assertEqual(Sale.objects.count(), 0)

    def test_export_escapes_both_identifier_columns(self):
        self.assertRedirects(self.upload(HEADER + '=1+1,2026-11-02,@SUM(A1),100,40\n'), reverse('dashboard'))
        response = self.client.get(reverse('export_sales'))
        row = list(csv.DictReader(io.StringIO(response.content.decode())))[0]
        self.assertEqual(row['transaction_id'], "'=1+1")
        self.assertEqual(row['customer_id'], "'@SUM(A1)")
        self.assertEqual(row['revenue'], '100.00')

    def test_ambiguous_headers_reject_upload_without_writes(self):
        response = self.upload('transaction_id,date,customer_id,revenue,amount,cost\nX,2026-11-02,C,100,999,40\n')
        self.assertContains(response, 'Ambiguous columns for revenue')
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(SalesImport.objects.count(), 0)

    def test_mixed_new_and_identical_rows_count_correctly(self):
        existing = 'X,2026-11-02,C,100,40\n'
        self.upload(HEADER + existing)
        self.assertRedirects(self.upload(HEADER + existing + 'Y,2026-11-03,C,50,20\n'), reverse('dashboard'))
        batch = SalesImport.objects.first()
        self.assertEqual((batch.inserted_count, batch.duplicate_count), (1, 1))
        self.assertEqual(Sale.objects.count(), 2)


class CSVValidationTests(SimpleTestCase):
    def test_invalid_input_matrix(self):
        invalid_files = [
            '', HEADER,
            'transaction_id,date,customer_id,revenue\nX,2026-11-02,C,1\n',
            'transaction_id,date,customer_id,revenue,Revenue,cost\nX,2026-11-02,C,1,2,3\n',
            HEADER + 'X,2026-02-30,C,1,0\n',
            HEADER + 'X,20261102,C,1,0\n',
            HEADER + ',2026-11-02,C,1,0\n',
            HEADER + 'X,2026-11-02,,1,0\n',
            HEADER + 'X' * 101 + ',2026-11-02,C,1,0\n',
            HEADER + 'X,2026-11-02,' + 'C' * 101 + ',1,0\n',
            HEADER + 'X,2026-11-02,C,1\n',
            HEADER + 'X,2026-11-02,C,1,0,extra\n',
            HEADER + '"X,2026-11-02,C,1,0\n',
            HEADER + 'X,2026-11-02,C,1,0\x00\n',
        ]
        for text in invalid_files:
            with self.subTest(text=text), self.assertRaises(CSVValidationError):
                parse_sales_csv(csv_upload(text))

    def test_invalid_amounts_in_both_columns(self):
        for amount in ['-1', 'NaN', 'Infinity', '1e2', '1.001', '1000000000000', '"1,23"', '']:
            for revenue, cost in [(amount, '0'), ('1', amount)]:
                with self.subTest(revenue=revenue, cost=cost), self.assertRaises(CSVValidationError):
                    parse_sales_csv(csv_upload(HEADER + f'X,2026-11-02,C,{revenue},{cost}\n'))

    def test_invalid_encoding_and_size(self):
        for data in [b'\xff', b'x' * (MAX_CSV_BYTES + 1)]:
            with self.subTest(size=len(data)), self.assertRaises(CSVValidationError):
                parse_sales_csv(SimpleUploadedFile('sales.csv', data))

    def test_row_limit_boundary(self):
        text = HEADER + ''.join(f'X-{n},2026-11-02,C,1,0\n' for n in range(MAX_ROWS))
        self.assertEqual(len(parse_sales_csv(csv_upload(text))), MAX_ROWS)
        with self.assertRaisesMessage(CSVValidationError, 'Too many rows'):
            parse_sales_csv(csv_upload(text + 'TOO-MANY,2026-11-02,C,1,0\n'))

    def test_bom_and_amount_boundaries(self):
        rows = parse_sales_csv(csv_upload('\ufeff' + HEADER + 'X,2026-11-02,C,"999,999,999,999.99",0\n'))
        self.assertEqual(rows[0].revenue, Decimal('999999999999.99'))
        self.assertEqual(rows[0].cost, Decimal('0.00'))


@override_settings(STORAGES=TEST_STORAGES)
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
