from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0004_widen_location_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='SavedCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('name_key', models.CharField(db_index=True, max_length=255, unique=True)),
                ('use_count', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name_plural': 'saved categories',
                'ordering': ['-use_count', 'name'],
            },
        ),
        migrations.CreateModel(
            name='SavedLocationSuggestion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('country', models.CharField(db_index=True, default='US', max_length=8)),
                ('label', models.CharField(max_length=255)),
                ('code', models.CharField(blank=True, max_length=64)),
                ('is_custom', models.BooleanField(default=False)),
                ('use_count', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name_plural': 'saved location suggestions',
                'ordering': ['-use_count', 'label'],
                'unique_together': {('country', 'label')},
            },
        ),
    ]
