"""Ingest / merge master lead files into a LeadWorkspace."""

from __future__ import annotations

import re
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction

from pipeline.services.importer import parse_upload
from pipeline.services.multi_merge import (
    FACILITY_ALIASES,
    _find_col,
    _norm_header,
    merge_files,
)

from ..models import LeadRecord, LeadSourceFile, LeadWorkspace
from .filters import REQUIRED_FILTERS, find_filter_column

INTERNAL_COLS = frozenset({
    'row_sources', 'match_key_used', '_merge_source', '_merge_row',
})

# Prefer these for *additional* filter dropdowns when present.
PREFERRED_FILTER_ALIASES = (
    'facility zip code', 'zip', 'postal_code',
    'phone type', 'phone_type', 'city_state',
)


def merge_label_for(src: LeadSourceFile) -> str:
    """
    Unique label for the merge engine. Must differ even when two uploads
    share the same original filename (e.g. ny.csv as Outscraper + BeenVerified).
    """
    kind = src.get_source_kind_display()
    name = src.original_filename or 'file'
    return f'{kind}:{name}#{src.pk}'


def _cell(row, col: str | None) -> str:
    if not col:
        return ''
    v = row.get(col, '')
    if v is None:
        return ''
    return str(v).strip()


def _display_columns(df_columns: list) -> list[str]:
    out = []
    for c in df_columns:
        name = str(c).strip()
        if not name or name in INTERNAL_COLS:
            continue
        if name.startswith('_merge'):
            continue
        out.append(name)
    return out


def _guess_sources(
    row_sources: str,
    fallback: str,
    label_map: dict[str, str] | None = None,
) -> list[str]:
    label_map = label_map or {}
    if not row_sources:
        return [fallback] if fallback else []
    parts = [p.strip() for p in row_sources.split('|') if p.strip()]
    kinds = []
    for p in parts:
        if p in label_map:
            kinds.append(label_map[p])
            continue
        # Labels look like "Outscraper:ny.csv#8"
        if ':' in p:
            kinds.append(p.split(':', 1)[0].strip() or 'Other')
            continue
        low = p.lower()
        if 'outscraper' in low:
            kinds.append('Outscraper')
        elif 'beenverif' in low or low.startswith('bv'):
            kinds.append('BeenVerified')
        elif 'dmv' in low:
            kinds.append('DMV')
        else:
            stem = Path(p).stem if p else 'Other'
            kinds.append(stem[:40] or 'Other')
    seen = set()
    out = []
    for k in kinds:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out or ([fallback] if fallback else ['Other'])


def _public_id(workspace_id: int, seq: int, facility: str) -> str:
    if facility:
        safe = re.sub(r'[^A-Za-z0-9]+', '', facility)[:12]
        if safe:
            return f'NY-{safe}'
    return f'NY-{workspace_id:02d}{seq:05d}'


def _has_contact(data: dict) -> bool:
    for key, val in data.items():
        n = _norm_header(key)
        s = str(val or '').strip()
        if not s:
            continue
        if 'phone' in n or 'mobile' in n or 'tel' in n:
            return True
        if 'email' in n and '@' in s:
            return True
    return False


def _has_owner(data: dict) -> bool:
    for key, val in data.items():
        n = _norm_header(key)
        if 'owner' in n and str(val or '').strip():
            return True
    return False


def _derive_status(data: dict, sources: list[str]) -> str:
    if not _has_owner(data):
        return LeadRecord.Status.NEEDS_OWNER
    if _has_contact(data) or len(sources) > 1:
        return LeadRecord.Status.RESEARCHED
    return LeadRecord.Status.NEEDS_OWNER


