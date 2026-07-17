from urllib.parse import urlencode

from django.db import connection
from django.db.models import CharField, QuerySet
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from pipeline.services.multi_merge import _find_col, _norm_header

from ..models import LeadRecord, LeadWorkspace

# Dummy industry values when the master file has no Industry column.
DUMMY_INDUSTRY_VALUES = ['Automotive']

# Always shown on every campaign. Matched to file columns by alias.
REQUIRED_FILTERS = (
    {
        'key': 'industry',
        'label': 'Industry',
        'aliases': (
            'industry', 'facility industry', 'industries', 'facility_industry',
        ),
        'dummy_values': DUMMY_INDUSTRY_VALUES,
    },
    {
        'key': 'sub_industry',
        'label': 'Sub industry',
        'aliases': (
            'sub industry', 'sub-industry', 'sub_industry', 'sub category',
            'subcategory', 'business type', 'business_type', 'biz type',
            'category',
        ),
    },
    {
        'key': 'state',
        'label': 'State',
        'aliases': (
            'facility state', 'state', 'facility_state', 'search_state',
            'src_facility state',
        ),
    },
    {
        'key': 'county',
        'label': 'County',
        'aliases': (
            'facility county', 'county', 'facility_county', 'src_facility county',
        ),
    },
    {
        'key': 'city',
        'label': 'City',
        'aliases': (
            'facility city', 'city', 'facility_city', 'src_facility city',
        ),
    },
)

REQUIRED_KEYS = frozenset(spec['key'] for spec in REQUIRED_FILTERS)


def _filter_by_source(qs: QuerySet, source: str) -> QuerySet:
    """
    Match a value inside JSONField list `sources`.
    PostgreSQL supports __contains; SQLite does not — use text match there.
    """
    if connection.vendor == 'postgresql':
        return qs.filter(sources__contains=[source])
    safe = source.replace('"', '')
    return qs.annotate(
        _sources_txt=Cast('sources', CharField(max_length=2048)),
    ).filter(_sources_txt__icontains=f'"{safe}"')


def _values_for_column(workspace: LeadWorkspace, column: str, limit: int = 200) -> list[str]:
    """Distinct non-empty values for a data column (from stored filter_fields or records)."""
    for ff in workspace.filter_fields or []:
        if ff.get('column') == column and ff.get('values') is not None:
            return list(ff.get('values') or [])[:limit]

    seen: set[str] = set()
    out: list[str] = []
    for data in workspace.records.values_list('data', flat=True).iterator(chunk_size=2000):
        v = str((data or {}).get(column, '') or '').strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
        if len(out) >= limit:
            break
    return sorted(out)


def resolve_required_filters(workspace: LeadWorkspace, params) -> list[dict]:
    """Always return Industry / Sub industry / County / State for the UI."""
    columns = list(workspace.columns or [])
    # Prefer exact pinned entries from filter_fields when present.
    pinned_by_key = {
        ff.get('pinned'): ff
        for ff in (workspace.filter_fields or [])
        if ff.get('pinned') in REQUIRED_KEYS
    }

    result = []
    for spec in REQUIRED_FILTERS:
        key = spec['key']
        pinned = pinned_by_key.get(key)
        data_column = None
        values: list[str] = []

        if pinned and pinned.get('column'):
            data_column = pinned['column']
            values = list(pinned.get('values') or [])
        else:
            data_column = _find_col(columns, spec['aliases'])
            if data_column:
                values = _values_for_column(workspace, data_column)

        if not values and spec.get('dummy_values'):
            values = list(spec['dummy_values'])
            data_column = data_column  # may still be None → UI-only dummy

        selected = (params.get(key) or '').strip()
        result.append({
            'key': key,
            'param': key,
            'label': spec['label'],
            'column': data_column or spec['label'],
            'data_column': data_column,
            'values': values,
            'selected': selected,
            'required': True,
            'is_dummy': data_column is None and bool(spec.get('dummy_values')),
            'missing_column': data_column is None and not spec.get('dummy_values'),
        })
    return result


def resolve_additional_filters(workspace: LeadWorkspace, params) -> list[dict]:
    """Extra categorical filters beyond the four required campaign filters."""
    required_cols = set()
    for ff in workspace.filter_fields or []:
        if ff.get('pinned') in REQUIRED_KEYS and ff.get('column'):
            required_cols.add(ff['column'])
    # Also exclude columns that match required aliases even if not pinned yet
    columns = list(workspace.columns or [])
    for spec in REQUIRED_FILTERS:
        col = _find_col(columns, spec['aliases'])
        if col:
            required_cols.add(col)

    extra = []
    idx = 0
    for ff in workspace.filter_fields or []:
        if ff.get('pinned') in REQUIRED_KEYS:
            continue
        col = ff.get('column') or ''
        if not col or col in required_cols:
            continue
        # Skip merge-suffix duplicates of required cols
        base = col.split('__', 1)[0]
        if any(_norm_header(base) == _norm_header(rc.split('__', 1)[0]) for rc in required_cols):
            # keep if different enough — actually skip exact alias matches of required
            nh = _norm_header(base)
            skip = False
            for spec in REQUIRED_FILTERS:
                if nh in {_norm_header(a) for a in spec['aliases']}:
                    skip = True
                    break
            if skip:
                continue

        val = (params.get(f'f{idx}') or '').strip()
        extra.append({
            'index': idx,
            'param': f'f{idx}',
            'label': col,
            'column': col,
            'data_column': col,
            'values': ff.get('values') or [],
            'selected': val,
            'required': False,
            'is_dummy': False,
            'missing_column': False,
            'pinned': ff.get('pinned') or '',
        })
        idx += 1
    return extra


