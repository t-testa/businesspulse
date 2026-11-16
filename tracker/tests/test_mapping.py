import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile, SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tracker.models import ColumnMapping, Organization, Sale, SalesImport
from tracker.services import ALIASES, CSVValidationError, MAX_CSV_BYTES, parse_sales_csv
from .test_imports import HEADER, TEST_STORAGES, csv_upload


CUSTOM_HEADER = 'Receipt,When,Buyer,Net,Expense,Note\n'
CUSTOM_ROW = 'X,2026-11-01,C,100.00,40.00,ignored\n'
MAPPING = dict(zip(ALIASES, ['Receipt', 'When', 'Buyer', 'Net', 'Expense']))


@override_settings(STORAGES=TEST_STORAGES)
class ColumnMappingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='mapping-owner')
        cls.other = get_user_model().objects.create_user(username='other-owner')
        cls.org = Organization.objects.create(owner=cls.user, name='Synthetic A')
        cls.other_org = Organization.objects.create(owner=cls.other, name='Synthetic B')

    def setUp(self):
        self.client.force_login(self.user)

    def upload(self, text=CUSTOM_HEADER + CUSTOM_ROW, **fields):
        return self.client.post(reverse('upload_sales'), {'file': csv_upload(text), **fields})

    def assert_no_writes(self):
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(SalesImport.objects.exists())
        self.assertFalse(ColumnMapping.objects.exists())

    def test_custom_mapping_imports_expected_values(self):
        response = self.upload(mapping=json.dumps(MAPPING))
        self.assertRedirects(response, reverse('dashboard'))
        sale = Sale.objects.get()
        self.assertEqual((sale.transaction_id, sale.customer_id, str(sale.revenue), str(sale.cost)),
                         ('X', 'C', '100.00', '40.00'))
        self.assertEqual(sale.organization, self.org)

    def test_bad_mapping_shapes_or_values_cannot_bypass_server(self):
        cases = ['{', 'null', '[]', 'true', '42', '{}', json.dumps({'date': 'When'}),
                 json.dumps({**MAPPING, 'sql': 'DROP TABLE tracker_sale'}),
                 json.dumps({**MAPPING, 'cost': 'Missing'}),
                 json.dumps({**MAPPING, 'cost': 'Net'}),
                 json.dumps({**MAPPING, 'cost': ['Expense']}),
                 json.dumps({**MAPPING, 'cost': None})]
        for value in cases:
            with self.subTest(mapping=value):
                response = self.upload(mapping=value, mapping_name='Do not save')
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assert_no_writes()

    def test_explicit_mapping_resolves_alias_ambiguity(self):
        mapping = {field: field for field in ALIASES}
        data = 'transaction_id,date,customer_id,revenue,cost,amount\nX,2026-11-01,C,100,40,999\n'
        self.assertRedirects(self.upload(data, mapping=json.dumps(mapping)), reverse('dashboard'))
        self.assertEqual(str(Sale.objects.get().revenue), '100.00')

    def test_headers_still_reject_duplicates_empty_and_limits(self):
        for header in ['Receipt,When,Buyer,Net,Net,Note', 'Receipt,When,Buyer,Net,,Note',
                       ','.join(f'col{n}' for n in range(101)), 'H' * 201]:
            with self.subTest(header=header), self.assertRaises(CSVValidationError):
                parse_sales_csv(csv_upload(header + '\n' + CUSTOM_ROW), MAPPING)

    def test_invalid_row_after_preview_rejects_everything(self):
        text = CUSTOM_HEADER + ''.join(f'X-{n},2026-11-01,C,100,40,ignored\n' for n in range(10))
        response = self.upload(text + 'BAD,2026-02-30,C,100,40,ignored\n',
                               mapping=json.dumps(MAPPING), mapping_name='Never saved')
        self.assertContains(response, 'invalid calendar date')
        self.assert_no_writes()

    def test_save_reuse_reorder_and_update_mapping(self):
        self.assertRedirects(self.upload(mapping=json.dumps(MAPPING), mapping_name='My export'), reverse('dashboard'))
        saved = ColumnMapping.objects.get()
        self.assertEqual(saved.columns, MAPPING)
        self.assertEqual(saved.organization, self.org)
        reordered = 'Expense,Buyer,Net,When,Receipt\n20,C,50,2026-11-02,Y\n'
        self.assertRedirects(self.upload(reordered, saved_mapping=saved.pk), reverse('dashboard'))
        self.assertEqual(Sale.objects.count(), 2)
        updated = {**MAPPING, 'revenue': 'Amount'}
        self.assertRedirects(self.upload((CUSTOM_HEADER + CUSTOM_ROW).replace('Net', 'Amount'),
                                         mapping=json.dumps(updated), mapping_name='My export'), reverse('dashboard'))
        saved.refresh_from_db()
        self.assertEqual(saved.columns, updated)
        self.assertEqual(ColumnMapping.objects.count(), 1)

    def test_automatic_mapping_can_be_saved_without_javascript(self):
        self.assertRedirects(self.upload(HEADER + 'X,2026-11-01,C,100,40\n', mapping_name='Canonical'), reverse('dashboard'))
        self.assertEqual(ColumnMapping.objects.get().columns, {field: field for field in ALIASES})

    def test_corrected_dropdowns_override_selected_profile(self):
        saved = ColumnMapping.objects.create(organization=self.org, name='Old', columns={**MAPPING, 'cost': 'Old expense'})
        self.assertRedirects(self.upload(saved_mapping=saved.pk, mapping=json.dumps(MAPPING)), reverse('dashboard'))
        saved.refresh_from_db()
        self.assertEqual(saved.columns['cost'], 'Old expense')  # No implicit overwrite.

    def test_stale_saved_mapping_fails_without_writes(self):
        saved = ColumnMapping.objects.create(organization=self.org, name='Old', columns={**MAPPING, 'cost': 'Old expense'})
        response = self.upload(saved_mapping=saved.pk, mapping_name='New')
        self.assertContains(response, 'Every mapped column must exist')
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(SalesImport.objects.exists())
        self.assertEqual(ColumnMapping.objects.count(), 1)

    def test_profiles_are_private_and_forged_ids_rejected(self):
        foreign = ColumnMapping.objects.create(organization=self.other_org, name='PRIVATE PROFILE', columns=MAPPING)
        page = self.client.get(reverse('upload_sales'))
        self.assertNotContains(page, 'PRIVATE PROFILE')
        self.assertEqual(page.context['saved_mappings'], [])
        for value in [foreign.pk, 999999, 'invalid']:
            with self.subTest(profile=value):
                response = self.upload(saved_mapping=value, mapping=json.dumps(MAPPING))
                self.assertEqual(response.status_code, 200)
                self.assertIn('saved_mapping', response.context['form'].errors)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(SalesImport.objects.exists())

    def test_same_name_is_independent_across_workspaces(self):
        self.upload(mapping=json.dumps(MAPPING), mapping_name='Export', organization=self.other_org.pk)
        self.client.force_login(self.other)
        self.upload(mapping=json.dumps(MAPPING), mapping_name='Export', organization=self.org.pk)
        self.assertEqual(ColumnMapping.objects.filter(name='Export').count(), 2)
        self.assertEqual(Sale.objects.filter(transaction_id='X').count(), 2)

    def test_duplicate_and_conflict_preserve_atomic_profile_changes(self):
        self.upload(mapping=json.dumps(MAPPING), mapping_name='Export')
        self.assertRedirects(self.upload(mapping=json.dumps(MAPPING), mapping_name='Export'), reverse('dashboard'))
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(SalesImport.objects.first().duplicate_count, 1)
        saved = ColumnMapping.objects.get()
        timestamp = saved.updated_at
        response = self.upload((CUSTOM_HEADER + CUSTOM_ROW).replace('100.00', '999.00'),
                               mapping=json.dumps(MAPPING), mapping_name='Export')
        self.assertContains(response, 'already exists with different data')
        saved.refresh_from_db()
        self.assertEqual(saved.updated_at, timestamp)
        self.assertEqual(SalesImport.objects.count(), 2)

    def test_profile_write_failure_rolls_back_sales_and_history(self):
        with patch('tracker.views.ColumnMapping.objects.update_or_create', side_effect=RuntimeError('simulated DB failure')):
            with self.assertRaises(RuntimeError):
                self.upload(mapping=json.dumps(MAPPING), mapping_name='Export')
        self.assert_no_writes()

    def test_mapping_name_limits(self):
        response = self.upload(mapping=json.dumps(MAPPING), mapping_name='X' * 101)
        self.assertIn('mapping_name', response.context['form'].errors)
        self.assert_no_writes()

    def test_saved_headers_are_json_escaped(self):
        malicious = '</script><script>alert(1)</script>'
        ColumnMapping.objects.create(organization=self.org, name=malicious, columns={**MAPPING, 'cost': malicious})
        page = self.client.get(reverse('upload_sales'))
        self.assertNotContains(page, malicious)
        self.assertContains(page, '\\u003C/script\\u003E')

    def test_file_is_in_memory_even_when_default_threshold_would_spool(self):
        from tracker.services import parse_sales_upload
        def inspect_file(uploaded, mapping):
            self.assertIsInstance(uploaded, InMemoryUploadedFile)
            return parse_sales_upload(uploaded, mapping)
        with override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1), \
                patch('tracker.views.parse_sales_upload', side_effect=inspect_file), \
                patch('django.core.files.uploadhandler.TemporaryFileUploadHandler.new_file', side_effect=AssertionError('disk upload')):
            self.assertRedirects(self.upload(mapping=json.dumps(MAPPING)), reverse('dashboard'))

    def test_oversized_and_multiple_files_reject_without_writes(self):
        with patch('django.core.files.uploadhandler.TemporaryFileUploadHandler.new_file', side_effect=AssertionError('disk upload')):
            response = self.client.post(reverse('upload_sales'), {
                'file': SimpleUploadedFile('large.csv', b'x' * (MAX_CSV_BYTES + 1)),
            })
            self.assertContains(response, 'Maximum file size is 2 MB')
            self.assert_no_writes()
            response = self.client.post(reverse('upload_sales'), {
                'file': [csv_upload(HEADER + 'X,2026-11-01,C,100,40\n'), csv_upload(HEADER + 'Y,2026-11-01,C,100,40\n')],
            })
            self.assertContains(response, 'Upload exactly one CSV')
            self.assert_no_writes()

    def test_csrf_and_login_remain_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('upload_sales'), {'file': csv_upload(CUSTOM_HEADER + CUSTOM_ROW)}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.upload(mapping=json.dumps(MAPPING), mapping_name='No').status_code, 302)
        self.assert_no_writes()
