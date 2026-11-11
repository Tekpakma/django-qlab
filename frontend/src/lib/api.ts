export interface BootstrapSettings {
  theme: 'dark' | 'light'
  default_page_size: number
  last_active_tab: string
  active_docs_key: string
  active_settings_key: string
  ui_state: Record<string, unknown>
}

export interface BootstrapModel {
  app_label: string
  model_name: string
  verbose_name: string
  verbose_name_plural: string
  count?: number
}

export interface SavedQuery {
  id: number
  name: string
  description: string
  app_label: string
  model_name: string
  query_payload: Record<string, unknown>
  tags: string[]
  is_shared: boolean
  last_run_at: string | null
  created_at: string
  updated_at: string
}

export interface QueryHistoryItem {
  id: number
  title: string
  app_label: string
  model_name: string
  query_payload: Record<string, unknown>
  status: 'success' | 'failed' | 'draft'
  duration_ms: number | null
  result_count: number | null
  error_message: string
  saved_query: number | null
  saved_query_name: string
  created_at: string
}

export interface BootstrapResponse {
  user: {
    id: number
    username: string
    is_authenticated: boolean
  }
  settings: BootstrapSettings
  config: {
    environment: string
    metadata: {
      relation_depth: number
      include_reverse_relations: boolean
    }
  }
  models: BootstrapModel[]
  saved_queries: SavedQuery[]
  history: QueryHistoryItem[]
}

export interface MetadataField {
  name: string
  type: string
  label: string
  required: boolean
  primary_key?: boolean
  unique?: boolean
  allowed_operations: string[]
  related_model?: string | null
  related_app_label?: string | null
  filter_name?: string | null
  max_length?: number | null
  choices?: Array<{ value: string; label: string }> | null
}

export interface MetadataResponse {
  model_name: string
  app_label: string
  primary_key_field: string
  fields: MetadataField[]
  all_lookups: string[]
}

interface MetadataRequestOptions {
  relationDepth?: number
  includeReverseRelations?: boolean
}

export interface QueryCondition {
  field: string
  op: string
  value: string | string[]
}

export interface QueryFilterGroup {
  and_operation?: Array<QueryCondition | QueryFilterGroup>
  or_operation?: Array<QueryCondition | QueryFilterGroup>
  not_operation?: Array<QueryCondition | QueryFilterGroup>
}

export type AggregationFunction = 'count' | 'sum' | 'avg' | 'min' | 'max'

export interface AggregationSpec {
  /** Optional for count: without a field the rows of each group are counted. */
  field?: string
  function: AggregationFunction
  alias?: string
  distinct?: boolean
}

export interface QueryRequest {
  model: string
  app_label?: string
  select_fields: string[]
  filter_fields?: QueryFilterGroup
  aggregations?: AggregationSpec[]
  /** Filter on aggregation aliases (SQL HAVING). Requires aggregations. */
  having?: QueryFilterGroup
  order_by?: string[]
  count?: 'exact' | 'estimate' | 'none'
  page?: number
  page_size?: number
  title?: string
  saved_query_id?: number
}

export interface QueryResponse {
  /** null when the count was skipped (count mode "none"). */
  count: number | null
  count_exact?: boolean
  page: number
  page_size: number
  /** null when the total is unknown. */
  total_pages: number | null
  next: number | null
  previous: number | null
  results: Array<Record<string, unknown>>
}

const trimTrailingSlash = (value: string) => value.replace(/\/+$/, '')
const envBase =
  (import.meta as ImportMeta & { env?: Record<string, string> }).env?.VITE_QLAB_API_BASE
const windowBase =
  typeof window !== 'undefined'
    ? (window as Window & { __QLAB_API_BASE__?: string }).__QLAB_API_BASE__
    : undefined

export const API_BASE = trimTrailingSlash(
  windowBase || envBase || '/qlab/api',
)

function getCsrfToken(): string {
  if (typeof window !== 'undefined') {
    return (window as Window & { __CSRF_TOKEN__?: string }).__CSRF_TOKEN__ || ''
  }
  return ''
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const csrfToken = getCsrfToken()
  return fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
      ...(init?.headers || {}),
    },
  })
}

