import re
from urllib.parse import urlencode

from django.db import connection
from django.db.models import CharField, QuerySet
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from pipeline.services.multi_merge import _norm_header

from ..models import LeadRecord, LeadWorkspace

# Dummy industry values when the master file has no Industry column.
DUMMY_INDUSTRY_VALUES = ['Automotive']

# Always shown on every campaign. Matched to file columns by alias / header token.
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
        # Master sheet column: Business Type
        'aliases': (
            'business type', 'business_type', 'biz type',
            'sub industry', 'sub-industry', 'sub_industry', 'sub category',
            'subcategory',
        ),
    },
    {
        'key': 'state',
        'label': 'State',
        'aliases': (
            'facility state', 'state', 'facility_state', 'search_state',
            'src_facility state',
        ),
        'contain_tokens': ('state',),
    },
    {
        'key': 'county',
        'label': 'County',
        # Master sheet column: Facility County
        'aliases': (
            'facility county', 'facility_county', 'county', 'src_facility county',
        ),
        'contain_tokens': ('county',),
    },
    {
        'key': 'city',
        'label': 'City',
        'aliases': (
            'facility city', 'city', 'facility_city', 'src_facility city',
        ),
        # Also match any header containing the word "city".
        'contain_tokens': ('city',),
    },
)

REQUIRED_KEYS = frozenset(spec['key'] for spec in REQUIRED_FILTERS)


def _alias_keys(alias: str) -> set[str]:
    """Normalized forms so 'Business Type' matches business_type / businesstype."""
    n = _norm_header(alias)
    return {
        n,
        n.replace(' ', ''),
        n.replace(' ', '_'),
        n.replace('_', ' '),
        n.replace('-', ' '),
    }


def find_filter_column(
    columns: list[str],
    aliases: tuple[str, ...],
    *,
    contain_tokens: tuple[str, ...] = (),
    exclude: set[str] | None = None,
) -> str | None:
    """
    Resolve a permanent filter to a master-file column.
    1) Exact alias match (space/underscore/collapsed tolerant)
    2) Header contains a whole-word token (e.g. 'state' in 'Facility State')
    """
    exclude = exclude or set()
    available = [c for c in columns if c and c not in exclude]
    if not available:
        return None

    by_key: dict[str, str] = {}
    for col in available:
        for key in _alias_keys(col):
            by_key.setdefault(key, col)

    for alias in aliases:
        for key in _alias_keys(alias):
            if key in by_key:
                return by_key[key]

    for token in contain_tokens:
        t = _norm_header(token)
        if not t:
            continue
        pat = re.compile(rf'(^| ){re.escape(t)}( |$)')
        candidates: list[tuple[int, int, str]] = []
        for col in available:
            n = _norm_header(col)
            if not pat.search(n):
                continue
            if n == t:
                score = 0
            elif n.endswith(f' {t}') or n.startswith(f'{t} '):
                score = 1
            else:
                score = 2
            candidates.append((score, len(n), col))
        if candidates:
            candidates.sort()
            return candidates[0][2]
    return None


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
    """Always return Industry / Sub industry / State / County / City for the UI."""
    columns = list(workspace.columns or [])
    # Prefer exact pinned entries from filter_fields when present.
    pinned_by_key = {
        ff.get('pinned'): ff
        for ff in (workspace.filter_fields or [])
        if ff.get('pinned') in REQUIRED_KEYS
    }

    result = []
    used_cols: set[str] = set()
    for spec in REQUIRED_FILTERS:
        key = spec['key']
        pinned = pinned_by_key.get(key)
        data_column = None
        values: list[str] = []
        contain = tuple(spec.get('contain_tokens') or ())

        pinned_col = (pinned or {}).get('column') or ''
        # Only trust a pin if that column still matches this filter's aliases.
        pin_ok = bool(
            pinned_col
            and pinned_col not in used_cols
            and find_filter_column(
                [pinned_col],
                spec['aliases'],
                contain_tokens=contain,
            ) == pinned_col
        )

        if pin_ok:
            data_column = pinned_col
            values = list(pinned.get('values') or [])
        else:
            data_column = find_filter_column(
                columns,
                spec['aliases'],
                contain_tokens=contain,
                exclude=used_cols,
            )
            if data_column:
                values = _values_for_column(workspace, data_column)

        if not values and spec.get('dummy_values'):
            values = list(spec['dummy_values'])

        if data_column:
            used_cols.add(data_column)

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
        col = find_filter_column(
            columns,
            spec['aliases'],
            contain_tokens=tuple(spec.get('contain_tokens') or ()),
            exclude=required_cols,
        )
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
