import { useEffect, useState } from 'react'
import { TopNav, TabId, ThemeMode } from './components/TopNav'
import { ContentSidebar, DocsNavGroup, RecentQuery } from './components/ContentSidebar'
import { HomePage } from './pages/HomePage'
import { QueriesPage } from './pages/QueriesPage'
import {
  DocsEntry,
  DocsPage,
  HistoryItem,
  ModelDetail,
  HistoryPage,
  ModelsPage,
  SavedQueriesPage,
} from './pages/WorkspacePages'
import {
  BootstrapModel,
  BootstrapSettings,
  MetadataField,
  MetadataResponse,
  QueryHistoryItem,
  QueryRequest,
  QueryResponse,
  SavedQuery,
  createSavedQuery,
  createShare,
  deleteSavedQuery,
  exportQuery,
  getBootstrap,
  getHistory,
  getModelMetadata,
  getSavedQueries,
  getShare,
  markSavedQueryRun,
  modelKey,
  patchSettings,
  runQuery,
  updateSavedQuery,
} from './lib/api'
import { setUrlParam } from './lib/url'
import './App.css'


const DOCS_ENTRIES: DocsEntry[] = [
  {
    key: 'overview',
    section: 'Getting Started',
    title: 'Overview',
    tagline: 'What QLab gives you inside a Django project.',
    intro: 'QLab combines dynamic querying, metadata discovery and a packaged UI so teams can inspect model data without hand-writing every query or view.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'run model queries' },
      { method: 'POST', path: '/api/metadata/', description: 'inspect model schema' },
    ],
    params: [
      { name: 'model', type: 'string', description: 'Target model for queries or metadata.' },
      { name: 'select_fields', type: 'array', description: 'Fields returned by query runs.' },
    ],
    code: `pip install django-qlab`,
  },
  {
    key: 'installation',
    section: 'Getting Started',
    title: 'Installation',
    tagline: 'Install the package and expose both API and UI.',
    intro: 'Install from PyPI, add qlab to INSTALLED_APPS, mount qlab.urls, run migrations and collectstatic. No npm or frontend build required — the compiled UI ships with the package.',
    endpoints: [
      { method: 'GET', path: '/qlab/', description: 'bundled UI entrypoint' },
    ],
    params: [
      { name: 'INSTALLED_APPS', type: 'list', description: 'Must include qlab, rest_framework, drf_spectacular and staticfiles.' },
      { name: 'urlpatterns', type: 'list', description: 'Include qlab.urls to mount the UI and all API routes.' },
      { name: 'QLabView', type: 'class', description: 'Optional UI view base for login redirects or custom auth.' },
      { name: 'QLabFrontendApiViewSet', type: 'class', description: 'Optional ViewSet base for per-user queryset scoping.' },
    ],
    code: `pip install django-qlab\n\n# settings.py\nINSTALLED_APPS = [\n    "django.contrib.staticfiles",\n    "rest_framework",\n    "drf_spectacular",\n    "qlab",\n]\n\n# urls.py\nfrom django.urls import include, path\n\nurlpatterns = [\n    path("qlab/", include("qlab.urls")),\n]\n\n# then:\npython manage.py migrate\npython manage.py collectstatic`,
  },
  {
    key: 'quick-start',
    section: 'Getting Started',
    title: 'Quick Start',
    tagline: 'Wire a ViewSet in a few lines and start querying.',
    intro: 'Mix QLab into a DRF ViewSet, expose query and metadata routes and optionally scope the queryset per model.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'query endpoint' },
      { method: 'POST', path: '/api/metadata/', description: 'metadata endpoint' },
      { method: 'POST', path: '/api/neighborhood/', description: 'relation graph endpoint' },
    ],
    params: [
      { name: 'get_queryset', type: 'callable', description: 'Optional scoping hook by model.' },
      { name: 'permission_classes', type: 'list', description: 'Standard DRF permission configuration.' },
    ],
    code: `class QLab(QLabMixin, NeighborhoodMixin, QLabMetadataMixin, viewsets.ViewSet):\n    permission_classes = [IsAuthenticated]\n\n    def get_queryset(self, model):\n        return model.objects.all()`,
  },
  {
    key: 'query-params',
    section: 'API Reference',
    title: 'Query Endpoint',
    tagline: 'Run interactive model queries through the packaged QLab shell.',
    intro: 'Use the query endpoint to select fields, apply nested filters and paginate results. This is the primary runtime surface the builder will target.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'execute a filtered query' },
    ],
    params: [
      { name: 'model', type: 'string', description: 'Target Django model name.' },
      { name: 'select_fields', type: 'array', description: 'Field paths returned in each result row.' },
      { name: 'filter_fields', type: 'object', description: 'AND/OR/NOT filter tree.' },
      { name: 'aggregations', type: 'array', description: 'Aggregate values computed per group of select_fields.' },
    ],
    code: `POST /api/query/\n{\n  "model": "Device",\n  "select_fields": ["id", "name", "status"],\n  "filter_fields": {\n    "and_operation": [\n      { "field": "status", "op": "is", "value": "active" }\n    ]\n  }\n}`,
  },
  {
    key: 'filtering',
    section: 'API Reference',
    title: 'Filtering',
    tagline: 'Build AND, OR and NOT groups across flat or nested field paths.',
    intro: 'QLab supports structured filter groups so the UI can express multiple conditions without string-building raw Django lookups.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'apply filter groups inside query payloads' },
    ],
    params: [
      { name: 'and_operation', type: 'array', description: 'All conditions must match.' },
      { name: 'or_operation', type: 'array', description: 'At least one condition must match.' },
      { name: 'not_operation', type: 'array', description: 'Excluded conditions.' },
    ],
    code: `{\n  "filter_fields": {\n    "and_operation": [\n      { "field": "status", "op": "is", "value": "active" }\n    ],\n    "or_operation": [\n      { "field": "region", "op": "is", "value": "DE" },\n      { "field": "region", "op": "is", "value": "AT" }\n    ]\n  }\n}`,
  },
  {
    key: 'aggregations',
    section: 'API Reference',
    title: 'Aggregations',
    tagline: 'Compute count, sum, avg, min and max per group of select_fields.',
    intro: 'select_fields act as the GROUP BY columns — each aggregation is computed per group and returned as an extra key on every result row. Rows collapse to one per unique combination of select_fields, so aggregating a to-many relation never duplicates rows. sum and avg require a numeric field.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'add aggregations to a query payload' },
    ],
    params: [
      { name: 'field', type: 'string', description: 'Field path to aggregate (e.g. devices or devices__size).' },
      { name: 'function', type: 'string', description: 'One of count, sum, avg, min, max.' },
      { name: 'alias', type: 'string', description: 'Result key; defaults to {function}_{field}.' },
      { name: 'distinct', type: 'boolean', description: 'Count only distinct values (count only).' },
    ],
    code: `POST /api/query/\n{\n  "model": "Team",\n  "select_fields": ["id", "name"],\n  "aggregations": [\n    { "field": "devices", "function": "count" },\n    { "field": "devices__owner", "function": "count",\n      "distinct": true, "alias": "distinct_owners" }\n  ]\n}\n\n// result rows:\n// { "id": 1, "name": "Ops",\n//   "count_devices": 17, "distinct_owners": 3 }`,
  },
  {
    key: 'having',
    section: 'API Reference',
    title: 'Having',
    tagline: 'Filter groups by their aggregated values (SQL HAVING).',
    intro: 'having takes the same AND/OR/NOT tree as filter_fields, but every condition references an aggregation alias instead of a model field. filter_fields still filter the rows before grouping (WHERE). Allowed operators: is, is_not, lt, lte, gt, gte, between, in, not_in.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'add having to an aggregation query' },
    ],
    params: [
      { name: 'having', type: 'object', description: 'Filter tree whose fields are aggregation aliases.' },
    ],
    code: `POST /api/query/\n{\n  "model": "Device",\n  "select_fields": ["status"],\n  "aggregations": [{ "function": "count", "alias": "n" }],\n  "having": {\n    "and_operation": [\n      { "field": "n", "op": "gte", "value": "10" }\n    ]\n  }\n}`,
  },
  {
    key: 'export',
    section: 'API Reference',
    title: 'Export',
    tagline: 'Download every result row, not just one page.',
    intro: 'The export endpoint takes the same payload as /api/query/ (page and page_size are ignored) and streams all rows as CSV or JSON. At most MAX_EXPORT_ROWS rows are written; the X-QLab-Truncated header is "true" when the export was cut off.',
    endpoints: [
      { method: 'POST', path: '/api/query/export/', description: 'stream all rows as csv or json' },
    ],
    params: [
      { name: 'format', type: 'string', description: 'csv (default) or json.' },
    ],
    code: `POST /api/query/export/\n{\n  "model": "Device",\n  "select_fields": ["id", "name"],\n  "format": "csv"\n}`,
  },
  {
    key: 'sharing',
    section: 'API Reference',
    title: 'Share Links',
    tagline: 'Short links that work for queries of any size.',
    intro: 'The Share button stores the query payload server side and copies a short ?share=<token> link. Recipients run the query with their own permissions and queryset scoping.',
    endpoints: [
      { method: 'POST', path: '/api/shares/', description: 'store a payload, returns a token' },
      { method: 'GET', path: '/api/shares/<token>/', description: 'resolve a token' },
    ],
    params: [
      { name: 'model_name', type: 'string', description: 'Model of the shared query.' },
      { name: 'app_label', type: 'string', description: 'App label of the model.' },
      { name: 'query_payload', type: 'object', description: 'The query payload to share.' },
    ],
    code: `POST /api/shares/\n{ "model_name": "Device", "app_label": "inventory",\n  "query_payload": { ... } }\n\n// → { "token": "x7Gq2PZk0aBc", ... }`,
  },
  {
    key: 'pagination',
    section: 'API Reference',
    title: 'Pagination',
    tagline: 'Keep result volumes predictable for both API consumers and the UI.',
    intro: 'Query responses return count and paging metadata so the frontend can switch pages without losing context.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'paginated result response' },
    ],
    params: [
      { name: 'page', type: 'integer', description: 'Requested result page.' },
      { name: 'page_size', type: 'integer', description: 'Requested page size within allowed limits.' },
    ],
    code: `{\n  "count": 250,\n  "page": 1,\n  "page_size": 100,\n  "total_pages": 3,\n  "next": 2,\n  "previous": null\n}`,
  },
  {
    key: 'permissions',
    section: 'API Reference',
    title: 'Permissions',
    tagline: 'QLab follows the permissions and scoping rules of the host project.',
    intro: 'The package does not replace your authorization model. Apply DRF permission classes and per-model scoping in the hosting ViewSet.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'protected by your DRF permissions' },
    ],
    params: [
      { name: 'permission_classes', type: 'list', description: 'DRF permission classes on the ViewSet.' },
    ],
    code: `class QLab(QLabMixin, viewsets.ViewSet):\n    permission_classes = [IsAuthenticated]\n\n    def get_queryset(self, model):\n        return model.objects.filter(tenant=self.request.user.tenant)`,
  },
  {
    key: 'metadata-endpoint',
    section: 'API Reference',
    title: 'Metadata Endpoint',
    tagline: 'Populate field choices, relations and allowed operators before query execution.',
    intro: 'The metadata endpoint is the schema feed for the frontend. It enables autocomplete, field grouping and relation traversal without hardcoded model knowledge.',
    endpoints: [
      { method: 'POST', path: '/api/metadata/', description: 'return schema metadata for one model' },
      { method: 'GET', path: '/api/metadata/', description: 'optionally expose a lightweight index' },
    ],
    params: [
      { name: 'model', type: 'string', description: 'Model to inspect.' },
      { name: 'include_relations', type: 'boolean', description: 'Include related models and nested lookups.' },
    ],
    code: `POST /api/metadata/\n{\n  "model": "Device",\n  "include_relations": true\n}`,
  },
  {
    key: 'qlab-mixin',
    section: 'Guides',
    title: 'QLabMixin',
    tagline: 'Core query execution mixin for field selection and filter validation.',
    intro: 'Use QLabMixin when you only need dynamic querying. It validates incoming field paths and builds queryset annotations safely.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'query execution route' },
    ],
    params: [
      { name: 'post', type: 'action', description: 'Main query action on the ViewSet.' },
    ],
    code: `class QLab(QLabMixin, viewsets.ViewSet):\n    def get_queryset(self, model):\n        return model.objects.all()`,
  },
  {
    key: 'neighborhood-mixin',
    section: 'Guides',
    title: 'NeighborhoodMixin',
    tagline: 'Resolve connected records to drive graph-like UI views.',
    intro: 'NeighborhoodMixin returns related node IDs for a set of records and is useful when the frontend wants graph exploration or relation previews.',
    endpoints: [
      { method: 'POST', path: '/api/neighborhood/', description: 'resolve neighborhood graph' },
    ],
    params: [
      { name: 'model', type: 'string', description: 'Source model.' },
      { name: 'node_ids', type: 'array', description: 'IDs to expand from.' },
    ],
    code: `{\n  "model": "Author",\n  "node_ids": ["1", "2"]\n}`,
  },
  {
    key: 'error-shapes',
    section: 'Guides',
    title: 'Error Shapes',
    tagline: 'Understand validation and query execution failures from the API.',
    intro: 'Validation errors come back as structured payloads so the UI can point users to the problematic field, operator or model selection.',
    endpoints: [
      { method: 'POST', path: '/api/query/', description: 'validation and execution errors' },
    ],
    params: [
      { name: 'errors', type: 'array', description: 'List of issues, each with loc, msg, type and code.' },
    ],
    code: `{\n  "errors": [\n    {\n      "loc": ["field"],\n      "msg": "Field 'foo' does not exist in model Device",\n      "type": "value_error",\n      "code": "VALUE_ERROR"\n    }\n  ]\n}`,
  },
]

