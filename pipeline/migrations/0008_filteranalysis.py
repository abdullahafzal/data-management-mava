from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0007_phoneverificationjob'),
    ]

    operations = [
        migrations.CreateModel(
            name='FilterAnalysis',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('match_type', models.CharField(blank=True, max_length=16)),
                ('recommendation', models.CharField(blank=True, choices=[('reuse_existing', 'Reuse existing data'), ('scrape_again', 'Scrape again'), ('needs_review', 'Needs review')], max_length=32)),
                ('headline', models.CharField(blank=True, max_length=255)),
                ('summary', models.TextField(blank=True)),
                ('reasoning', models.JSONField(blank=True, default=list)),
                ('warnings', models.JSONField(blank=True, default=list)),
                ('suggested_reuse_import_id', models.PositiveIntegerField(blank=True, null=True)),
                ('confidence', models.CharField(blank=True, max_length=16)),
                ('context_snapshot', models.JSONField(blank=True, default=dict)),
                ('model_name', models.CharField(blank=True, max_length=64)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('completed', 'Completed'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('data_import', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='filter_analyses', to='pipeline.dataimport')),
            ],
            options={
                'verbose_name_plural': 'filter analyses',
                'ordering': ['-created_at'],
            },
        ),
    ]
