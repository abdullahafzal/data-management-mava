# Generated manually — copy overall process_status onto destination fields

from django.db import migrations


def backfill_destinations(apps, schema_editor):
    LeadRecord = apps.get_model('dashboard', 'LeadRecord')
    fields = (
        'status_millionverifier',
        'status_smartlead',
        'status_xverify',
        'status_simpletexting',
    )
    qs = LeadRecord.objects.filter(process_status='proceeded')
    batch = []
    for r in qs.iterator(chunk_size=1000):
        # Skip rows that already have destination data set independently
        if any(getattr(r, f) == 'proceeded' for f in fields):
            continue
        for f in fields:
            setattr(r, f, 'proceeded')
        batch.append(r)
        if len(batch) >= 500:
            LeadRecord.objects.bulk_update(batch, list(fields))
            batch = []
    if batch:
        LeadRecord.objects.bulk_update(batch, list(fields))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0005_destination_statuses'),
    ]

    operations = [
        migrations.RunPython(backfill_destinations, noop),
    ]
