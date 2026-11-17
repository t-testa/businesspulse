"""Synthetic acceptance, authorization, audit and correction regressions."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from tracker.import_management import TOKEN_SALT, preview_import, reverse_import
from tracker.models import Expense, ExpenseImport, Organization, Sale, SalesImport, ColumnMapping
from tracker.services import (CSVValidationError, ExpenseRow, SaleRow, parse_sales_csv,
                              report_for, save_expense_import, save_sales_import)
from .test_imports import TEST_STORAGES, csv_upload


@override_settings(STORAGES=TEST_STORAGES)
class ImportManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='demo-owner')
        cls.org = Organization.objects.create(owner=cls.user, name='Demo Company')
        cls.other = get_user_model().objects.create_user(username='mapping-owner')
        cls.other_org = Organization.objects.create(owner=cls.other, name='mappingtest')

    def setUp(self):
        self.client.force_login(self.user)

    def upload(self, dataset='expenses', organization=None, user=None, amount='750', identifier='E1'):
        if dataset == 'expenses':
            return save_expense_import(organization=organization or self.org, user=user or self.user,
                file_name='synthetic-expenses.csv', rows=[ExpenseRow(identifier, date(2026,11,1), 'Rent', Decimal(amount))])
        return save_sales_import(organization=organization or self.org, user=user or self.user,
            file_name='synthetic-sales.csv', rows=[SaleRow(identifier, date(2026,11,1), 'C', Decimal(amount), Decimal('40'))])

    def preview(self, batch, dataset='expenses'):
        return preview_import(organization=self.org, user=self.user, dataset=dataset, batch_id=batch.pk)

    def reverse_batch(self, batch, dataset='expenses', token=None):
        return reverse_import(organization=self.org, user=self.user, dataset=dataset, batch_id=batch.pk,
            token=token or self.preview(batch, dataset)['token'], reason='Incorrect source export')

    def url(self, batch, dataset='expenses'):
        return reverse('import_detail', args=[dataset, batch.pk])

    def test_required_acceptance_original_duplicate_reversal_reimport_and_other_workspace(self):
        fixture = Path(__file__).resolve().parents[2] / 'sample_data/sales_example.csv'
        save_sales_import(organization=self.org, user=self.user, file_name='synthetic-sales.csv', rows=parse_sales_csv(csv_upload(fixture.read_text())))
        rows = [ExpenseRow('E'+str(i), date(2026,11,1), category, Decimal(amount))
                for i, (category, amount) in enumerate([('Rent','600'),('Software','100'),('Utilities','50')])]
        original = save_expense_import(organization=self.org, user=self.user, file_name='original.csv', rows=rows)
        duplicate = save_expense_import(organization=self.org, user=self.user, file_name='duplicate.csv', rows=rows)
        save_expense_import(organization=self.other_org, user=self.other, file_name='mappingtest.csv', rows=rows)
        before_sales = report_for(self.org)['cards']
        preview = self.preview(original)
        self.assertEqual(len(preview['records']), 3)
        self.assertEqual(preview['impact'][3], {'label':'Total operating expenses', 'before':'CAD 750.00', 'after':'Not imported'})
        self.reverse_batch(original, token=preview['token'])
        original.refresh_from_db(); duplicate.refresh_from_db()
        self.assertEqual(original.status, 'Reversed')
        self.assertEqual(duplicate.status, 'Active')
        self.assertEqual((duplicate.inserted_count, duplicate.duplicate_count), (0,3))
        self.assertEqual(ExpenseImport.objects.filter(organization=self.org).count(), 2)
        self.assertEqual(Sale.objects.filter(organization=self.org, is_active=True).count(), 12)
        self.assertEqual(report_for(self.org)['cards'], before_sales)
        self.assertFalse(report_for(self.org)['expense_available'])
        self.assertEqual(report_for(self.other_org)['expense_cards'][0]['value'], 'CAD 750.00')
        again = save_expense_import(organization=self.org, user=self.user, file_name='again.csv', rows=rows)
        self.assertEqual((again.inserted_count, again.duplicate_count), (3,0))
        self.assertEqual(Expense.objects.filter(organization=self.org).count(), 6)
        self.assertEqual(Expense.objects.filter(organization=self.org, is_active=True).count(), 3)
        self.assertEqual(report_for(self.org)['expense_cards'][0]['value'], 'CAD 750.00')
        self.assertFalse(self.reverse_batch(original, token=preview['token']))
        self.assertEqual(Expense.objects.filter(organization=self.org, is_active=True).count(), 3)

    def test_sales_reversal_export_kpis_audit_and_corrected_id(self):
        batch = self.upload('sales', amount='100')
        self.upload('expenses', amount='10')
        profile = ColumnMapping.objects.create(organization=self.org, name='Keep', columns={'date':'date'})
        preview = self.preview(batch, 'sales')
        self.assertEqual(preview['impact'][0]['after'], 'CAD 0.00')
        self.assertEqual(preview['impact'][2]['after'], 'CAD 0.00')
        self.assertEqual(preview['impact'][4]['after'], 'CAD -10.00')
        self.assertEqual(preview['impact'][5]['after'], 'N/A')
        self.reverse_batch(batch, 'sales', preview['token'])
        batch.refresh_from_db()
        self.assertEqual(batch.reversed_by, self.user)
        self.assertEqual(batch.reversal_reason, 'Incorrect source export')
        self.assertIsNotNone(batch.reversed_at)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(report_for(self.org)['transaction_count'], 0)
        self.assertEqual(report_for(self.org)['chart_rows'], [])
        self.assertNotContains(self.client.get(reverse('export_sales')), 'E1')
        self.assertTrue(ColumnMapping.objects.filter(pk=profile.pk).exists())
        self.upload('sales', amount='200')
        self.assertEqual(Sale.objects.count(), 2)
        self.assertEqual(Sale.objects.filter(is_active=True).get().revenue, Decimal('200'))
        self.assertContains(self.client.get(reverse('export_sales')), '200.00')
        with self.assertRaises(CSVValidationError):
            self.upload('sales', amount='300')

    def test_duplicate_only_reversal_owns_no_transactions(self):
        for dataset, model in [('sales', Sale), ('expenses', Expense)]:
            with self.subTest(dataset=dataset):
                original = self.upload(dataset)
                duplicate = self.upload(dataset)
                preview = self.preview(duplicate, dataset)
                self.assertEqual(preview['records'], [])
                self.assertTrue(all(row['before'] == row['after'] for row in preview['impact']))
                self.reverse_batch(duplicate, dataset)
                self.assertEqual(model.objects.filter(is_active=True).count(), 1)
                original.refresh_from_db()
                self.assertEqual(original.status, 'Active')

    def test_mixed_duplicate_import_reverses_only_created_rows(self):
        self.upload()
        batch = save_expense_import(organization=self.org, user=self.user, file_name='mixed.csv', rows=[
            ExpenseRow('E1',date(2026,11,1),'Rent',Decimal('750')),
            ExpenseRow('E2',date(2026,11,1),'Software',Decimal('100'))])
        self.assertEqual([r.expense_id for r in self.preview(batch)['records']], ['E2'])
        self.reverse_batch(batch)
        self.assertEqual(list(Expense.objects.filter(is_active=True).values_list('expense_id', flat=True)), ['E1'])

    def test_management_page_both_datasets_and_get_preview_never_write(self):
        sale = self.upload('sales'); expense = self.upload()
        self.org.refresh_from_db(); revision = self.org.data_revision
        response = self.client.get(reverse('manage_imports'))
        self.assertContains(response, 'synthetic-sales.csv'); self.assertContains(response, 'synthetic-expenses.csv')
        for batch, dataset in [(sale,'sales'),(expense,'expenses')]:
            page = self.client.get(self.url(batch,dataset))
            self.assertContains(page, 'Exact records created by this import')
            self.assertContains(page, 'E1')
            self.assertEqual(len(page.context['records']), 1)
            batch.refresh_from_db(); self.assertIsNone(batch.reversed_at)
        self.org.refresh_from_db(); self.assertEqual(self.org.data_revision, revision)
        self.assertTrue(Sale.objects.get().is_active); self.assertTrue(Expense.objects.get().is_active)

    def test_owner_scope_forged_ids_and_dataset(self):
        foreign = self.upload(organization=self.other_org, user=self.other)
        self.assertNotContains(self.client.get(reverse('manage_imports')), 'synthetic-expenses.csv')
        for method in [self.client.get, self.client.post]:
            self.assertEqual(method(self.url(foreign), {'reason':'forged', 'confirm':'on', 'preview_token':'forged'}).status_code, 404)
            self.assertEqual(method('/imports/expenses/999999/').status_code, 404)
            self.assertEqual(method('/imports/unknown/1/').status_code, 404)
        with self.assertRaises(Http404):
            preview_import(organization=self.other_org, user=self.user, dataset='expenses', batch_id=foreign.pk)
        with self.assertRaises(Http404):
            reverse_import(organization=self.other_org, user=self.user, dataset='expenses', batch_id=foreign.pk, token='x',reason='x')
        self.assertTrue(Expense.objects.get().is_active)

    def test_confirmation_reason_csrf_and_login_required(self):
        batch = self.upload(); token = self.preview(batch)['token']
        for fields in [{'confirm':'on'}, {'reason':'x'}, {'reason':' ' ,'confirm':'on'}, {'reason':'x'*1001,'confirm':'on'}]:
            self.assertEqual(self.client.post(self.url(batch), {'preview_token':token, **fields}).status_code, 400)
            self.assertTrue(Expense.objects.get().is_active)
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.url(batch), {'preview_token':token,'confirm':'on','reason':'x'}).status_code, 403)
        self.assertEqual(self.client.delete(self.url(batch)).status_code, 405)
        self.client.logout(); self.assertEqual(self.client.get(self.url(batch)).status_code,302)

    def test_post_success_repeated_confirmation_preserves_first_audit(self):
        batch = self.upload(); token = self.preview(batch)['token']
        payload = {'preview_token':token, 'confirm':'on', 'reason':'First reason', 'organization':self.other_org.pk}
        self.assertRedirects(self.client.post(self.url(batch),payload), self.url(batch))
        batch.refresh_from_db(); stamp = batch.reversed_at
        self.assertRedirects(self.client.post(self.url(batch),{**payload,'reason':'overwrite'}), self.url(batch))
        batch.refresh_from_db(); self.assertEqual(batch.reversed_at,stamp); self.assertEqual(batch.reversal_reason,'First reason')
        page=self.client.get(self.url(batch)); self.assertContains(page,'First reason'); self.assertNotContains(page,'Reverse this import')

    def test_stale_after_other_dataset_import_and_new_confirmation(self):
        batch = self.upload(); token = self.preview(batch)['token']; self.upload('sales')
        response=self.client.post(self.url(batch),{'preview_token':token,'confirm':'on','reason':'x'})
        self.assertContains(response,'Workspace data changed',status_code=409)
        self.assertFalse(response.context['form']['confirm'].value())
        self.assertTrue(Expense.objects.get().is_active)
        fresh=response.context['form'].initial['preview_token']
        self.assertRedirects(self.client.post(self.url(batch),{'preview_token':fresh,'confirm':'on','reason':'x'}),self.url(batch))

    def test_tampered_expired_wrong_batch_dataset_user_and_workspace_tokens(self):
        batch=self.upload(); sale=self.upload('sales'); token=self.preview(batch)['token']
        data=signing.loads(token,salt=TOKEN_SALT)
        cases=[token+'x'] + [signing.dumps({**data, key:value},salt=TOKEN_SALT)
            for key,value in [('batch',999999),('dataset','sales'),('user',self.other.pk),('organization',self.other_org.pk)]]
        for value in cases:
            with self.assertRaises(CSVValidationError): self.reverse_batch(batch,token=value)
        with patch('django.core.signing.time.time', return_value=99999999999):
            with self.assertRaises(CSVValidationError): self.reverse_batch(batch,token=token)
        with self.assertRaises(CSVValidationError): self.reverse_batch(sale,'sales',token)
        self.assertTrue(Expense.objects.get().is_active)

    def test_rollback_if_audit_write_fails(self):
        for dataset, model, batch_model in [('sales',Sale,SalesImport),('expenses',Expense,ExpenseImport)]:
            batch=self.upload(dataset); token=self.preview(batch,dataset)['token']; self.org.refresh_from_db();revision=self.org.data_revision
            with patch.object(batch_model,'save',side_effect=RuntimeError('audit unavailable')):
                with self.assertRaises(RuntimeError):self.reverse_batch(batch,dataset,token)
            self.assertTrue(model.objects.get().is_active)
            batch.refresh_from_db(); self.assertIsNone(batch.reversed_at)
            self.org.refresh_from_db();self.assertEqual(self.org.data_revision,revision)

    def test_partial_unique_constraints_and_inactive_expense_charts(self):
        for dataset,model in [('sales',Sale),('expenses',Expense)]:
            batch=self.upload(dataset); self.reverse_batch(batch,dataset); self.upload(dataset,amount='50')
            active=model.objects.get(is_active=True); active.pk=None
            with self.assertRaises(IntegrityError), transaction.atomic():active.save(force_insert=True)
        report=report_for(self.org)
        self.assertEqual(report['expense_cards'][0]['value'],'CAD 50.00')
        self.assertEqual(report['expense_chart'][0]['amount'],'CAD 50.00')
        self.assertEqual(report['expense_categories'][0]['amount'],'CAD 50.00')

    def test_financial_admin_is_read_only_including_organization(self):
        request=RequestFactory().get('/admin/');request.user=self.user
        for model in [Organization,Sale,SalesImport,Expense,ExpenseImport]:
            model_admin=admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