def _build_filter_fields(columns: list[str], row_dicts: list[dict]) -> list[dict]:
    """
    Build dropdown filters from actual file columns.
    Always pin Industry / Sub industry / County / State first (required on every campaign).
    Then add a few additional categorical columns.
    """
    if not columns:
        # Still emit required shells so UI always has the four dropdowns after rebuild.
        return [
            {
                'column': '',
                'values': list(spec.get('dummy_values') or []),
                'pinned': spec['key'],
                'label': spec['label'],
            }
            for spec in REQUIRED_FILTERS
        ]

    distinct_map: dict[str, set[str]] = {c: set() for c in columns}
    for row in row_dicts:
        for c in columns:
            v = str(row.get(c, '') or '').strip()
            if v:
                distinct_map[c].add(v)

    result: list[dict] = []
    used: set[str] = set()

    for spec in REQUIRED_FILTERS:
        col = find_filter_column(
            columns,
            spec['aliases'],
            contain_tokens=tuple(spec.get('contain_tokens') or ()),
            exclude=used,
        )
        if col:
            values = sorted(v for v in distinct_map.get(col, set()) if v)[:200]
            result.append({
                'column': col,
                'values': values,
                'pinned': spec['key'],
                'label': spec['label'],
            })
            used.add(col)
        else:
            result.append({
                'column': '',
                'values': list(spec.get('dummy_values') or []),
                'pinned': spec['key'],
                'label': spec['label'],
            })

    preferred_norm = {_norm_header(a) for a in PREFERRED_FILTER_ALIASES}
    skip_tokens = (
        'url', 'error', 'email', 'phone', 'mobile', 'detail', 'raw', 'html',
        'message', 'password', 'token',
    )
    scored: list[tuple[int, str, list[str]]] = []
    for c in columns:
        if c in used:
            continue
        nh = _norm_header(c)
        if any(tok in nh for tok in skip_tokens) and 'phone type' not in nh:
            continue
        values = sorted(distinct_map[c])
        n = len(values)
        if n < 2 or n > 80:
            continue
        fill_ratio = n / max(len(row_dicts), 1)
        if fill_ratio > 0.85 and n > 40:
            continue
        prefer = 0 if nh in preferred_norm else 1
        if any(k in nh for k in ('city', 'zip', 'type', 'age')):
            prefer = 0
        scored.append((prefer, c, values[:80]))

    scored.sort(key=lambda t: (t[0], t[1].lower()))
    for _, c, vals in scored[:6]:
        result.append({'column': c, 'values': vals})
    return result


def dataframe_to_records(
    workspace: LeadWorkspace,
    df,
    *,
    display_columns: list[str],
    default_source: str,
    label_map: dict[str, str] | None = None,
    process_snapshot: dict | None = None,
) -> tuple[list[LeadRecord], list[dict]]:
    cols = [str(c) for c in df.columns]
    fac_c = _find_col(cols, FACILITY_ALIASES)
    sources_c = 'row_sources' if 'row_sources' in cols else None
    match_c = 'match_key_used' if 'match_key_used' in cols else None
    snap = process_snapshot or {}

    records: list[LeadRecord] = []
    row_dicts: list[dict] = []
    used_ids: set[str] = set()

    for seq, (_, row) in enumerate(df.iterrows(), start=1):
        data = {c: _cell(row, c) for c in display_columns}
        row_dicts.append(data)

        facility = _cell(row, fac_c)
        row_sources = _cell(row, sources_c) if sources_c else ''
        sources = _guess_sources(row_sources, default_source, label_map)
        pid = _public_id(workspace.pk, seq, facility)
        if pid in used_ids:
            pid = f'{pid}-{seq}'
        used_ids.add(pid)

        search_parts = [pid] + [v for v in data.values() if v]
        search_text = ' '.join(search_parts).lower()

        is_enriched = _has_contact(data) or len(sources) > 1
        status = _derive_status(data, sources)

        # Preserve destination statuses across rematch; brand-new rows stay pending.
        prev = snap.get(f'id:{pid}') or (
            snap.get(f'fac:{facility.upper()}') if facility else None
        )
        dest_kwargs = {}
        if isinstance(prev, dict):
            for key, field, _label in LeadRecord.DESTINATION_FIELDS:
                dest_kwargs[field] = prev.get(key, LeadRecord.ProcessStatus.PENDING)
        elif prev == LeadRecord.ProcessStatus.PROCEEDED:
            for _key, field, _label in LeadRecord.DESTINATION_FIELDS:
                dest_kwargs[field] = LeadRecord.ProcessStatus.PROCEEDED
        else:
            for _key, field, _label in LeadRecord.DESTINATION_FIELDS:
                dest_kwargs[field] = LeadRecord.ProcessStatus.PENDING

        rec = LeadRecord(
            workspace=workspace,
            public_id=pid,
            data=data,
            search_text=search_text,
            sources=sources,
            status=status,
            is_enriched=is_enriched,
            match_key_used=_cell(row, match_c)[:64] if match_c else '',
            **dest_kwargs,
        )
        rec.sync_overall_process_status()
        records.append(rec)
    return records, row_dicts


