from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0005_savedcategory_savedlocationsuggestion'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataimport',
            name='outscraper_advanced',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Advanced Outscraper parameters (quick filters, language, etc.)',
            ),
        ),
    ]
