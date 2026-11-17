import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tracker.models import ColumnMapping, Expense, ExpenseColumnMapping, ExpenseImport, Organization, Sale
from tracker.services import (CSVValidationError, EXPENSE_ALIASES, MAX_CSV_BYTES, MAX_ROWS,
                              parse_expense_upload, report_for, save_expense_import)
from .test_imports import HEADER, TEST_STORAGES, csv_upload

EXPENSE_HEADER = 'expense_id,date,category,amount\n'
EXAMPLE = 'E1,2026-11-01,Rent,600\nE2,2026-11-02,Software,100\nE3,2026-11-03,Utilities,50\n'
CUSTOM = 'Reference,When,Group,Value\nE1,2026-11-01,Rent,600\n'
MAPPING = dict(zip(EXPENSE_ALIASES, ['Reference', 'When', 'Group', 'Value']))


@override_settings(STORAGES=TEST_STORAGES)
class ExpenseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='expense-owner')
        cls.other = get_user_model().objects.create_user(username='other-expense-owner')
        cls.org = Organization.objects.create(owner=cls.user, name='Mapping Test Company', currency='CAD')
        cls.other_org = Organization.objects.create(owner=cls.other, name='Other synthetic company', currency='USD')

    def setUp(self):
        self.client.force_login(self.user)

    def upload(self, text=EXPENSE_HEADER + EXAMPLE, **fields):
        return self.client.post(reverse('upload_expenses'), {'file': csv_upload(text, 'synthetic-expenses.csv'), **fields})

    def sales(self, text='S1,2026-11-01,C1,3700,2200\n'):
        return self.client.post(reverse('upload_sales'), {'file': csv_upload(HEADER + text)})

    def cards(self, period='all'):
        return {c['label']: c['value'] for c in report_for(self.org, period)['expense_cards']}

    def assert_empty(self):
        self.assertFalse(Expense.objects.exists())
        self.assertFalse(ExpenseImport.objects.exists())
        self.assertFalse(ExpenseColumnMapping.objects.exists())

    def test_exact_synthetic_fixture_and_existing_sales_metrics(self):
        self.sales()
        fixture = Path(__file__).resolve().parents[2] / 'sample_data' / 'expenses_example.csv'
        self.assertRedirects(self.upload(fixture.read_text()), reverse('dashboard'))
        self.assertEqual(self.cards(), {'Total operating expenses': 'CAD 750.00',
            'Estimated operating result': 'CAD 750.00', 'Estimated operating margin': '20.3%'})
        report = report_for(self.org)
        sales = {c['slug']: c['value'] for c in report['cards']}
        self.assertEqual(sales['revenue'], 'CAD 3,700.00')
        self.assertEqual(sales['gross_profit'], 'CAD 1,500.00')
        self.assertEqual(sales['margin'], '40.5%')
        self.assertEqual(report['expense_categories'], [
            {'label': 'Rent', 'amount': 'CAD 600.00'}, {'label': 'Software', 'amount': 'CAD 100.00'},
            {'label': 'Utilities', 'amount': 'CAD 50.00'}])
        self.assertEqual(report['expense_chart'], [{'label': 'Nov 2026', 'amount': 'CAD 750.00', 'width': 100}])
        self.assertEqual(Sale.objects.count(), 1)

    def test_missing_expenses_genuine_zero_and_zero_revenue(self):
        self.sales()
        self.assertEqual(self.cards()['Total operating expenses'], 'Not imported')
        self.assertEqual(self.cards()['Estimated operating result'], 'N/A')
        self.assertContains(self.client.get(reverse('dashboard')), 'No expense data imported')
        self.upload(EXPENSE_HEADER + 'ZERO,2026-11-01,Rent,0\n')
        self.assertEqual(self.cards()['Total operating expenses'], 'CAD 0.00')
        self.assertEqual(self.cards()['Estimated operating result'], 'CAD 1,500.00')
        self.client.force_login(self.other)
        self.upload(EXPENSE_HEADER + 'ZERO,2026-11-01,Rent,100\n')
        report = report_for(self.other_org)
        self.assertEqual(report['expense_cards'][1]['value'], 'USD -100.00')
        self.assertEqual(report['expense_cards'][2]['value'], 'N/A')
        self.assertContains(self.client.get(reverse('dashboard')), 'USD 100.00')

    @patch('tracker.services.timezone.localdate', return_value=date(2026, 11, 17))
    def test_period_boundaries_shared_by_sales_expenses_and_charts(self, today):
        dates = ['2025-12-31', '2026-01-01', '2026-08-19', '2026-08-20',
                 '2026-10-18', '2026-10-19', '2026-11-17', '2026-11-18']
        self.sales(''.join(f'S{i},{day},C,100,20\n' for i, day in enumerate(dates)))
        self.upload(EXPENSE_HEADER + ''.join(f'E{i},{day},Rent,10\n' for i, day in enumerate(dates)))
        for period, count in [('30', 2), ('90', 4), ('ytd', 6), ('all', 8), ('invalid', 8)]:
            with self.subTest(period=period):
                report = report_for(self.org, period)
                self.assertEqual(report['expense_count'], count)
                self.assertEqual(report['transaction_count'], count)
                self.assertEqual(report['expense_cards'][0]['value'], f'CAD {count * 10:.2f}')
                self.assertEqual(report['expense_cards'][1]['value'], f'CAD {count * 70:.2f}')
                self.assertEqual(report['expense_cards'][2]['value'], '70.0%')
                self.assertEqual(report['expense_categories'][0]['amount'], f'CAD {count * 10:.2f}')
        self.assertEqual([x['label'] for x in report_for(self.org, '30')['expense_chart']], ['Oct 2026', 'Nov 2026'])

    @patch('tracker.services.timezone.localdate', return_value=date(2027, 11, 17))
    def test_no_rows_in_period_is_qualified_zero_and_filter_remains_visible(self, today):
        self.upload()
        response = self.client.get(reverse('dashboard'), {'period': '30'})
        self.assertContains(response, 'No expense transactions in this period')
        self.assertContains(response, 'Reporting period')
        self.assertEqual(self.cards('30')['Total operating expenses'], 'CAD 0.00')
        self.assertEqual(self.cards('30')['Estimated operating margin'], 'N/A')

    def test_duplicate_and_mixed_uploads_do_not_double_count(self):
        self.upload()
        self.assertRedirects(self.upload(), reverse('dashboard'))
        self.assertEqual(Expense.objects.count(), 3)
        batch = ExpenseImport.objects.first()
        self.assertEqual((batch.inserted_count, batch.duplicate_count), (0, 3))
        self.upload(EXPENSE_HEADER + EXAMPLE + 'NEW,2026-11-04,Rent,10\n')
        batch = ExpenseImport.objects.first()
        self.assertEqual((batch.inserted_count, batch.duplicate_count), (1, 3))
        self.assertEqual(self.cards()['Total operating expenses'], 'CAD 760.00')

    def test_each_conflicting_field_rolls_back_new_rows_history_and_mapping(self):
        self.upload(mapping_name='Original')
        saved = ExpenseColumnMapping.objects.get()
        for row in ['E1,2026-11-02,Rent,600', 'E1,2026-11-01,Software,600', 'E1,2026-11-01,Rent,601']:
            with self.subTest(row=row):
                response = self.upload(EXPENSE_HEADER + 'NEW,2026-11-01,Rent,1\n' + row + '\n', mapping_name='Original')
                self.assertContains(response, 'already exists with different data')
                self.assertEqual(Expense.objects.count(), 3)
                self.assertEqual(ExpenseImport.objects.count(), 1)
                self.assertEqual(ExpenseColumnMapping.objects.get().updated_at, saved.updated_at)

    def test_invalid_data_matrix_and_late_row_reject_every_write(self):
        cases = ['', EXPENSE_HEADER, 'id,date,amount\nX,2026-11-01,1\n',
            EXPENSE_HEADER + 'X,2026-02-30,Rent,1\n', EXPENSE_HEADER + 'X,20261101,Rent,1\n',
            EXPENSE_HEADER + ',2026-11-01,Rent,1\n', EXPENSE_HEADER + 'X,2026-11-01,,1\n',
            EXPENSE_HEADER + 'X' * 101 + ',2026-11-01,Rent,1\n',
            EXPENSE_HEADER + 'X,2026-11-01,' + 'R' * 101 + ',1\n',
            EXPENSE_HEADER + 'X,2026-11-01,Rent\n', EXPENSE_HEADER + 'X,2026-11-01,Rent,1,extra\n',
            EXPENSE_HEADER + '"X,2026-11-01,Rent,1\n', EXPENSE_HEADER + EXAMPLE + EXAMPLE,
            EXPENSE_HEADER + ''.join(f'X{n},2026-11-01,Rent,1\n' for n in range(10)) + 'BAD,2026-02-30,Rent,1\n']
        cases += [EXPENSE_HEADER + f'X,2026-11-01,Rent,{amount}\n' for amount in
                  ['-1', 'NaN', 'Infinity', '1e2', '1.001', '1000000000000', '"1,23"', '', '\x00']]
        for text in cases:
            with self.subTest(text=text[:100]):
                response = self.upload(text, mapping_name='No save')
                self.assertTrue(response.context['form'].errors)
                self.assert_empty()

    def test_server_limits_encoding_headers_rows_and_amount_boundary(self):
        for raw in [b'\xff', b'x' * (MAX_CSV_BYTES + 1)]:
            with self.assertRaises(CSVValidationError):
                parse_expense_upload(SimpleUploadedFile('expense.csv', raw))
        for header in ['id,date,category,amount,Amount', 'id,date,,amount', 'H'*201, ','.join(f'H{i}' for i in range(101))]:
            with self.assertRaises(CSVValidationError):
                parse_expense_upload(csv_upload(header + '\nX,2026-11-01,Rent,1\n'))
        text = EXPENSE_HEADER + ''.join(f'X{n},2026-11-01,Rent,0\n' for n in range(MAX_ROWS))
        self.assertEqual(len(parse_expense_upload(csv_upload(text))[0]), MAX_ROWS)
        with self.assertRaises(CSVValidationError):
            parse_expense_upload(csv_upload(text + 'OVER,2026-11-01,Rent,0\n'))
        rows, _ = parse_expense_upload(csv_upload('\ufeff'+EXPENSE_HEADER+'X,2026-11-01, Rent ,"999,999,999,999.99"\n'))
        self.assertEqual((rows[0].category, rows[0].amount), ('Rent', Decimal('999999999999.99')))

    def test_custom_mapping_save_reuse_reorder_and_update(self):
        self.assertRedirects(self.upload(CUSTOM, mapping=json.dumps(MAPPING), mapping_name='Export'), reverse('dashboard'))
        profile = ExpenseColumnMapping.objects.get()
        self.assertEqual(profile.columns, MAPPING)
        self.assertRedirects(self.upload('Value,Group,When,Reference\n100,Software,2026-11-02,E2\n', saved_mapping=profile.pk), reverse('dashboard'))
        changed = {**MAPPING, 'amount': 'Paid'}
        self.assertRedirects(self.upload(CUSTOM.replace('Value', 'Paid'), mapping=json.dumps(changed), mapping_name='Export'), reverse('dashboard'))
        self.assertEqual(ExpenseColumnMapping.objects.get().columns, changed)
        self.assertEqual(Expense.objects.count(), 2)

    def test_invalid_mappings_rejected_on_server(self):
        for mapping in ['{}', 'null', '[]', '{', json.dumps({'amount': 'Value'}),
                        json.dumps({**MAPPING, 'amount': 'Missing'}), json.dumps({**MAPPING, 'amount': 'Group'}),
                        json.dumps({**MAPPING, 'extra': 'Value'}), json.dumps({**MAPPING, 'amount': []})]:
            with self.subTest(mapping=mapping):
                response = self.upload(CUSTOM, mapping=mapping, mapping_name='Never')
                self.assertTrue(response.context['form'].errors)
                self.assert_empty()

    def test_ownership_profiles_history_and_reports_are_isolated(self):
        self.upload(mapping_name='Private A')
        profile = ExpenseColumnMapping.objects.get()
        self.client.force_login(self.other)
        page = self.client.get(reverse('upload_expenses'))
        self.assertNotContains(page, 'Private A')
        response = self.upload(saved_mapping=profile.pk)
        self.assertIn('saved_mapping', response.context['form'].errors)
        self.assertContains(self.client.get(reverse('dashboard')), 'No expense data imported')
        self.assertRedirects(self.upload(EXPENSE_HEADER + 'E1,2026-11-01,PRIVATE B,99\n',
            organization=self.org.pk, uploaded_by=self.user.pk, mapping_name='Private A'), reverse('dashboard'))
        batch = ExpenseImport.objects.get(organization=self.other_org)
        self.assertEqual(batch.uploaded_by, self.other)
        self.assertEqual(ExpenseColumnMapping.objects.count(), 2)
        self.client.force_login(self.user)
        page = self.client.get(reverse('dashboard'), {'organization': self.other_org.pk})
        self.assertNotContains(page, 'PRIVATE B')
        self.assertEqual(self.cards()['Total operating expenses'], 'CAD 750.00')
        rows, _ = parse_expense_upload(csv_upload(EXPENSE_HEADER + EXAMPLE))
        with self.assertRaises(CSVValidationError):
            save_expense_import(organization=self.org, user=self.other, rows=rows, file_name='wrong.csv')
        self.assertEqual(ExpenseImport.objects.count(), 2)

    def test_sales_and_expense_profile_namespaces_and_ids_are_separate(self):
        sales_mapping = ColumnMapping.objects.create(organization=self.org, name='Sales only', columns={'date': 'date'})
        expense_mapping = ExpenseColumnMapping.objects.create(pk=sales_mapping.pk, organization=self.org, name='Expense only', columns=MAPPING)
        self.assertNotContains(self.client.get(reverse('upload_expenses')), 'Sales only')
        self.assertNotContains(self.client.get(reverse('upload_sales')), 'Expense only')
        # Same numeric PK can exist in each table; resolution must use route-specific table.
        self.assertEqual(sales_mapping.pk, expense_mapping.pk)
        response = self.upload(CUSTOM, saved_mapping=expense_mapping.pk)
        self.assertRedirects(response, reverse('dashboard'))
        self.sales('E1,2026-11-01,C,100,40\n')
        self.assertEqual((Sale.objects.count(), Expense.objects.count()), (1, 1))
        self.assertEqual(sales_mapping.columns, {'date': 'date'})

    def test_profile_failure_rolls_back_expenses(self):
        with patch('tracker.views.ExpenseColumnMapping.objects.update_or_create', side_effect=RuntimeError('simulated metadata failure')):
            with self.assertRaises(RuntimeError):
                self.upload(mapping_name='Fail')
        self.assert_empty()

    def test_login_csrf_and_http_method_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('upload_expenses'), {'file': csv_upload(EXPENSE_HEADER+EXAMPLE)}).status_code, 403)
        self.assertEqual(self.client.delete(reverse('upload_expenses')).status_code, 405)
        self.client.logout()
        self.assertEqual(self.upload().status_code, 302)
        self.assert_empty()

    def test_database_constraints_and_import_organization_validation(self):
        self.upload()
        row = Expense.objects.get(expense_id='E1')
        for changes in [{'expense_id': 'E1'}, {'expense_id': 'NEG', 'amount': -1}]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Expense.objects.create(organization=self.org, import_batch=row.import_batch,
                    date=row.date, category='Rent', **{'expense_id':'NEW', 'amount':1, **changes})
        row.organization = self.other_org
        with self.assertRaises(ValidationError):
            row.clean()

    def test_negative_result_grouped_categories_and_months(self):
        self.sales('S1,2026-11-01,C,100,40\n')
        self.upload(EXPENSE_HEADER + 'A,2026-10-01,Rent,50\nB,2026-11-01,Rent,50\nC,2026-11-01,Software,20\n')
        report = report_for(self.org)
        self.assertEqual(self.cards()['Estimated operating result'], 'CAD -60.00')
        self.assertEqual(self.cards()['Estimated operating margin'], '-60.0%')
        self.assertEqual(report['expense_categories'][0], {'label':'Rent', 'amount':'CAD 100.00'})
        self.assertEqual([row['amount'] for row in report['expense_chart']], ['CAD 50.00', 'CAD 70.00'])

    def test_untrusted_category_and_filename_are_escaped_on_dashboard(self):
        self.upload(EXPENSE_HEADER + 'X,2026-11-01,<script>alert(1)</script>,1\n')
        page = self.client.get(reverse('dashboard'))
        self.assertNotContains(page, '<script>alert(1)</script>')
        self.assertContains(page, '&lt;script&gt;alert(1)&lt;/script&gt;')
