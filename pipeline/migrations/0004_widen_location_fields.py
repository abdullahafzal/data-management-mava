from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0003_campaign_processing_mode'),
    ]

    operations = [
        migrations.AlterField(
            model_name='campaign',
            name='location',
            field=models.CharField(blank=True, max_length=512),
        ),
        migrations.AlterField(
            model_name='dataimport',
            name='outscraper_location',
            field=models.CharField(
                blank=True,
                help_text='Location filter (country + regions or custom text)',
                max_length=512,
            ),
        ),
    ]
