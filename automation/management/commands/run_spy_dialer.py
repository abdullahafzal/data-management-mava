import os

from django.core.management.base import BaseCommand

from automation.models import AutomationRun
from automation.services.runner import execute_spy_dialer_run


class Command(BaseCommand):
    help = 'Run Spy Dialer people search (headless Chrome by default).'

    def add_arguments(self, parser):
        parser.add_argument('--job-id', type=int, help='Run an existing AutomationRun by ID')
        parser.add_argument('--input', type=str, help='Input CSV/XLSX path')
        parser.add_argument('--offset', type=int, default=0)
        parser.add_argument('--limit', type=int, default=None)
        parser.add_argument('--city', type=str, default=None)
        parser.add_argument('--headless', action='store_true', default=None)
        parser.add_argument('--no-headless', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if options['job_id']:
            execute_spy_dialer_run(options['job_id'])
            self.stdout.write(self.style.SUCCESS(f'Job {options["job_id"]} finished.'))
            return

        input_path = options['input']
        if not input_path:
            self.stderr.write('Provide --job-id or --input')
            return

        headless = False if options['no_headless'] else (
            True if options['headless'] else None
        )
        if headless is None:
            raw = os.environ.get('AUTOMATION_HEADLESS_CHROME', 'true').lower()
            headless = raw not in ('0', 'false', 'no', 'off')

        with open(input_path, 'rb') as fh:
            from django.core.files import File

            run = AutomationRun(
                job_type=AutomationRun.JobType.SPY_DIALER_PEOPLE,
                headless=headless,
                params={
                    'offset': options['offset'],
                    'limit': options['limit'],
                    'city': (options['city'] or '').strip().upper() or None,
                    'dry_run': options['dry_run'],
                },
            )
            run.input_file.save(os.path.basename(input_path), File(fh), save=False)
            run.save()

        execute_spy_dialer_run(run.pk)
        run.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(f'Run #{run.pk} — {run.get_status_display()}'))