function errorMessage(response: Response, payload: any): string {
  const firstFieldError =
    payload && typeof payload === 'object' && !Array.isArray(payload)
      ? Object.values(payload as Record<string, unknown>).find(
          (value): value is string[] =>
            Array.isArray(value) && typeof value[0] === 'string',
        )?.[0]
      : undefined
  return (
    payload?.detail ||
    payload?.errors?.[0]?.msg ||
    firstFieldError ||
    `Request failed with status ${response.status}`
  )
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)

  const contentType = response.headers.get('content-type') || ''
  const payload = contentType.includes('application/json')
    ? await response.json()
    : null

  if (!response.ok) {
    throw new Error(errorMessage(response, payload))
  }

  return payload as T
}

export function modelKey(appLabel: string, modelName: string): string {
  return `${appLabel}::${modelName}`
}

export function parseModelKey(key: string): { appLabel: string; modelName: string } {
  const idx = key.indexOf('::')
  if (idx === -1) return { appLabel: '', modelName: key }
  return { appLabel: key.slice(0, idx), modelName: key.slice(idx + 2) }
}

export function getBootstrap() {
  return requestJson<BootstrapResponse>('/bootstrap/')
}

export function getModelMetadata(
  model: string,
  appLabel?: string,
  options?: MetadataRequestOptions,
) {
  return requestJson<MetadataResponse>('/metadata/', {
    method: 'POST',
    body: JSON.stringify({
      model,
      ...(appLabel ? { app_label: appLabel } : {}),
      ...(typeof options?.relationDepth === 'number'
        ? { relation_depth: options.relationDepth }
        : {}),
      ...(typeof options?.includeReverseRelations === 'boolean'
        ? { include_reverse_relations: options.includeReverseRelations }
        : {}),
    }),
  })
}

export function runQuery(payload: QueryRequest) {
  return requestJson<QueryResponse>('/query/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export type ExportFormat = 'csv' | 'json'

export interface ExportResult {
  blob: Blob
  filename: string
  totalCount: number | null
  exportedCount: number | null
  truncated: boolean
}

/** Export every row of a query (all pages), streamed by the backend. */
export async function exportQuery(
  payload: QueryRequest,
  format: ExportFormat = 'csv',
): Promise<ExportResult> {
  const { page: _page, page_size: _pageSize, ...rest } = payload
  const response = await request('/query/export/', {
    method: 'POST',
    body: JSON.stringify({ ...rest, format }),
  })
  if (!response.ok) {
    const contentType = response.headers.get('content-type') || ''
    const errorPayload = contentType.includes('application/json')
      ? await response.json()
      : null
    throw new Error(errorMessage(response, errorPayload))
  }
  const disposition = response.headers.get('content-disposition') || ''
  const filename =
    /filename="?([^";]+)"?/.exec(disposition)?.[1] ||
    `${payload.model.toLowerCase()}-export.${format}`
  const toNumber = (value: string | null) =>
    value === null || value === '' ? null : Number(value)
  return {
    blob: await response.blob(),
    filename,
    totalCount: toNumber(response.headers.get('x-qlab-total-count')),
    exportedCount: toNumber(response.headers.get('x-qlab-exported-count')),
    truncated: response.headers.get('x-qlab-truncated') === 'true',
  }
}

export interface SharedQueryLink {
  token: string
  app_label: string
  model_name: string
  query_payload: QueryRequest
  created_at: string
}

export function createShare(payload: {
  app_label?: string
  model_name: string
  query_payload: QueryRequest
}) {
  return requestJson<SharedQueryLink>('/shares/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getShare(token: string) {
  return requestJson<SharedQueryLink>(`/shares/${encodeURIComponent(token)}/`)
}

export function getHistory(limit = 50) {
  return requestJson<QueryHistoryItem[]>(`/history/?limit=${limit}`)
}

export function patchSettings(payload: Partial<BootstrapSettings>) {
  return requestJson<BootstrapSettings>('/settings/', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function getSavedQueries() {
  return requestJson<SavedQuery[]>('/saved-queries/')
}

export function createSavedQuery(payload: Omit<SavedQuery, 'id' | 'last_run_at' | 'created_at' | 'updated_at'>) {
  return requestJson<SavedQuery>('/saved-queries/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateSavedQuery(id: number, payload: Partial<Omit<SavedQuery, 'id' | 'last_run_at' | 'created_at' | 'updated_at'>>) {
  return requestJson<SavedQuery>(`/saved-queries/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteSavedQuery(id: number) {
  return requestJson<void>(`/saved-queries/${id}/`, {
    method: 'DELETE',
  })
}

export function markSavedQueryRun(id: number) {
  return requestJson<QueryResponse>(`/saved-queries/${id}/run/`, {
    method: 'POST',
  })
}