def _snapshot_process_status(workspace: LeadWorkspace) -> dict:
    """Map destination statuses by public_id and facility # before rebuild."""
    snap: dict = {}
    fac_col = _find_col(workspace.columns or [], FACILITY_ALIASES)
    only_fields = [
        'public_id', 'data', 'process_status',
        *[f for _k, f, _l in LeadRecord.DESTINATION_FIELDS],
    ]
    for r in workspace.records.only(*only_fields).iterator(chunk_size=2000):
        payload = r.destination_statuses()
        snap[f'id:{r.public_id}'] = payload
        if fac_col:
            fac = r.cell(fac_col)
            if fac:
                key = f'fac:{fac.upper()}'
                # Prefer keeping proceeded destinations if collision
                existing = snap.get(key)
                if not existing:
                    snap[key] = payload
                else:
                    merged = dict(existing)
                    for k, v in payload.items():
                        if v == LeadRecord.ProcessStatus.PROCEEDED:
                            merged[k] = v
                    snap[key] = merged
    return snap


@transaction.atomic
def rebuild_workspace_from_sources(workspace: LeadWorkspace) -> dict:
    sources = list(workspace.source_files.order_by('sort_order', 'pk'))
    if not sources:
        raise ValueError('No source files on this workspace.')

    process_snapshot = _snapshot_process_status(workspace)

    labeled = [(merge_label_for(s), s.file.path) for s in sources]
    result = merge_files(labeled)

    master_name = f'master_{workspace.pk}.csv'
    workspace.master_file.save(master_name, ContentFile(result.csv_bytes), save=False)

    display_columns = _display_columns(list(result.dataframe.columns))
    default_source = sources[0].get_source_kind_display()
    label_map = {merge_label_for(s): s.get_source_kind_display() for s in sources}
    records, row_dicts = dataframe_to_records(
        workspace,
        result.dataframe,
        display_columns=display_columns,
        default_source=default_source,
        label_map=label_map,
        process_snapshot=process_snapshot,
    )

    workspace.records.all().delete()
    LeadRecord.objects.bulk_create(records, batch_size=1500)

    workspace.columns = display_columns
    workspace.filter_fields = _build_filter_fields(display_columns, row_dicts)
    workspace.row_count = len(records)
    workspace.enriched_count = sum(1 for r in records if r.is_enriched)
    workspace.pending_count = sum(
        1 for r in records if r.process_status == LeadRecord.ProcessStatus.PENDING
    )
    workspace.proceeded_count = sum(
        1 for r in records if r.process_status == LeadRecord.ProcessStatus.PROCEEDED
    )
    workspace.in_campaign_count = sum(
        1 for r in records if r.status == LeadRecord.Status.IN_CAMPAIGN
    )
    workspace.last_merge_report = result.report
    workspace.save()
    return result.report


@transaction.atomic
def create_workspace_with_master(
    *,
    name: str,
    uploaded_file,
    source_kind: str = LeadSourceFile.SourceKind.DMV,
) -> LeadWorkspace:
    workspace = LeadWorkspace(name=name.strip())
    workspace.save()

    fname = uploaded_file.name or 'master.csv'
    src = LeadSourceFile(
        workspace=workspace,
        original_filename=fname,
        source_kind=source_kind,
        sort_order=0,
    )
    src.file.save(fname, uploaded_file, save=False)
    src.save()
    try:
        parsed = parse_upload(src.file.path)
        src.row_count = parsed['row_count']
        src.save(update_fields=['row_count'])
    except Exception:
        pass

    rebuild_workspace_from_sources(workspace)
    return workspace


@transaction.atomic
def append_source_and_merge(
    workspace: LeadWorkspace,
    uploaded_file,
    *,
    source_kind: str = LeadSourceFile.SourceKind.OTHER,
) -> dict:
    max_order = (
        workspace.source_files.order_by('-sort_order')
        .values_list('sort_order', flat=True)
        .first()
    )
    next_order = 0 if max_order is None else max_order + 1
    fname = uploaded_file.name or f'source_{next_order + 1}.csv'
    src = LeadSourceFile(
        workspace=workspace,
        original_filename=fname,
        source_kind=source_kind,
        sort_order=next_order,
    )
    src.file.save(fname, uploaded_file, save=False)
    src.save()
    try:
        parsed = parse_upload(src.file.path)
        src.row_count = parsed['row_count']
        src.save(update_fields=['row_count'])
    except Exception:
        pass

    return rebuild_workspace_from_sources(workspace)
