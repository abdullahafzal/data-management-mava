"""Automatic campaign pipeline: parse → clean with fixed columns."""

from django.core.files.base import ContentFile

from ..constants import resolve_automatic_columns
from ..models import CleanedDataset, DataImport
from .cleaner import build_cleaned_csv


class AutomaticPipelineError(Exception):
    pass


def run_automatic_pipeline(data_import: DataImport) -> tuple[int, list[str], list[str]]:
    """
    Clean import using automatic column set. Returns
    (row_count, columns_used, columns_missing_from_file).
    """
    if data_import.status != DataImport.Status.PARSED:
        raise AutomaticPipelineError('Import must be parsed before automatic cleaning.')

    selected, missing = resolve_automatic_columns(data_import.columns)
    if not selected:
        raise AutomaticPipelineError(
            'None of the automatic columns were found in this file. '
            'Use manual mode or check the Outscraper export format.'
        )

    csv_bytes, row_count = build_cleaned_csv(
        data_import.original_file.path,
        selected,
    )

    data_import.selected_columns = selected
    data_import.save(update_fields=['selected_columns'])

    cleaned, _ = CleanedDataset.objects.update_or_create(
        data_import=data_import,
        defaults={'row_count': row_count},
    )
    filename = f'cleaned_{data_import.campaign_id}_{data_import.pk}.csv'
    cleaned.file.save(filename, ContentFile(csv_bytes), save=True)

    return row_count, selected, missing
