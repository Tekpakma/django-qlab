import React, { useMemo, useState } from 'react'
import { TabId } from './TopNav'
import { handleActivationKeyDown, handleRovingKeyDown } from '../lib/keyboardNav'
import './ContentSidebar.css'

export interface ModelEntry {
  name: string
  rawModelName?: string
  displayName?: string
  count: number
  color: string
  appLabel?: string
  favorite?: boolean
}

export interface RecentQuery {
  title: string
  meta: string
  modelName: string
  appLabel?: string
  queryPayload?: Record<string, unknown>
}

export interface SavedQueryNavItem {
  id: number
  name: string
  modelName: string
  modelLabel?: string
  updatedAt: string
}

export interface DocsNavItem {
  key: string
  label: string
}

export interface DocsNavGroup {
  label: string
  items: DocsNavItem[]
}

interface SidebarChoice {
  label: string
  color?: string
  active?: boolean
}

interface ContentSidebarProps {
  activeTab: TabId
  models: ModelEntry[]
  recentQueries: RecentQuery[]
  recentModelNames?: string[]
  activeModel?: string
  savedQueries?: SavedQueryNavItem[]
  activeSavedQueryId?: number | null
  historyModelOptions?: Array<{ label: string }>
  activeHistoryModel?: string
  onHistoryModelSelect?: (model: string) => void
  activeHistoryRange?: 'all' | 'today' | '7d' | '30d'
  onHistoryRangeSelect?: (range: 'all' | 'today' | '7d' | '30d') => void
  docsGroups?: DocsNavGroup[]
  activeDocsKey?: string
  onDocsSelect?: (key: string) => void
  onModelSelect: (name: string) => void
  onSavedQuerySelect?: (id: number) => void
  onToggleModelFavorite?: (name: string) => void
  onRecentQuerySelect: (q: RecentQuery) => void
}

function normalizeQuery(value: string) {
  return value.trim().toLowerCase()
}

function groupModels(models: ModelEntry[]) {
  const groups = new Map<string, ModelEntry[]>()
  models.forEach((model) => {
    const key = model.appLabel || 'other'
    if (!groups.has(key)) {
      groups.set(key, [])
    }
    groups.get(key)?.push(model)
  })
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([label, entries]) => ({
      label,
      entries: entries.sort((left, right) => left.name.localeCompare(right.name)),
    }))
}

const IconSearch = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" className="sidebar-search-icon">
    <circle cx="10" cy="10" r="6.5" stroke="currentColor" strokeWidth="2" />
    <line x1="14.8" y1="14.8" x2="20" y2="20" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
)

const FavoriteButton: React.FC<{
  active: boolean
  onClick: () => void
}> = ({ active, onClick }) => (
  <button
    className={`favorite-btn${active ? ' active' : ''}`}
    type="button"
    title={active ? 'Remove favorite' : 'Add favorite'}
    onClick={(event) => {
      event.stopPropagation()
      onClick()
    }}
  >
    ★
  </button>
)

const ModelListItem: React.FC<{
  model: ModelEntry
  active?: boolean
  onSelect: (name: string) => void
  onToggleFavorite?: (name: string) => void
}> = ({ model, active, onSelect, onToggleFavorite }) => (
  <div
    className={`res-item${active ? ' active' : ''}`}
    role="button"
    tabIndex={0}
    data-roving-item
    onClick={() => onSelect(model.name)}
    onKeyDown={(event) => handleActivationKeyDown(event, () => onSelect(model.name))}
  >
    <div className="res-pip" style={{ background: model.color }} />
    <span className="res-name">{model.displayName || model.name}</span>
    {onToggleFavorite && (
      <FavoriteButton active={Boolean(model.favorite)} onClick={() => onToggleFavorite(model.name)} />
    )}
  </div>
)

const ModelSearch: React.FC<{
  value: string
  onChange: (value: string) => void
}> = ({ value, onChange }) => (
  <div className="sidebar-search-wrap">
    <IconSearch />
    <input
      className="sidebar-search"
      placeholder="Search models"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  </div>
)

const WIDE_TABS: TabId[] = ['queries', 'schema']

