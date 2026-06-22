import os

from django.core.management.base import BaseCommand

from automation.services.runner import execute_icm_run


class Command(BaseCommand):
    help = 'Run ICM Step 3 personal contact enrichment (Instant Checkmate).'

    def add_arguments(self, parser):
        parser.add_argument('--job-id', type=int, required=True, help='AutomationRun ID')

    def handle(self, *args, **options):
        execute_icm_run(options['job_id'])
        self.stdout.write(self.style.SUCCESS(f'ICM job {options["job_id"]} finished.'))
