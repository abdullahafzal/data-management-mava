from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0006_dataimport_outscraper_advanced'),
    ]

    operations = [
        migrations.CreateModel(
            name='PhoneVerificationJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('results_file', models.FileField(blank=True, null=True, upload_to='phone_verification/%Y/%m/')),
                ('total_count', models.PositiveIntegerField(default=0)),
                ('valid_count', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('processing', 'Processing'), ('completed', 'Completed'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('cleaned_dataset', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='phone_verification_job', to='pipeline.cleaneddataset')),
            ],
        ),
    ]