export const ContentSidebar: React.FC<ContentSidebarProps> = ({
  activeTab,
  models,
  recentQueries,
  recentModelNames = [],
  activeModel,
  savedQueries = [],
  activeSavedQueryId,
  historyModelOptions = [],
  activeHistoryModel,
  onHistoryModelSelect,
  activeHistoryRange,
  onHistoryRangeSelect,
  docsGroups = [],
  activeDocsKey,
  onDocsSelect,
  onModelSelect,
  onSavedQuerySelect,
  onToggleModelFavorite,
  onRecentQuerySelect,
}) => {
  const wide = WIDE_TABS.includes(activeTab)

  return (
    <div className={`page-sidebar${wide ? ' wide' : ' narrow'}`}>
      {activeTab === 'queries' && (
        <QueriesSidebarPanel
          models={models}
          recentQueries={recentQueries}
          recentModelNames={recentModelNames}
          activeModel={activeModel}
          onModelSelect={onModelSelect}
          onToggleModelFavorite={onToggleModelFavorite}
          onRecentQuerySelect={onRecentQuerySelect}
        />
      )}

      {activeTab === 'schema' && (
        <SchemaSidebarPanel
          models={models}
          recentModelNames={recentModelNames}
          activeModel={activeModel}
          onModelSelect={onModelSelect}
          onToggleModelFavorite={onToggleModelFavorite}
        />
      )}

      {activeTab === 'saved' && (
        <SavedSidebarPanel
          queries={savedQueries}
          activeId={activeSavedQueryId}
          onSelect={onSavedQuerySelect}
        />
      )}

      {activeTab === 'history' && (
        <HistorySidebarPanel
          modelOptions={historyModelOptions}
          activeModel={activeHistoryModel || 'all'}
          onSelectModel={onHistoryModelSelect}
          activeRange={activeHistoryRange || 'all'}
          onSelectRange={onHistoryRangeSelect}
        />
      )}

      {activeTab === 'docs' && (
        <DocsSidebarPanel
          groups={docsGroups}
          activeKey={activeDocsKey}
          onSelect={onDocsSelect}
        />
      )}
    </div>
  )
}

interface QueryModelPanelProps {
  models: ModelEntry[]
  recentModelNames: string[]
  activeModel?: string
  onModelSelect: (name: string) => void
  onToggleModelFavorite?: (name: string) => void
}

