"""Export LeadWorkspace records as a full CSV (data + status columns)."""

from __future__ import annotations

import csv
import io

from ..models import LeadRecord, LeadWorkspace


def build_master_export_csv(workspace: LeadWorkspace) -> bytes:
    """
    CSV matching the dashboard table: ID, file columns, Source,
    destination statuses, and overall Process status.
    """
    data_cols = list(workspace.table_columns)
    dest_headers = [label for _key, _field, label in LeadRecord.DESTINATION_FIELDS]
    headers = [
        'ID',
        *data_cols,
        'Source',
        *dest_headers,
        'Process',
        'Research status',
        'Enriched',
    ]

    buf = io.StringIO(newline='')
    writer = csv.writer(buf)
    writer.writerow(headers)

    qs = workspace.records.order_by('id').iterator(chunk_size=2000)
    for r in qs:
        sources = ' | '.join(r.sources or [])
        dest_vals = [
            getattr(r, field)
            for _key, field, _label in LeadRecord.DESTINATION_FIELDS
        ]
        writer.writerow([
            r.public_id,
            *[r.cell(col) for col in data_cols],
            sources,
            *dest_vals,
            r.process_status,
            r.status,
            'yes' if r.is_enriched else 'no',
        ])

    return buf.getvalue().encode('utf-8-sig')