const DOCS_GROUPS: DocsNavGroup[] = [
  {
    label: 'Getting Started',
    items: [
      { key: 'overview', label: 'Overview' },
      { key: 'installation', label: 'Installation' },
      { key: 'quick-start', label: 'Quick Start' },
    ],
  },
  {
    label: 'API Reference',
    items: [
      { key: 'query-params', label: 'Query Params' },
      { key: 'filtering', label: 'Filtering' },
      { key: 'aggregations', label: 'Aggregations' },
      { key: 'having', label: 'Having' },
      { key: 'export', label: 'Export' },
      { key: 'sharing', label: 'Share Links' },
      { key: 'pagination', label: 'Pagination' },
      { key: 'permissions', label: 'Permissions' },
    ],
  },
  {
    label: 'Guides',
    items: [
      { key: 'qlab-mixin', label: 'QLabMixin' },
      { key: 'neighborhood-mixin', label: 'NeighborhoodMixin' },
      { key: 'error-shapes', label: 'Error Shapes' },
    ],
  },
]

type HistoryRange = 'all' | 'today' | '7d' | '30d'
type ToastTone = 'success' | 'error'

interface ToastItem {
  id: number
  tone: ToastTone
  message: string
}

const asDisplayString = (value: unknown) =>
  typeof value === 'string' ? value : value == null ? '' : String(value)