def apply_filters(qs: QuerySet, params, workspace: LeadWorkspace) -> QuerySet:
    q = (params.get('q') or '').strip()
    source = (params.get('source') or '').strip()
    status = (params.get('status') or '').strip()
    process = (params.get('process') or '').strip()

    if q:
        qs = qs.filter(search_text__icontains=q.lower())

    if status:
        qs = qs.filter(status=status)

    if process:
        qs = qs.filter(process_status=process)

    if source:
        qs = _filter_by_source(qs, source)

    # Required campaign filters (named params)
    for req in resolve_required_filters(workspace, params):
        val = req['selected']
        col = req.get('data_column')
        if not val or not col:
            # Dummy industry with no column: UI-only, does not restrict
            continue
        alias = f'_req_{req["key"]}'
        qs = qs.annotate(**{alias: KeyTextTransform(col, 'data')}).filter(
            **{f'{alias}__iexact': val}
        )

    # Additional filters (f0, f1, …) — same order as resolve_additional_filters
    for ff in resolve_additional_filters(workspace, params):
        val = ff['selected']
        if not val:
            continue
        col = ff.get('data_column')
        if not col:
            continue
        alias = f'_f{ff["index"]}'
        qs = qs.annotate(**{alias: KeyTextTransform(col, 'data')}).filter(
            **{f'{alias}__iexact': val}
        )
    return qs


def filter_ui_context(workspace: LeadWorkspace, params) -> dict:
    source_counts: dict[str, int] = {}
    for sources in workspace.records.values_list('sources', flat=True).iterator(chunk_size=2000):
        for s in (sources or []):
            source_counts[s] = source_counts.get(s, 0) + 1

    active = []
    q = (params.get('q') or '').strip()
    if q:
        active.append({'key': 'q', 'label': 'Search', 'value': q})

    process = (params.get('process') or '').strip()
    required_filters = resolve_required_filters(workspace, params)
    additional_filters = resolve_additional_filters(workspace, params)

    selected = {
        'q': q,
        'process': process,
        'source': (params.get('source') or '').strip(),
        'status': (params.get('status') or '').strip(),
    }

    for req in required_filters:
        selected[req['param']] = req['selected']
        if req['selected']:
            active.append({
                'key': req['param'],
                'label': req['label'],
                'value': req['selected'],
            })

    for ff in additional_filters:
        selected[ff['param']] = ff['selected']
        if ff['selected']:
            active.append({
                'key': ff['param'],
                'label': ff['label'],
                'value': ff['selected'],
            })

    if process:
        label = dict(LeadRecord.ProcessStatus.choices).get(process, process)
        active.append({'key': 'process', 'label': 'Process', 'value': label})

    src = selected['source']
    if src:
        active.append({'key': 'source', 'label': 'Source', 'value': src})
    st = selected['status']
    if st:
        label = dict(LeadRecord.Status.choices).get(st, st)
        active.append({'key': 'status', 'label': 'Research', 'value': label})

    # Back-compat: flat list (required first) for any older template refs
    column_filters = required_filters + additional_filters

    return {
        'column_filters': column_filters,
        'required_filters': required_filters,
        'additional_filters': additional_filters,
        'sources': sorted(source_counts.keys()),
        'source_counts': source_counts,
        'statuses': LeadRecord.Status.choices,
        'process_statuses': LeadRecord.ProcessStatus.choices,
        'selected': selected,
        'active_filters': active,
    }


def workspace_metrics(workspace: LeadWorkspace, filtered: QuerySet) -> dict:
    pending = workspace.records.filter(
        process_status=LeadRecord.ProcessStatus.PENDING
    ).count()
    proceeded = workspace.records.filter(
        process_status=LeadRecord.ProcessStatus.PROCEEDED
    ).count()
    return {
        'master_records': workspace.row_count,
        'working_set': filtered.count(),
        'enriched': workspace.enriched_count,
        'pending': pending,
        'proceeded': proceeded,
        'in_campaign': workspace.in_campaign_count,
    }


def query_string_from_params(params, *, page: int | None = None) -> str:
    """Build a query string preserving current filters for pagination links."""
    data = []
    for key in params.keys():
        if key == 'page':
            continue
        val = (params.get(key) or '').strip()
        if val:
            data.append((key, val))
    if page is not None:
        data.append(('page', str(page)))
    return urlencode(data)