const QueryModelPanel: React.FC<QueryModelPanelProps> = ({
  models,
  recentModelNames,
  activeModel,
  onModelSelect,
  onToggleModelFavorite,
}) => {
  const [search, setSearch] = useState('')
  const query = normalizeQuery(search)

  const filteredModels = useMemo(() => (
    models.filter((model) => {
      if (!query) {
        return true
      }
      return model.name.toLowerCase().includes(query) || (model.appLabel || '').toLowerCase().includes(query)
    })
  ), [models, query])

  const favoriteNames = new Set(filteredModels.filter((model) => model.favorite).map((model) => model.name))
  const favorites = filteredModels.filter((model) => favoriteNames.has(model.name))
  const recentModels = recentModelNames
    .map((name) => filteredModels.find((model) => model.name === name))
    .filter((model): model is ModelEntry => model !== undefined)
    .filter((model) => !favoriteNames.has(model.name))
  const grouped = groupModels(filteredModels)

  return (
    <div onKeyDown={handleRovingKeyDown}>
      <ModelSearch value={search} onChange={setSearch} />

      {favorites.length > 0 && (
        <>
          <div className="sidebar-label">Favorites</div>
          {favorites.map((model) => (
            <ModelListItem
              key={model.name}
              model={model}
              active={activeModel === model.name}
              onSelect={onModelSelect}
              onToggleFavorite={onToggleModelFavorite}
            />
          ))}
        </>
      )}

      {recentModels.length > 0 && (
        <>
          <div className="sidebar-label">Recent Models</div>
          {recentModels.map((model) => (
            <ModelListItem
              key={model.name}
              model={model}
              active={activeModel === model.name}
              onSelect={onModelSelect}
              onToggleFavorite={onToggleModelFavorite}
            />
          ))}
        </>
      )}

      <div className="sidebar-label">All Models</div>
      {grouped.length === 0 && <div className="sidebar-empty">No models match.</div>}
      {grouped.map((group) => (
        <div key={group.label} className="model-group">
          <div className="sidebar-subgroup">{group.label}</div>
          {group.entries.map((model) => (
            <ModelListItem
              key={model.name}
              model={model}
              active={activeModel === model.name}
              onSelect={onModelSelect}
              onToggleFavorite={onToggleModelFavorite}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

interface QueriesSidebarPanelProps extends QueryModelPanelProps {
  recentQueries: RecentQuery[]
  onRecentQuerySelect: (q: RecentQuery) => void
}

const QueriesSidebarPanel: React.FC<QueriesSidebarPanelProps> = ({
  models,
  recentQueries,
  recentModelNames,
  activeModel,
  onModelSelect,
  onToggleModelFavorite,
  onRecentQuerySelect,
}) => (
  <>
    <div className="sidebar-card">
      <QueryModelPanel
        models={models}
        recentModelNames={recentModelNames}
        activeModel={activeModel}
        onModelSelect={onModelSelect}
        onToggleModelFavorite={onToggleModelFavorite}
      />
    </div>
    <div className="sidebar-card" onKeyDown={handleRovingKeyDown}>
      <div className="sidebar-label">Recent Queries</div>
      {recentQueries.length === 0 && (
        <div className="sidebar-empty">Nothing yet — run a query to see it here.</div>
      )}
      {recentQueries.map((query, index) => (
        <div
          key={`${query.title}-${index}`}
          className="recent-item"
          role="button"
          tabIndex={0}
          data-roving-item
          onClick={() => onRecentQuerySelect(query)}
          onKeyDown={(event) => handleActivationKeyDown(event, () => onRecentQuerySelect(query))}
        >
          <div className="recent-title">{query.title}</div>
          <div className="recent-meta">{query.meta}</div>
        </div>
      ))}
    </div>
  </>
)

interface SchemaSidebarPanelProps extends QueryModelPanelProps {}

const SchemaSidebarPanel: React.FC<SchemaSidebarPanelProps> = ({
  models,
  recentModelNames,
  activeModel,
  onModelSelect,
  onToggleModelFavorite,
}) => (
  <div className="sidebar-card">
    <QueryModelPanel
      models={models}
      recentModelNames={recentModelNames}
      activeModel={activeModel}
      onModelSelect={onModelSelect}
      onToggleModelFavorite={onToggleModelFavorite}
    />
  </div>
)

const PassiveSidebarItem: React.FC<{ item: SidebarChoice; onClick?: () => void }> = ({ item, onClick }) => (
  <div
    className={`res-item${item.active ? ' active' : ''}`}
    role="button"
    tabIndex={0}
    data-roving-item
    onClick={onClick}
    onKeyDown={(event) => onClick && handleActivationKeyDown(event, onClick)}
  >
    {item.color && <div className="res-pip" style={{ background: item.color }} />}
    <span className="res-name">{item.label}</span>
  </div>
)

const HistorySidebarPanel: React.FC<{
  modelOptions: Array<{ label: string }>
  activeModel: string
  onSelectModel?: (model: string) => void
  activeRange: 'all' | 'today' | '7d' | '30d'
  onSelectRange?: (range: 'all' | 'today' | '7d' | '30d') => void
}> = ({ modelOptions, activeModel, onSelectModel, activeRange, onSelectRange }) => (
  <>
    <div className="sidebar-card" onKeyDown={handleRovingKeyDown}>
      <div className="sidebar-label">Filter by Model</div>
      <PassiveSidebarItem
        item={{ label: 'All Models', active: activeModel === 'all' }}
        onClick={() => onSelectModel?.('all')}
      />
      {modelOptions.map((item) => (
        <PassiveSidebarItem
          key={item.label}
          item={{ label: item.label, active: activeModel === item.label }}
          onClick={() => onSelectModel?.(item.label)}
        />
      ))}
    </div>
    <div className="sidebar-card" onKeyDown={handleRovingKeyDown}>
      <div className="sidebar-label">Time Range</div>
      <PassiveSidebarItem item={{ label: 'All Time', active: activeRange === 'all' }} onClick={() => onSelectRange?.('all')} />
      <PassiveSidebarItem item={{ label: 'Today', active: activeRange === 'today' }} onClick={() => onSelectRange?.('today')} />
      <PassiveSidebarItem item={{ label: 'Last 7 days', active: activeRange === '7d' }} onClick={() => onSelectRange?.('7d')} />
      <PassiveSidebarItem item={{ label: 'Last 30 days', active: activeRange === '30d' }} onClick={() => onSelectRange?.('30d')} />
    </div>
  </>
)

const SavedSidebarPanel: React.FC<{
  queries: SavedQueryNavItem[]
  activeId?: number | null
  onSelect?: (id: number) => void
}> = ({ queries, activeId, onSelect }) => (
  <div className="sidebar-card" onKeyDown={handleRovingKeyDown}>
    <div className="sidebar-label">Saved Queries</div>
    {queries.length === 0 && <div className="sidebar-empty">No saved queries yet.</div>}
    {queries.map((query) => (
      <div
        key={query.id}
        className={`recent-item saved-query-item${activeId === query.id ? ' active' : ''}`}
        role="button"
        tabIndex={0}
        data-roving-item
        onClick={() => onSelect?.(query.id)}
        onKeyDown={(event) => handleActivationKeyDown(event, () => onSelect?.(query.id))}
      >
        <div className="recent-title">{query.name}</div>
        <div className="recent-meta">{query.modelLabel || query.modelName} · {new Date(query.updatedAt).toLocaleDateString()}</div>
      </div>
    ))}
  </div>
)


const DocsSidebarPanel: React.FC<{
  groups: DocsNavGroup[]
  activeKey?: string
  onSelect?: (key: string) => void
}> = ({ groups, activeKey, onSelect }) => (
  <div className="sidebar-card" onKeyDown={handleRovingKeyDown}>
    {groups.map((group, index) => (
      <React.Fragment key={group.label}>
        {index > 0 && <div className="sidebar-divider docs-sidebar-divider" />}
        <div className="docs-sidebar-group">
          <div className="sidebar-label">{group.label}</div>
          {group.items.map((item) => (
            <div
              key={item.key}
              className={`res-item${activeKey === item.key ? ' active' : ''}`}
              role="button"
              tabIndex={0}
              data-roving-item
              onClick={() => onSelect?.(item.key)}
              onKeyDown={(event) => handleActivationKeyDown(event, () => onSelect?.(item.key))}
            >
              <span className="res-name">{item.label}</span>
            </div>
          ))}
        </div>
      </React.Fragment>
    ))}
  </div>
)
