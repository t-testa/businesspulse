# Initial migration for BusinessPulse, authored with the first project scaffold.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import tracker.kpis


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name='Organization',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=140)),
                ('currency', models.CharField(choices=[('CAD', 'Canadian dollar'), ('USD', 'US dollar')], default='CAD', max_length=3)),
                ('visible_kpis', models.JSONField(default=tracker.kpis.default_kpis)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='organization', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='SalesImport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('filename', models.CharField(max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('inserted_count', models.PositiveIntegerField(default=0)),
                ('duplicate_count', models.PositiveIntegerField(default=0)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='imports', to='tracker.organization')),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='Sale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transaction_id', models.CharField(max_length=100)),
                ('sale_date', models.DateField()),
                ('customer_id', models.CharField(max_length=100)),
                ('revenue', models.DecimalField(decimal_places=2, max_digits=14)),
                ('cost', models.DecimalField(decimal_places=2, max_digits=14)),
                ('import_batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sales', to='tracker.salesimport')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales', to='tracker.organization')),
            ],
            options={'indexes': [models.Index(fields=['organization', 'sale_date'], name='sales_org_date_idx')]},
        ),
        migrations.AddConstraint(
            model_name='sale', constraint=models.UniqueConstraint(fields=('organization', 'transaction_id'), name='unique_org_transaction')
        ),
        migrations.AddConstraint(
            model_name='sale', constraint=models.CheckConstraint(condition=models.Q(('revenue__gte', 0)), name='sale_revenue_nonnegative')
        ),
        migrations.AddConstraint(
            model_name='sale', constraint=models.CheckConstraint(condition=models.Q(('cost__gte', 0)), name='sale_cost_nonnegative')
        ),
    ]