const titleCaseWords = (value: unknown) =>
  asDisplayString(value)
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')

const splitModelIdentifier = (value: unknown) =>
  asDisplayString(value)
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/_/g, ' ')
    .trim()

const getModelDisplayLabel = (model: Pick<BootstrapModel, 'model_name' | 'verbose_name'>) => {
  const verboseName = asDisplayString(model.verbose_name).trim()
  const raw = verboseName || splitModelIdentifier(model.model_name)
  return titleCaseWords(raw)
}

const getMetadataCacheKey = (modelName: string, appLabel?: string) =>
  `${appLabel || ''}:${modelName}`

// ─── App ─────────────────────────────────────────────────────────────────────

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>('home')
  const [activeModel, setActiveModel] = useState<string>('')
  const [activeDocsKey, setActiveDocsKey] = useState<string>('overview')
  const [theme, setTheme] = useState<ThemeMode>(() => {
    if (typeof window === 'undefined') return 'dark'
    const savedTheme = window.localStorage.getItem('qlab-theme')
    return savedTheme === 'light' ? 'light' : 'dark'
  })
  const [defaultPageSize, setDefaultPageSize] = useState(100)
  const [bootstrapModels, setBootstrapModels] = useState<BootstrapModel[]>([])
  const [historyItems, setHistoryItems] = useState<QueryHistoryItem[]>([])
  const [savedQueries, setSavedQueries] = useState<SavedQuery[]>([])
  const [metadataByKey, setMetadataByKey] = useState<Record<string, MetadataResponse>>({})
  const [queryPreset, setQueryPreset] = useState<QueryRequest | null>(null)
  const [queryResultPreset, setQueryResultPreset] = useState<QueryResponse | null>(null)
  const [activeSavedQueryId, setActiveSavedQueryId] = useState<number | null>(null)
  const [favoriteModels, setFavoriteModels] = useState<string[]>([])
  const [recentModelNames, setRecentModelNames] = useState<string[]>([])
  const [isBootstrapping, setIsBootstrapping] = useState(true)
  const [bootstrapError, setBootstrapError] = useState<string | null>(null)
  const [bootstrapConfig, setBootstrapConfig] = useState({
    environment: '',
    metadata: {
      relation_depth: 1,
      include_reverse_relations: false,
    },
  })
  const [settingsReady, setSettingsReady] = useState(false)
  const [activeHistoryModel, setActiveHistoryModel] = useState<string>('all')
  const [activeHistoryRange, setActiveHistoryRange] = useState<HistoryRange>('all')
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const normalizeTab = (tab: string | null | undefined): TabId => {
    if (tab === 'models') {
      return 'schema'
    }
    switch (tab) {
      case 'home':
      case 'queries':
      case 'schema':
      case 'saved':
      case 'history':
      case 'docs':
        return tab
      default:
        return 'home'
    }
  }

  const pushToast = (tone: ToastTone, message: string) => {
    const id = Date.now() + Math.floor(Math.random() * 1000)
    setToasts((current) => [...current, { id, tone, message }])
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id))
    }, 2400)
  }

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem('qlab-theme', theme)
  }, [theme])

  useEffect(() => {
    let isMounted = true

    const loadBootstrap = async () => {
      try {
        const bootstrap = await getBootstrap()
        if (!isMounted) {
          return
        }
        setBootstrapModels(bootstrap.models)
        if (bootstrap.config) {
          setBootstrapConfig(bootstrap.config)
        }
        setHistoryItems(bootstrap.history)
        setSavedQueries(bootstrap.saved_queries)
        setActiveSavedQueryId(bootstrap.saved_queries[0]?.id ?? null)
        // Bootstrap only carries the first 20 saved queries — load them all.
        void getSavedQueries()
          .then((all) => {
            if (isMounted) setSavedQueries(all)
          })
          .catch((error) => console.error(error))

        const urlParams = new URLSearchParams(window.location.search)
        const shareToken = urlParams.get('share')
        if (shareToken) {
          setUrlParam('share', null)
          try {
            const shared = await getShare(shareToken)
            const sharedModel = bootstrap.models.find(
              (m) => m.model_name === shared.model_name && m.app_label === shared.app_label,
            )
            if (!isMounted) return
            if (sharedModel) {
              urlParams.set('model', modelKey(sharedModel.app_label, sharedModel.model_name))
              setUrlParam('fields', null)
              setUrlParam('filters', null)
              setQueryPreset({
                ...shared.query_payload,
                model: sharedModel.model_name,
                app_label: sharedModel.app_label,
              })
            } else {
              urlParams.set('model', modelKey(shared.app_label, shared.model_name))
            }
          } catch {
            pushToast('error', 'This share link is invalid or has expired.')
          }
        }
        const urlModel = urlParams.get('model')
        const firstModel = bootstrap.models[0]
          ? modelKey(bootstrap.models[0].app_label, bootstrap.models[0].model_name)
          : ''
        const validUrlModel =
          urlModel && bootstrap.models.some((m) => modelKey(m.app_label, m.model_name) === urlModel)
            ? urlModel
            : null
        if (urlModel && !validUrlModel) {
          pushToast('error', 'The model in this link is not available to you.')
          // The shared fields/filters were scoped to a model this user can't see — don't
          // let them leak onto whichever fallback model ends up active.
          setUrlParam('fields', null)
          setUrlParam('filters', null)
        }
        setActiveModel(validUrlModel ?? firstModel)
        setActiveTab(validUrlModel ? 'queries' : normalizeTab(bootstrap.settings.last_active_tab))
        setActiveDocsKey(bootstrap.settings.active_docs_key || 'overview')
        setDefaultPageSize(bootstrap.settings.default_page_size || 100)
        setTheme(bootstrap.settings.theme || 'dark')
        setFavoriteModels(
          Array.isArray(bootstrap.settings.ui_state?.favorite_models)
            ? (bootstrap.settings.ui_state.favorite_models as string[])
            : [],
        )
        setRecentModelNames(
          Array.isArray(bootstrap.settings.ui_state?.recent_models)
            ? (bootstrap.settings.ui_state.recent_models as string[])
            : [],
        )
        setSettingsReady(true)
      } catch (error) {
        if (!isMounted) {
          return
        }
        setBootstrapError(error instanceof Error ? error.message : 'Failed to load QLab.')
      } finally {
        if (isMounted) {
          setIsBootstrapping(false)
        }
      }
    }

    void loadBootstrap()
    return () => {
      isMounted = false
    }
  }, [])

  useEffect(() => {
    if (!settingsReady) {
      return
    }
    const timeoutId = window.setTimeout(() => {
      void patchSettings({
        theme,
        default_page_size: defaultPageSize,
        last_active_tab: activeTab,
        active_docs_key: activeDocsKey,
        ui_state: {
          favorite_models: favoriteModels,
          recent_models: recentModelNames,
        },
      } as Partial<BootstrapSettings>)
    }, 250)

    return () => window.clearTimeout(timeoutId)
  }, [
    activeDocsKey,
    activeTab,
    defaultPageSize,
    favoriteModels,
    recentModelNames,
    settingsReady,
    theme,
  ])

  useEffect(() => {
    if (!activeModel) return
    setUrlParam('model', activeModel)
  }, [activeModel])

  useEffect(() => {
    document.querySelector('.app-content')?.scrollTo(0, 0)
  }, [activeTab])

  const activeModelEntry = bootstrapModels.find((model) => modelKey(model.app_label, model.model_name) === activeModel)
  const activeMetadataKey = activeModelEntry ? getMetadataCacheKey(activeModelEntry.model_name, activeModelEntry.app_label) : ''
  const activeMetadata = activeMetadataKey ? metadataByKey[activeMetadataKey] : undefined

  const loadMetadata = async (modelName: string, appLabel?: string) => {
    const cacheKey = getMetadataCacheKey(modelName, appLabel)
    if (metadataByKey[cacheKey]) {
      return metadataByKey[cacheKey]
    }

    const metadata = await getModelMetadata(modelName, appLabel, {
      relationDepth: bootstrapConfig.metadata.relation_depth,
      includeReverseRelations: bootstrapConfig.metadata.include_reverse_relations,
    })

    setMetadataByKey((current) => {
      if (current[cacheKey]) {
        return current
      }
      return { ...current, [cacheKey]: metadata }
    })

    return metadata
  }

  useEffect(() => {
    if (!activeModel) {
      return
    }

    const preloadMetadata = async () => {
      try {
        if (!activeModelEntry) return
        await loadMetadata(activeModelEntry.model_name, activeModelEntry.app_label)
      } catch (error) {
        console.error(error)
      }
    }

    void preloadMetadata()
  }, [activeModel, activeModelEntry?.app_label, bootstrapConfig.metadata.include_reverse_relations, bootstrapConfig.metadata.relation_depth, metadataByKey])

  const refreshHistory = async () => {
    try {
      setHistoryItems(await getHistory(50))
    } catch (error) {
      console.error(error)
      pushToast('error', 'Failed to refresh history.')
    }
  }

  const syncSavedQuery = (savedQuery: SavedQuery) => {
    setSavedQueries((current) => {
      const exists = current.some((entry) => entry.id === savedQuery.id)
      const next = exists
        ? current.map((entry) => entry.id === savedQuery.id ? savedQuery : entry)
        : [...current, savedQuery]
      return [...next].sort((left, right) => left.name.localeCompare(right.name))
    })
    setActiveSavedQueryId(savedQuery.id)
  }

  const handleModelSelect = (name: string) => {
    setActiveModel(name)
    setRecentModelNames((current) => {
      const next = [name, ...current.filter((entry) => entry !== name)]
      return next.slice(0, 6)
    })
  }

  const handleToggleModelFavorite = (name: string) => {
    setFavoriteModels((current) => (
      current.includes(name)
        ? current.filter((entry) => entry !== name)
        : [...current, name]
    ))
  }

  const handleRecentQuerySelect = (query: RecentQuery) => {
    const match = findModel(query.modelName, query.appLabel)
    if (match) {
      handleModelSelect(modelKey(match.app_label, match.model_name))
    }
    if (query.queryPayload) {
      // Load the query itself, not just its model.
      const { saved_query_id: _savedId, export_format: _format, ...payload } = query.queryPayload
      setQueryPreset({ ...(payload as unknown as QueryRequest), page: 1 })
    }
    setActiveTab('queries')
  }

  const findModel = (modelName: string, appLabel?: string) =>
    bootstrapModels.find(
      (entry) => entry.model_name === modelName && (!appLabel || entry.app_label === appLabel),
    ) || bootstrapModels.find((entry) => entry.model_name === modelName)

  const uniqueSavedQueryName = (base: string, taken: Set<string>) => {
    if (!taken.has(base)) return base
    let index = 2
    while (taken.has(`${base} (${index})`)) index += 1
    return `${base} (${index})`
  }

  const handleReplayQuery = (item: HistoryItem) => {
    const model = findModel(item.model, item.appLabel)
    if (model) {
      handleModelSelect(modelKey(model.app_label, model.model_name))
    }
    if (item.queryPayload) {
      setQueryPreset(item.queryPayload as unknown as QueryRequest)
    }
    setActiveTab('queries')
  }

  const handleOpenSavedQuery = (query: SavedQuery) => {
    const model = bootstrapModels.find((entry) => entry.model_name === query.model_name && entry.app_label === query.app_label)
    if (model) {
      handleModelSelect(modelKey(model.app_label, model.model_name))
    }
    setQueryPreset({
      ...(query.query_payload as unknown as QueryRequest),
      saved_query_id: query.id,
      title: query.name,
    })
    setActiveTab('queries')
  }

  const handleRunSavedQuery = async (query: SavedQuery) => {
    const response = await markSavedQueryRun(query.id)
    pushToast('success', `Ran saved query "${query.name}".`)
    await refreshHistory()
    setSavedQueries((current) => current.map((entry) => (
      entry.id === query.id
        ? { ...entry, last_run_at: new Date().toISOString() }
        : entry
    )))
    const model = bootstrapModels.find((entry) => entry.model_name === query.model_name && entry.app_label === query.app_label)
    if (model) {
      handleModelSelect(modelKey(model.app_label, model.model_name))
    }
    setQueryPreset({
      ...(query.query_payload as unknown as QueryRequest),
      saved_query_id: query.id,
      title: query.name,
    })
    setQueryResultPreset(response)
    setActiveTab('queries')
  }

  const getDirectFields = (metadata?: MetadataResponse) =>
    metadata
      ? metadata.fields.filter((field) => !field.name.includes('__'))
      : []

  const colors = ['#4a9eff', '#7cbcff', '#ad4eff', '#2c7be5', '#8c84ff', '#00efff']
  const buildFieldNotes = (field: MetadataField) => {
    const notes: string[] = []
    if (field.primary_key) notes.push('Primary key')
    if (field.max_length) notes.push(`max_length=${field.max_length}`)
    if (field.related_model) notes.push(`${field.type === 'reverse_relation' ? '←' : '→'} ${field.related_model}`)
    if (field.filter_name) notes.push(`filter: ${field.filter_name}`)
    if (field.choices?.length) notes.push(`choices: ${field.choices.map((choice) => choice.label).join(', ')}`)
    return notes.join(' · ')
  }

  const models: ModelDetail[] = bootstrapModels.map((model, index) => {
    const metadata = metadataByKey[getMetadataCacheKey(model.model_name, model.app_label)]
    const directFields = getDirectFields(metadata)
    return {
      name: modelKey(model.app_label, model.model_name),
      rawModelName: model.model_name,
      displayName: getModelDisplayLabel(model),
      count: model.count || 0,
      color: colors[index % colors.length],
      appLabel: model.app_label,
      favorite: favoriteModels.includes(modelKey(model.app_label, model.model_name)),
      description: model.verbose_name_plural,
      tags: [
        directFields.some((field) => field.allowed_operations.length > 0) ? 'filterable' : 'read-only',
        model.app_label,
      ],
      fields: directFields.map((field) => ({
        name: field.name,
        type: field.type,
        nullable: !field.required,
        filterable: field.allowed_operations.length > 0,
        notes: buildFieldNotes(field),
      })),
      relations: directFields
        .filter((field) => field.related_model)
        .map((field) => ({
          direction: field.type === 'reverse_relation' ? '<-' : field.type === 'manytomany' ? '↔' : '->',
          target: field.related_model || '',
          targetAppLabel: field.related_app_label || model.app_label,
          field: field.filter_name || field.name,
          kind: field.type === 'reverse_relation' ? 'rev' : field.type === 'manytomany' ? 'm2m' : 'fk',
        })),
    }
  })

  // Paging through results records one run per page; show each distinct
  // query only once in the sidebar (and skip failed runs).
  const recentHistory: QueryHistoryItem[] = []
  const seenRecent = new Set<string>()
  for (const item of historyItems) {
    if (item.status === 'failed') continue
    const {
      page: _page,
      page_size: _pageSize,
      title: _title,
      saved_query_id: _savedId,
      export_format: _exportFormat,
      order_by: _orderBy,
      format: _format,
      ...rest
    } = (item.query_payload || {}) as Record<string, unknown>
    const signature = `${item.app_label}::${item.model_name}::${JSON.stringify(rest)}`
    if (seenRecent.has(signature)) continue
    seenRecent.add(signature)
    recentHistory.push(item)
    if (recentHistory.length === 4) break
  }

  const recentQueries: RecentQuery[] = recentHistory.map((item) => ({
    title: item.title || `${getModelDisplayLabel(bootstrapModels.find((model) => model.model_name === item.model_name) || { model_name: item.model_name, verbose_name: item.model_name, app_label: '', verbose_name_plural: '' })} query`,
    meta: `${getModelDisplayLabel(bootstrapModels.find((model) => model.model_name === item.model_name) || { model_name: item.model_name, verbose_name: item.model_name, app_label: '', verbose_name_plural: '' })} · ${item.result_count ?? 0} results`,
    modelName: item.model_name,
    appLabel: item.app_label,
    queryPayload: item.query_payload,
  }))

  const savedQueriesForView = savedQueries.map((query) => ({
    ...query,
    display_name: query.name === `${query.model_name} query`
      ? `${getModelDisplayLabel(bootstrapModels.find((model) => model.model_name === query.model_name) || { model_name: query.model_name, verbose_name: query.model_name })} query`
      : query.name,
    display_model_name: getModelDisplayLabel(
      bootstrapModels.find((model) => model.model_name === query.model_name) || { model_name: query.model_name, verbose_name: query.model_name },
    ),
  }))

  const savedQueryNavItems = savedQueries.map((query) => ({
    id: query.id,
    name: query.name === `${query.model_name} query`
      ? `${getModelDisplayLabel(bootstrapModels.find((model) => model.model_name === query.model_name) || { model_name: query.model_name, verbose_name: query.model_name, app_label: '', verbose_name_plural: '' })} query`
      : query.name,
    modelName: query.model_name,
    modelLabel: getModelDisplayLabel(bootstrapModels.find((model) => model.model_name === query.model_name) || { model_name: query.model_name, verbose_name: query.model_name, app_label: '', verbose_name_plural: '' }),
    updatedAt: query.updated_at,
  }))

  const historyViewItems: HistoryItem[] = historyItems.map((item) => ({
    id: item.id,
    title: item.title || `${getModelDisplayLabel(bootstrapModels.find((model) => model.model_name === item.model_name) || { model_name: item.model_name, verbose_name: item.model_name })} query`,
    model: item.model_name,
    modelLabel: getModelDisplayLabel(bootstrapModels.find((model) => model.model_name === item.model_name) || { model_name: item.model_name, verbose_name: item.model_name }),
    filters: item.query_payload.filter_fields ? 'custom filters' : 'all records',
    ranAt: new Date(item.created_at).toLocaleString(),
    duration: item.duration_ms ? `${item.duration_ms} ms` : '—',
    status: item.status === 'failed' ? 'failed' : item.status === 'draft' ? 'draft' : item.saved_query ? 'cached' : 'ok',
    queryPayload: item.query_payload,
    appLabel: item.app_label,
  }))

  const now = Date.now()
  const historyModelCounts = historyItems.reduce<Record<string, number>>((acc, item) => {
    acc[item.model_name] = (acc[item.model_name] || 0) + 1
    return acc
  }, {})

  const filteredHistoryItems = historyViewItems.filter((item, index) => {
    const raw = historyItems[index]
    if (!raw) {
      return true
    }
    if (activeHistoryModel !== 'all' && item.model !== activeHistoryModel) {
      return false
    }
    if (activeHistoryRange === 'today') {
      return new Date(raw.created_at).toDateString() === new Date(now).toDateString()
    }
    if (activeHistoryRange === '7d') {
      return now - new Date(raw.created_at).getTime() <= 7 * 24 * 60 * 60 * 1000
    }
    if (activeHistoryRange === '30d') {
      return now - new Date(raw.created_at).getTime() <= 30 * 24 * 60 * 60 * 1000
    }
    return true
  })

  if (isBootstrapping) {
    return <div className="app-screen"><div className="app-screen-panel"><strong>Loading QLab</strong><span>Bootstrapping models, settings and saved query state.</span></div></div>
  }

  if (bootstrapError) {
    return <div className="app-screen"><div className="app-screen-panel error"><strong>QLab failed to load</strong><span>{bootstrapError}</span></div></div>
  }

  const homeStats = {
    totalModels: bootstrapModels.length,
    totalApps: new Set(bootstrapModels.map((model) => model.app_label)).size,
    queriesThisWeek: historyItems.filter((item) => now - new Date(item.created_at).getTime() <= 7 * 24 * 60 * 60 * 1000).length,
    savedQueriesCount: savedQueries.length,
  }

  return (
    <>
      <TopNav
        activeTab={activeTab}
        onTabChange={setActiveTab}
        theme={theme}
        onThemeToggle={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
        version="v0.5.0"
      />

      <div className="app-content">
        <div className="toast-stack">
          {toasts.map((toast) => (
            <div key={toast.id} className={`toast-item ${toast.tone}`}>
              {toast.message}
            </div>
          ))}
        </div>

        {activeTab === 'home' && <HomePage stats={homeStats} />}

        {activeTab !== 'home' && (
          <div className="page-row">
            <ContentSidebar
              activeTab={activeTab}
              models={models}
              recentQueries={recentQueries}
              recentModelNames={recentModelNames}
              activeModel={activeModel}
              savedQueries={savedQueryNavItems}
              activeSavedQueryId={activeSavedQueryId}
              historyModelOptions={Object.keys(historyModelCounts).map((label) => ({ label }))}
              activeHistoryModel={activeHistoryModel}
              onHistoryModelSelect={setActiveHistoryModel}
              activeHistoryRange={activeHistoryRange}
              onHistoryRangeSelect={setActiveHistoryRange}
              docsGroups={DOCS_GROUPS}
              activeDocsKey={activeDocsKey}
              onDocsSelect={setActiveDocsKey}
              onModelSelect={handleModelSelect}
              onSavedQuerySelect={(id) => {
                setActiveSavedQueryId(id)
                setActiveTab('saved')
              }}
              onToggleModelFavorite={handleToggleModelFavorite}
              onRecentQuerySelect={handleRecentQuerySelect}
            />

            <div className="page-main">
        {activeTab === 'queries' && (
              <QueriesPage
                activeModel={activeModelEntry?.model_name || ''}
                activeModelLabel={activeModelEntry ? getModelDisplayLabel(activeModelEntry) : activeModel}
                activeAppLabel={activeModelEntry?.app_label}
                activeMetadata={activeMetadata}
                metadataLoading={Boolean(activeModel) && !activeMetadata}
                defaultPageSize={defaultPageSize}
                queryPreset={queryPreset}
                resultsPreset={queryResultPreset}
                onPresetApplied={() => setQueryPreset(null)}
                onResultsPresetApplied={() => setQueryResultPreset(null)}
                onRequestMetadata={loadMetadata}
                onSaveQuery={async ({ name, description, payload }) => {
              try {
                if (payload.saved_query_id) {
                  const updated = await updateSavedQuery(payload.saved_query_id, {
                    name,
                    description,
                    query_payload: payload as unknown as Record<string, unknown>,
                  })
                  syncSavedQuery(updated)
                  pushToast('success', `Updated query "${updated.name}".`)
                } else {
                  const created = await createSavedQuery({
                    name,
                    description,
                    app_label: activeModelEntry?.app_label || '',
                    model_name: payload.model,
                    query_payload: payload as unknown as Record<string, unknown>,
                    tags: [],
                    is_shared: false,
                  })
                  syncSavedQuery(created)
                  pushToast('success', `Saved query "${created.name}".`)
                }
              } catch (error) {
                pushToast('error', error instanceof Error ? error.message : 'Could not save query.')
                throw error
              }
            }}
            onExportQuery={(payload, format) => exportQuery(payload, format)}
            onShareQuery={async (payload) => {
              const shared = await createShare({
                app_label: activeModelEntry?.app_label || payload.app_label,
                model_name: payload.model,
                query_payload: payload,
              })
              return shared.token
            }}
            onRunQuery={async (payload) => {
              // Errors surface inline in QueriesPage via .query-error, not as a toast, to avoid duplicate messaging.
              const response = await runQuery(payload)
              await refreshHistory()
              if (payload.saved_query_id) {
                setSavedQueries((current) => current.map((entry) => (
                  entry.id === payload.saved_query_id
                    ? { ...entry, last_run_at: new Date().toISOString() }
                    : entry
                )))
              }
              return response
            }}
              />
        )}
        {activeTab === 'schema' && (
          <ModelsPage
            models={models}
            activeModel={activeModel}
            activeMetadata={activeMetadata}
            onRequestMetadata={loadMetadata}
            onQueryModel={(modelName) => {
              const entry = bootstrapModels.find((m) => m.model_name === modelName)
              setActiveModel(entry ? modelKey(entry.app_label, entry.model_name) : modelName)
              setActiveTab('queries')
            }}
          />
        )}
        {activeTab === 'saved' && (
              <SavedQueriesPage
                savedQueries={savedQueriesForView}
                activeSavedQueryId={activeSavedQueryId}
            onSelectSavedQuery={setActiveSavedQueryId}
            onUpdateSavedQuery={async (id, payload) => {
              try {
                const updated = await updateSavedQuery(id, payload)
                syncSavedQuery(updated)
                pushToast('success', `Updated "${updated.name}".`)
              } catch (error) {
                pushToast('error', error instanceof Error ? error.message : 'Could not update saved query.')
                throw error
              }
            }}
            onDeleteSavedQuery={async (id) => {
              try {
                await deleteSavedQuery(id)
                setSavedQueries((current) => {
                  const next = current.filter((entry) => entry.id !== id)
                  setActiveSavedQueryId((currentActive) => currentActive === id ? next[0]?.id ?? null : currentActive)
                  return next
                })
                pushToast('success', 'Saved query deleted.')
              } catch (error) {
                pushToast('error', error instanceof Error ? error.message : 'Could not delete saved query.')
              }
            }}
            onOpenInBuilder={handleOpenSavedQuery}
            onRunSavedQuery={async (query) => {
              try {
                await handleRunSavedQuery(query)
              } catch (error) {
                pushToast('error', error instanceof Error ? error.message : 'Could not run saved query.')
              }
            }}
          />
        )}
        {activeTab === 'history' && (
          <HistoryPage
            historyItems={filteredHistoryItems}
            savedQueryNames={savedQueries.map((entry) => entry.name)}
            onReplayQuery={handleReplayQuery}
            onSaveQuery={async (item, takenNames) => {
              try {
                // Names are unique per user — saving the same history entry
                // twice (or several "Device query" runs at once) used to fail.
                const taken = takenNames ?? new Set(savedQueries.map((entry) => entry.name))
                const name = uniqueSavedQueryName(item.title || `${item.model} query`, taken)
                taken.add(name)
                const { saved_query_id: _savedId, export_format: _format, ...payload } =
                  (item.queryPayload || {}) as Record<string, unknown>
                const created = await createSavedQuery({
                  name,
                  description: `Saved from history on ${item.ranAt}`,
                  app_label: item.appLabel || findModel(item.model)?.app_label || '',
                  model_name: item.model,
                  query_payload: payload,
                  tags: ['history'],
                  is_shared: false,
                })
                syncSavedQuery(created)
                setActiveTab('saved')
                pushToast('success', `Saved "${created.name}" from history.`)
              } catch (error) {
                pushToast('error', error instanceof Error ? error.message : 'Could not save from history.')
              }
            }}
          />
        )}
        {activeTab === 'docs' && (
          <DocsPage docs={DOCS_ENTRIES} activeDocKey={activeDocsKey} />
        )}
            </div>
          </div>
        )}
      </div>
    </>
  )
}
