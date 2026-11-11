# django-qlab v0.5.0

## Highlights

- **Export all** — download every result row (CSV or JSON), not just the current page
- **HAVING** — filter aggregated groups by their computed values; `group_by` accepted as in issue #7
- **Inline editing** of filter values for saved queries in the Saved tab (issue #4)
- **Short share links** stored server side — large queries can be shared too
- **Sorting** via `order_by`, sorting across all pages from the column headers
- New operators `in`, `not_in`, `between`, `istartswith`, `iendswith`, offered per field type
- Count modes (`exact`, `estimate`, `none`) and a typeahead endpoint (`/api/suggest/`)

## Fixes

- Filters across a to-many relation (reverse FK / M2M) no longer return the same
  object several times; counts and paging are correct
- Grouping by a field other than the primary key returned one row per object
- Sums/averages are no longer multiplied by joins over a second to-many relation
- Wrongly typed filter values (e.g. `id = "h"`) return a clear 400 instead of a 500
- Malformed payloads and misspelled filter keys return 400 instead of 500 or
  being silently ignored
- The model registry is now actually filled on `migrate` (the signal was never
  connected)
- UI assets are cache-busted, so browsers pick up a new UI after an upgrade

## Security

- Relation traversal is checked against the model registry: disabled models and
  restricted models without a matching group can no longer be read through a
  relation (e.g. `team__name`)
- The user model's password hash is never exposed (`EXCLUDED_FIELDS`)
- `MAX_RELATION_DEPTH` now applies to queries, not only to metadata
- Restricted models are hidden from users without access; share links of such
  models can't be resolved by them
- CSV exports neutralise spreadsheet formulas (`=`, `+`, `-`, `@`)

## Upgrade notes

### Required

1. Run migrations — adds the `SharedQuery` table (and `ModelRegistry` if you are
   coming from 0.3.x):

   ```bash
   python manage.py migrate
   python manage.py collectstatic
   ```

2. Check the model registry in the Django admin. `migrate` now registers models
   that were missing as **submitted** (not queryable until enabled). Existing
   entries, their status and groups are left unchanged.

### Required if you scope querysets

If you override `get_queryset()` in a custom ViewSet, point QLab at it:

```python
QLAB_SETTINGS = {
    "QUERY_VIEWSET": "myapp.views.ScopedQLabViewSet",
}
```

Mounting the ViewSet only on `api/query/` (the previous README approach) leaves
the new export endpoint and saved-query runs **unscoped**.

### Behaviour changes to review

| Change | What to do |
|---|---|
| Query run history is pruned to the newest **1000 runs per user** | Set `HISTORY_RETENTION: 0` to keep everything (e.g. for auditing) |
| Field paths deeper than `MAX_RELATION_DEPTH` (default 2) return 400 | Raise `MAX_RELATION_DEPTH` if you rely on deeper paths |
| Reading disabled/restricted models through a relation returns 403 | Enable the model or grant the group |
| `*.password` of the user model is not available | Configure `EXCLUDED_FIELDS` to change the list |
| Aggregation results no longer contain `id` unless it is selected | Select the field explicitly if a client needs it |
| `sum`/`avg`/non-distinct `count` across two different to-many relations return 400 | Use `count` with `distinct`, `min`/`max`, or split the query |
| Filter values are validated against the field type | Fix clients that send e.g. text for number fields |
| Unknown keys in filter groups and invalid `page`/`page_size` return 400 | Fix the client payload |
| Saved queries are validated on save | Invalid stored payloads must be fixed before they can be updated |

### New settings (all optional)

| Setting | Default |
|---|---|
| `MAX_EXPORT_ROWS` | `100000` |
| `MAX_SHARE_PAYLOAD_BYTES` | `65536` |
| `QUERY_VIEWSET` | `"qlab.api_views.QLabFrontendApiViewSet"` |
| `EXCLUDED_FIELDS` | `None` → the user model's `password` |
| `HISTORY_RETENTION` | `1000` |

Old share links (`?filters=…`) keep working.
