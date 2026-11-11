import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Dialog, DialogPanel, DialogTitle } from '@headlessui/react'
import {
  AggregationFunction,
  ExportFormat,
  ExportResult,
  MetadataField,
  MetadataResponse,
  QueryCondition,
  QueryFilterGroup,
  QueryRequest,
  QueryResponse,
} from '../lib/api'
import {
  ALL_OPERATIONS,
  HAVING_OPERATIONS,
  decodeBase64Url,
  encodeBase64Url,
  inputToValue,
  isBooleanType,
  operationLabel,
  typedPlaceholder,
  validateTypedInput,
  valueToInput,
  visitConditions,
} from '../lib/filters'
import { setUrlParam } from '../lib/url'
import './QueriesPage.css'

type FilterOp = string
type FilterJoiner = 'and' | 'or'

interface FilterConditionNode {
  id: string
  type: 'condition'
  field: string
  op: FilterOp
  value: string
}

interface FilterGroupNode {
  id: string
  type: 'group'
  operator: FilterJoiner
  children: FilterNode[]
}

type FilterNode = FilterConditionNode | FilterGroupNode

interface ResultRow {
  [key: string]: unknown
}

type ResultTab = 'table' | 'json'

interface FieldSuggestion {
  path: string
  label: string
  displayLabel: string
  type: string
  isRelation: boolean
  relatedModel?: string | null
  relatedAppLabel?: string | null
}

interface FieldResolution {
  suggestions: FieldSuggestion[]
  exactMatch: FieldSuggestion | null
  error: string | null
}

interface FieldAutocompleteProps {
  value: string
  rootModel: string
  rootAppLabel?: string
  rootMetadata?: MetadataResponse
  placeholder?: string
  className?: string
  autoFocus?: boolean
  compact?: boolean
  excludeFields?: string[]
  onChange: (value: string) => void
  onRequestMetadata: (modelName: string, appLabel?: string) => Promise<MetadataResponse>
  onSubmit?: (value: string) => void
}

const RELATION_TYPES = new Set([
  'foreignkey',
  'onetoone',
  'manytomany',
  'reverse_relation',
])

const AGGREGATION_FUNCTIONS: AggregationFunction[] = ['count', 'sum', 'avg', 'min', 'max']

interface AggregationRow {
  id: string
  field: string
  function: AggregationFunction
  distinct: boolean
  alias: string
}

interface HavingRow {
  id: string
  alias: string
  op: FilterOp
  value: string
}

const aggregationAlias = (row: Pick<AggregationRow, 'alias' | 'field' | 'function'>) =>
  row.alias.trim() || (row.field.trim() ? `${row.function}_${row.field.trim()}` : row.function)

// Longer encoded filters are not mirrored into the address bar: very long URLs
// get rejected by proxies/servers (414) and truncated by chat tools. Sharing
// uses short server-side links instead (see handleShare).
const MAX_URL_FILTER_LENGTH = 1500

function readUrlFilters(): QueryFilterGroup | undefined {
  const urlFilters = new URLSearchParams(window.location.search).get('filters')
  if (!urlFilters) return undefined
  for (const decode of [decodeBase64Url, (value: string) => atob(value)]) {
    try {
      return JSON.parse(decode(urlFilters)) as QueryFilterGroup
    } catch {
      // try the next (legacy) encoding
    }
  }
  return undefined
}

const splitIdentifier = (value: string) =>
  value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/_/g, ' ')
    .trim()

const normalizeDisplayLabel = (label: string, fieldName: string) => {
  const normalizedLabel = label.trim().toLowerCase()
  const normalizedField = splitIdentifier(fieldName).trim().toLowerCase()
  return normalizedLabel === normalizedField ? splitIdentifier(fieldName) : label
}

const IconPlay = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <polygon points="5 3 19 12 5 21 5 3" />
  </svg>
)

const IconCopy = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
)

const IconDownload = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="7 10 12 15 17 10" />
    <line x1="12" y1="15" x2="12" y2="3" />
  </svg>
)

function getRelativeTime(date: Date): string {
  const secs = Math.floor((Date.now() - date.getTime()) / 1000)
  if (secs < 15) return 'just now'
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

const IconShare = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
  </svg>
)

const IconSave = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
    <polyline points="17 21 17 13 7 13 7 21" />
    <polyline points="7 3 7 8 15 8" />
  </svg>
)

let idCounter = 0
const newId = () => `filter-${++idCounter}`

function newCondition(fallbackField: string): FilterConditionNode {
  return {
    id: newId(),
    type: 'condition',
    field: fallbackField,
    op: 'is',
    value: '',
  }
}

function newGroup(fallbackField: string, operator: FilterJoiner = 'and'): FilterGroupNode {
  return {
    id: newId(),
    type: 'group',
    operator,
    children: fallbackField ? [newCondition(fallbackField)] : [],
  }
}

function toCondition(node: FilterConditionNode): QueryCondition {
  return {
    field: node.field,
    op: node.op,
    value: inputToValue(node.op, node.value),
  }
}

function serializeNode(node: FilterNode): QueryCondition | QueryFilterGroup | null {
  if (node.type === 'condition') {
    if (!node.field.trim() || !node.value.trim()) {
      return null
    }
    return toCondition(node)
  }

  const children = node.children
    .map((child) => serializeNode(child))
    .filter((child): child is QueryCondition | QueryFilterGroup => child !== null)

  if (children.length === 0) {
    return null
  }

  return node.operator === 'or'
    ? { or_operation: children }
    : { and_operation: children }
}

function serializeFilters(root: FilterGroupNode): QueryFilterGroup | undefined {
  const serialized = serializeNode(root)
  if (!serialized || 'field' in serialized) {
    return undefined
  }
  return serialized
}

function deserializeFilters(
  filterGroup: QueryFilterGroup | undefined,
  fallbackField: string,
): FilterGroupNode {
  if (!filterGroup) {
    return newGroup(fallbackField, 'and')
  }

  const walk = (node: QueryFilterGroup | QueryCondition): FilterNode => {
    if ('field' in node) {
      return {
        id: newId(),
        type: 'condition',
        field: node.field,
        op: node.op || 'is',
        value: valueToInput(node.value),
      }
    }

    if (node.or_operation) {
      return {
        id: newId(),
        type: 'group',
        operator: 'or',
        children: node.or_operation.map((child) =>
          walk(child as QueryFilterGroup | QueryCondition),
        ),
      }
    }

    return {
      id: newId(),
      type: 'group',
      operator: 'and',
      children: (node.and_operation || []).map((child) =>
        walk(child as QueryFilterGroup | QueryCondition),
      ),
    }
  }

  const result = walk(filterGroup)
  return result.type === 'group'
    ? result
    : {
        id: newId(),
        type: 'group',
        operator: 'and',
        children: [result],
      }
}

function getDirectFields(metadata?: MetadataResponse): MetadataField[] {
  return metadata ? metadata.fields.filter((field) => !field.name.includes('__')) : []
}

function isRelationField(field: MetadataField) {
  return RELATION_TYPES.has(field.type) && Boolean(field.related_model)
}

async function resolveFieldPath(
  query: string,
  rootModel: string,
  rootAppLabel: string | undefined,
  rootMetadata: MetadataResponse | undefined,
  onRequestMetadata: (modelName: string, appLabel?: string) => Promise<MetadataResponse>,
): Promise<FieldResolution> {
  const normalized = query.trim()
  const endsWithPathSeparator = normalized.endsWith('__')
  const segments = normalized.split('__').filter(Boolean)

  let currentModel = rootModel
  let currentAppLabel = rootAppLabel
  let currentMetadata =
    rootMetadata || (await onRequestMetadata(rootModel, rootAppLabel))
  const traversedSegments: string[] = []

  const relationSegments = endsWithPathSeparator ? segments : segments.slice(0, -1)
  const partial = endsWithPathSeparator ? '' : segments[segments.length - 1] || ''

  for (const segment of relationSegments) {
    const relationField = getDirectFields(currentMetadata).find(
      (field) => field.name === segment && isRelationField(field),
    )

    if (!relationField || !relationField.related_model) {
      return {
        suggestions: [],
        exactMatch: null,
        error: 'Unknown relation path.',
      }
    }

    traversedSegments.push(segment)
    currentModel = relationField.related_model
    currentAppLabel = relationField.related_app_label || currentAppLabel
    currentMetadata = await onRequestMetadata(currentModel, currentAppLabel)
  }

  const directFields = getDirectFields(currentMetadata)
  const exactField = !endsWithPathSeparator
    ? directFields.find((field) => field.name === partial)
    : null

  const suggestions = directFields
    .filter((field) => {
      if (!partial) {
        return true
      }
      const needle = partial.toLowerCase()
      return (
        field.name.toLowerCase().includes(needle) ||
        field.label.toLowerCase().includes(needle)
      )
    })
    .map((field) => ({
      path: [...traversedSegments, field.name].join('__'),
      label: field.label || field.name,
      displayLabel: normalizeDisplayLabel(field.label || field.name, field.name),
      type: field.type,
      isRelation: isRelationField(field),
      relatedModel: field.related_model,
      relatedAppLabel: field.related_app_label,
    }))

  return {
    suggestions,
    exactMatch: exactField
      ? {
          path: [...traversedSegments, exactField.name].join('__'),
          label: exactField.label || exactField.name,
          displayLabel: normalizeDisplayLabel(
            exactField.label || exactField.name,
            exactField.name,
          ),
          type: exactField.type,
          isRelation: isRelationField(exactField),
          relatedModel: exactField.related_model,
          relatedAppLabel: exactField.related_app_label,
        }
      : null,
    error: null,
  }
}

const ResourceRow: React.FC<{
  activeModel: string
  activeModelLabel?: string
}> = ({ activeModel, activeModelLabel }) => (
  <div className="resource-row">
    <span className="from-label">FROM</span>
    <div className="resource-current">
      <button className="resource-chip active" type="button">
        {activeModelLabel || activeModel}
      </button>
      <span className="field-hint">Change model in the left sidebar.</span>
    </div>
  </div>
)

const IconChevronDown = () => (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="6 9 12 15 18 9" />
  </svg>
)

interface DropdownOption<T extends string> {
  value: T
  label: string
}

interface DropdownProps<T extends string> {
  value: T
  options: DropdownOption<T>[]
  onChange: (value: T) => void
  className?: string
  menuClassName?: string
}

function Dropdown<T extends string>({ value, options, onChange, className = '', menuClassName = '' }: DropdownProps<T>) {
  const [open, setOpen] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const closeTimeout = useRef<number | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const current = options.find((option) => option.value === value)

  const clearCloseTimer = () => {
    if (closeTimeout.current) {
      window.clearTimeout(closeTimeout.current)
      closeTimeout.current = null
    }
  }

  const selectOption = (option: DropdownOption<T>) => {
    onChange(option.value)
    setOpen(false)
    setHighlightedIndex(-1)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
        setHighlightedIndex(Math.max(options.findIndex((option) => option.value === value), 0))
        return
      }
      const next = Math.min(highlightedIndex + 1, options.length - 1)
      setHighlightedIndex(next)
      menuRef.current?.children[next]?.scrollIntoView({ block: 'nearest' })
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
        setHighlightedIndex(Math.max(options.findIndex((option) => option.value === value), 0))
        return
      }
      const next = Math.max(highlightedIndex - 1, 0)
      setHighlightedIndex(next)
      menuRef.current?.children[next]?.scrollIntoView({ block: 'nearest' })
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (open && highlightedIndex >= 0) {
        selectOption(options[highlightedIndex])
      } else {
        setOpen(true)
      }
    } else if (event.key === 'Escape') {
      setOpen(false)
      setHighlightedIndex(-1)
    }
  }

  return (
    <div
      className={`dropdown${className ? ` ${className}` : ''}`}
      onBlur={() => {
        closeTimeout.current = window.setTimeout(() => setOpen(false), 120)
      }}
    >
      <button
        type="button"
        className="dropdown-trigger"
        onClick={() => {
          clearCloseTimer()
          setOpen((current) => !current)
        }}
        onKeyDown={handleKeyDown}
      >
        <span>{current?.label ?? value}</span>
        <IconChevronDown />
      </button>

      {open && (
        <div className={`field-autocomplete-menu dropdown-menu${menuClassName ? ` ${menuClassName}` : ''}`} ref={menuRef}>
          {options.map((option, index) => (
            <button
              key={option.value}
              type="button"
              className={`field-picker-item${option.value === value ? ' selected' : ''}${index === highlightedIndex ? ' highlighted' : ''}`}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setHighlightedIndex(index)}
              onClick={() => selectOption(option)}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

const FieldAutocomplete: React.FC<FieldAutocompleteProps> = ({
  value,
  rootModel,
  rootAppLabel,
  rootMetadata,
  placeholder = 'Search field',
  className = '',
  autoFocus = false,
  compact = false,
  excludeFields,
  onChange,
  onRequestMetadata,
  onSubmit,
}) => {
  const [query, setQuery] = useState(value)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [resolution, setResolution] = useState<FieldResolution>({
    suggestions: [],
    exactMatch: null,
    error: null,
  })
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const closeTimeout = useRef<number | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setQuery(value)
  }, [value])

  useEffect(() => {
    let active = true

    const load = async () => {
      setLoading(true)
      try {
        const nextResolution = await resolveFieldPath(
          query,
          rootModel,
          rootAppLabel,
          rootMetadata,
          onRequestMetadata,
        )
        if (active) {
          setResolution(nextResolution)
          setHighlightedIndex(-1)
        }
      } catch {
        if (active) {
          setResolution({
            suggestions: [],
            exactMatch: null,
            error: 'Could not load relation metadata.',
          })
        }
      } finally {
        if (active) {
          setLoading(false)
        }
      }
    }

    if (!rootModel) {
      setResolution({ suggestions: [], exactMatch: null, error: null })
      return () => {
        active = false
      }
    }

    void load()
    return () => {
      active = false
    }
  }, [onRequestMetadata, query, rootAppLabel, rootMetadata, rootModel])

  const clearCloseTimer = () => {
    if (closeTimeout.current) {
      window.clearTimeout(closeTimeout.current)
      closeTimeout.current = null
    }
  }

  const handleSuggestionClick = (suggestion: FieldSuggestion) => {
    if (suggestion.isRelation) {
      const nextPath = `${suggestion.path}__`
      setQuery(nextPath)
      onChange(nextPath)
      setHighlightedIndex(-1)
      setOpen(true)
      return
    }

    setQuery(suggestion.path)
    onChange(suggestion.path)
    setOpen(false)
    onSubmit?.(suggestion.path)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    const visibleSuggestions = resolution.suggestions.filter(
      (s) => !excludeFields?.includes(s.path),
    )

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
        setHighlightedIndex(0)
        return
      }
      const next = Math.min(highlightedIndex + 1, visibleSuggestions.length - 1)
      setHighlightedIndex(next)
      menuRef.current?.children[next]?.scrollIntoView({ block: 'nearest' })
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      const next = Math.max(highlightedIndex - 1, 0)
      setHighlightedIndex(next)
      menuRef.current?.children[next]?.scrollIntoView({ block: 'nearest' })
    } else if (event.key === 'Enter') {
      if (highlightedIndex >= 0 && visibleSuggestions[highlightedIndex]) {
        event.preventDefault()
        handleSuggestionClick(visibleSuggestions[highlightedIndex])
      }
    } else if (event.key === 'Escape') {
      setOpen(false)
      setHighlightedIndex(-1)
    }
  }

  return (
    <div
      className={`field-autocomplete${compact ? ' compact' : ''}${className ? ` ${className}` : ''}`}
      onFocus={() => {
        clearCloseTimer()
        setOpen(true)
      }}
      onBlur={() => {
        closeTimeout.current = window.setTimeout(() => setOpen(false), 120)
      }}
    >
      <input
        className="field-autocomplete-input"
        value={query}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onChange={(event) => {
          const nextValue = event.target.value
          setQuery(nextValue)
          onChange(nextValue)
          setOpen(true)
        }}
        onKeyDown={handleKeyDown}
      />

      {open && (
        <div className="field-autocomplete-menu" ref={menuRef}>
          {loading && <div className="field-picker-empty">Loading fields…</div>}
          {!loading && resolution.error && (
            <div className="field-picker-empty">{resolution.error}</div>
          )}
          {!loading && !resolution.error && resolution.suggestions.length === 0 && (
            <div className="field-picker-empty">No matching fields.</div>
          )}
          {!loading && !resolution.error && resolution.suggestions
            .filter((s) => !excludeFields?.includes(s.path))
            .map((suggestion, index) => (
              <button
                key={suggestion.path}
                type="button"
                className={`field-picker-item${index === highlightedIndex ? ' highlighted' : ''}`}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setHighlightedIndex(index)}
                onClick={() => handleSuggestionClick(suggestion)}
              >
                <span className="field-picker-item-path">{suggestion.path}</span>
                {(suggestion.isRelation ||
                  suggestion.displayLabel.trim().toLowerCase() !==
                    suggestion.path.replace(/__/g, ' ').trim().toLowerCase()) && (
                  <span className="field-picker-item-meta">
                    {suggestion.isRelation
                      ? `Relation → ${suggestion.relatedModel}`
                      : suggestion.displayLabel}
                  </span>
                )}
              </button>
            ))}
        </div>
      )}
    </div>
  )
}

interface ConditionRowProps {
  node: FilterConditionNode
  rootModel: string
  rootAppLabel?: string
  rootMetadata?: MetadataResponse
  onChange: (id: string, patch: Partial<FilterConditionNode>) => void
  onRemove: (id: string) => void
  onRequestMetadata: (modelName: string, appLabel?: string) => Promise<MetadataResponse>
}

function operationsForField(metadata: MetadataResponse | undefined, fieldPath: string, currentOp: string) {
  const known = metadata?.fields.find((field) => field.name === fieldPath)?.allowed_operations
  // Paths deeper than the loaded metadata are validated by the backend.
  const ops: string[] = known && known.length > 0 ? [...known] : [...ALL_OPERATIONS]
  if (currentOp && !ops.includes(currentOp)) {
    ops.push(currentOp)
  }
  return ops
}

function fieldTypeOf(metadata: MetadataResponse | undefined, fieldPath: string) {
  return metadata?.fields.find((field) => field.name === fieldPath)?.type
}

const ConditionRow: React.FC<ConditionRowProps> = ({
  node,
  rootModel,
  rootAppLabel,
  rootMetadata,
  onChange,
  onRemove,
  onRequestMetadata,
}) => {
  const fieldType = fieldTypeOf(rootMetadata, node.field)
  const problem = node.value.trim() ? validateTypedInput(fieldType, node.op, node.value) : null
  const booleanChoice = isBooleanType(fieldType) && (node.op === 'is' || node.op === 'is_not')
  return (
  <div className="filter-row">
    <FieldAutocomplete
      value={node.field}
      rootModel={rootModel}
      rootAppLabel={rootAppLabel}
      rootMetadata={rootMetadata}
      onChange={(field) => onChange(node.id, { field })}
      onRequestMetadata={onRequestMetadata}
      className="filter-field-autocomplete"
      compact
    />

    <Dropdown
      className="dropdown-op"
      value={node.op}
      options={operationsForField(rootMetadata, node.field, node.op).map((operation) => ({
        value: operation,
        label: operationLabel(operation),
      }))}
      onChange={(op) => onChange(node.id, { op })}
    />

    {booleanChoice ? (
      <Dropdown
        className="dropdown-op fval-bool"
        value={node.value === 'false' ? 'false' : node.value === 'true' ? 'true' : ''}
        options={[
          ...(node.value === 'true' || node.value === 'false' ? [] : [{ value: '', label: 'choose…' }]),
          { value: 'true', label: 'true' },
          { value: 'false', label: 'false' },
        ]}
        onChange={(value) => onChange(node.id, { value })}
      />
    ) : (
      <input
        className={`fval${problem ? ' invalid' : ''}`}
        type="text"
        inputMode={
          fieldType && ['integer', 'bigint', 'smallint', 'positiveint'].includes(fieldType) && !['between', 'in', 'not_in'].includes(node.op)
            ? 'numeric'
            : undefined
        }
        placeholder={typedPlaceholder(fieldType, node.op)}
        value={node.value}
        aria-invalid={Boolean(problem)}
        title={problem ? `${node.field} ${problem}` : undefined}
        onChange={(event) => onChange(node.id, { value: event.target.value })}
      />
    )}

    <button
      className="remove-btn"
      type="button"
      onClick={() => onRemove(node.id)}
      title="Remove filter"
    >
      ×
    </button>
    {problem && <div className="fval-error">{node.field} {problem}</div>}
  </div>
  )
}

interface FilterGroupEditorProps {
  node: FilterGroupNode
  depth?: number
  isRoot?: boolean
  fallbackField: string
  rootModel: string
  rootAppLabel?: string
  rootMetadata?: MetadataResponse
  onUpdateGroup: (id: string, patch: Partial<FilterGroupNode>) => void
  onUpdateCondition: (id: string, patch: Partial<FilterConditionNode>) => void
  onAddCondition: (groupId: string) => void
  onAddGroup: (groupId: string) => void
  onRemoveNode: (id: string) => void
  onRequestMetadata: (modelName: string, appLabel?: string) => Promise<MetadataResponse>
}

const FilterGroupEditor: React.FC<FilterGroupEditorProps> = ({
  node,
  depth = 0,
  isRoot = false,
  fallbackField,
  rootModel,
  rootAppLabel,
  rootMetadata,
  onUpdateGroup,
  onUpdateCondition,
  onAddCondition,
  onAddGroup,
  onRemoveNode,
  onRequestMetadata,
}) => (
  <div className={`filter-group depth-${depth}`}>
    <div className="filter-group-head">
      <span className="filter-kw where">{isRoot ? 'WHERE' : 'GROUP'}</span>
      <Dropdown
        className="dropdown-group-op"
        value={node.operator}
        options={[
          { value: 'and', label: 'AND' },
          { value: 'or', label: 'OR' },
        ]}
        onChange={(operator) => onUpdateGroup(node.id, { operator })}
      />
      <button
        className="btn btn-ghost mini"
        type="button"
        title="Add a filter condition to this group"
        onClick={() => onAddCondition(node.id)}
      >
        + Rule
      </button>
      <button
        className="btn btn-ghost mini"
        type="button"
        title="Add a nested filter group (AND / OR)"
        onClick={() => onAddGroup(node.id)}
      >
        + Group
      </button>
      {!isRoot && (
        <button
          className="remove-btn"
          type="button"
          onClick={() => onRemoveNode(node.id)}
          title="Remove group"
        >
          ×
        </button>
      )}
    </div>
    <div className="filter-group-body">
      {node.children.length === 0 && (
        <div className="field-hint">No rules in this group.</div>
      )}
      {node.children.map((child) =>
        child.type === 'group' ? (
          <FilterGroupEditor
            key={child.id}
            node={child}
            depth={depth + 1}
            fallbackField={fallbackField}
            rootModel={rootModel}
            rootAppLabel={rootAppLabel}
            rootMetadata={rootMetadata}
            onUpdateGroup={onUpdateGroup}
            onUpdateCondition={onUpdateCondition}
            onAddCondition={onAddCondition}
            onAddGroup={onAddGroup}
            onRemoveNode={onRemoveNode}
            onRequestMetadata={onRequestMetadata}
          />
        ) : (
          <ConditionRow
            key={child.id}
            node={child}
            rootModel={rootModel}
            rootAppLabel={rootAppLabel}
            rootMetadata={rootMetadata}
            onChange={onUpdateCondition}
            onRemove={onRemoveNode}
            onRequestMetadata={onRequestMetadata}
          />
        ),
      )}
      {node.children.length === 0 && fallbackField && (
        <button
          className="btn btn-ghost mini"
          type="button"
          onClick={() => onAddCondition(node.id)}
        >
          Add first rule
        </button>
      )}
    </div>
  </div>
)

const formatCell = (value: unknown) =>
  value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)

const ResultsTable: React.FC<{
  rows: ResultRow[]
  selectedRow: number | null
  onRowSelect: (index: number) => void
  sortField: string
  sortDir: 'asc' | 'desc'
  onSort: (field: string) => void
}> = ({ rows, selectedRow, onRowSelect, sortField, sortDir, onSort }) => {
  if (rows.length === 0) {
    return <div className="empty-state">No results</div>
  }

  const columns = Object.keys(rows[0])

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                className={sortField === column ? 'sorted' : ''}
                onClick={() => onSort(column)}
              >
                {column} {sortField === column ? (sortDir === 'asc' ? '↑' : '↓') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={index}
              className={selectedRow === index ? 'selected' : ''}
              onClick={() => onRowSelect(index)}
            >
              {columns.map((column) => (
                <td
                  key={column}
                  className={
                    column === 'id'
                      ? 'td-id'
                      : column === 'name'
                        ? 'td-name'
                        : 'td-plain'
                  }
                >
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const buildPaginationItems = (currentPage: number, totalPages: number) => {
  if (totalPages <= 1) {
    return [1] as Array<number | 'ellipsis'>
  }

  const pages = new Set<number>([1, totalPages])
  for (
    let page = Math.max(1, currentPage - 2);
    page <= Math.min(totalPages, currentPage + 2);
    page += 1
  ) {
    pages.add(page)
  }

  const sortedPages = Array.from(pages).sort((left, right) => left - right)
  const items: Array<number | 'ellipsis'> = []

  sortedPages.forEach((page, index) => {
    const previousPage = sortedPages[index - 1]
    if (previousPage && page - previousPage > 1) {
      items.push('ellipsis')
    }
    items.push(page)
  })

  return items
}

interface QueriesPageProps {
  activeModel: string
  activeModelLabel?: string
  activeAppLabel?: string
  activeMetadata?: MetadataResponse
  metadataLoading?: boolean
  defaultPageSize: number
  queryPreset?: QueryRequest | null
  resultsPreset?: QueryResponse | null
  onPresetApplied?: () => void
  onResultsPresetApplied?: () => void
  onSaveQuery?: (input: {
    name: string
    description: string
    payload: QueryRequest
  }) => Promise<void>
  onRequestMetadata: (modelName: string, appLabel?: string) => Promise<MetadataResponse>
  onRunQuery: (payload: QueryRequest) => Promise<QueryResponse>
  onExportQuery?: (payload: QueryRequest, format: ExportFormat) => Promise<ExportResult>
  onShareQuery?: (payload: QueryRequest) => Promise<string>
}

export const QueriesPage: React.FC<QueriesPageProps> = ({
  activeModel,
  activeModelLabel,
  activeAppLabel,
  activeMetadata,
  metadataLoading = false,
  defaultPageSize,
  queryPreset,
  resultsPreset,
  onPresetApplied,
  onResultsPresetApplied,
  onSaveQuery,
  onRequestMetadata,
  onRunQuery,
  onExportQuery,
  onShareQuery,
}) => {
  const directFields = useMemo(() => getDirectFields(activeMetadata), [activeMetadata])
  const directFieldNames = useMemo(
    () => directFields.map((field) => field.name),
    [directFields],
  )
  const validLookupNames = useMemo(
    () => new Set(activeMetadata?.all_lookups || directFieldNames),
    [activeMetadata, directFieldNames],
  )
  // all_lookups only goes as deep as the metadata relation depth; deeper paths
  // (e.g. from saved queries) are still valid if they start at a direct field —
  // the backend validates the rest. Dropping them silently changed queries.
  const isKnownPath = useMemo(() => {
    const direct = new Set(directFieldNames)
    return (path: string) =>
      validLookupNames.has(path) || (path.includes('__') && direct.has(path.split('__')[0]))
  }, [directFieldNames, validLookupNames])
  const fallbackField = directFieldNames[0] || 'id'
  const hasAppliedPreset = useRef(false)

  const [filters, setFilters] = useState<FilterGroupNode>(() => {
    const urlFilters = readUrlFilters()
    if (urlFilters) {
      try {
        return deserializeFilters(urlFilters, fallbackField)
      } catch {
        // malformed link — fall back to an empty filter group below
      }
    }
    return newGroup(fallbackField, 'and')
  })
  const [limit, setLimit] = useState(defaultPageSize || 100)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resultTab, setResultTab] = useState<ResultTab>('table')
  const [selectedRow, setSelectedRow] = useState<number | null>(null)
  // sortField === '' means "keep the server order".
  const [sortField, setSortField] = useState('')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [orderBy, setOrderBy] = useState<string[]>([])
  const [results, setResults] = useState<QueryResponse | null>(null)
  const [selectedFields, setSelectedFields] = useState<string[]>(() => {
    const urlFields = new URLSearchParams(window.location.search).get('fields')
    return urlFields ? urlFields.split(',').filter(Boolean) : []
  })
  const [aggregations, setAggregations] = useState<AggregationRow[]>([])
  const [havingRows, setHavingRows] = useState<HavingRow[]>([])
  const [exportState, setExportState] = useState<'idle' | 'working' | 'done'>('idle')
  const [notice, setNotice] = useState<string | null>(null)
  const lastRunPayload = useRef<QueryRequest | null>(null)
  const [isFieldPickerOpen, setIsFieldPickerOpen] = useState(false)
  const [fieldSearch, setFieldSearch] = useState('')
  const [copyState, setCopyState] = useState<'idle' | 'done' | 'error'>('idle')
  const [csvState, setCsvState] = useState<'idle' | 'done'>('idle')
  const [shareState, setShareState] = useState<'idle' | 'working' | 'done' | 'error'>('idle')
  const [lastRunAt, setLastRunAt] = useState<Date | null>(null)
  const [relativeTime, setRelativeTime] = useState('')
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [saveDescription, setSaveDescription] = useState('')
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved'>('idle')
  const [savedQueryId, setSavedQueryId] = useState<number | null>(null)
  const [presetTitle, setPresetTitle] = useState<string | null>(null)
  const fieldPickerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setError(null)
  }, [activeModel])

  useEffect(() => {
    // While metadata is still loading, activeMetadata is undefined and directFieldNames is
    // transiently empty — leave any URL-restored filters/fields alone until it resolves.
    if (!activeMetadata) {
      return
    }

    if (!directFieldNames.length) {
      setFilters(newGroup('', 'and'))
      setSelectedFields([])
      return
    }

    setSelectedFields((current) => {
      const next = current.filter((field) => isKnownPath(field))
      if (next.length > 0) {
        return next
      }
      return directFieldNames.slice(0, Math.min(directFieldNames.length, 5))
    })

    setFilters((current) => {
      const normalizeNode = (node: FilterNode): FilterNode => {
        if (node.type === 'group') {
          return { ...node, children: node.children.map((child) => normalizeNode(child)) }
        }
        return {
          ...node,
          field: node.field && isKnownPath(node.field) ? node.field : fallbackField,
        }
      }
      return normalizeNode(current) as FilterGroupNode
    })
  }, [activeModel, activeMetadata, directFieldNames, fallbackField, isKnownPath])

  useEffect(() => {
    if (selectedFields.length === 0) return
    setUrlParam('fields', selectedFields.join(','))
  }, [selectedFields])

  useEffect(() => {
    const serialized = serializeFilters(filters)
    const encoded = serialized ? encodeBase64Url(JSON.stringify(serialized)) : ''
    setUrlParam('filters', encoded && encoded.length <= MAX_URL_FILTER_LENGTH ? encoded : null)
  }, [filters])

  useEffect(() => {
    if (!lastRunAt) return
    const update = () => setRelativeTime(getRelativeTime(lastRunAt))
    update()
    const id = window.setInterval(update, 30_000)
    return () => window.clearInterval(id)
  }, [lastRunAt])

  useEffect(() => {
    if (!queryPreset || hasAppliedPreset.current) {
      return
    }

    hasAppliedPreset.current = true
    setSavedQueryId(queryPreset.saved_query_id ?? null)
    setPresetTitle(queryPreset.title ?? null)
    setFilters(deserializeFilters(queryPreset.filter_fields, fallbackField))
    setLimit(queryPreset.page_size || defaultPageSize || 100)
    setAggregations(
      (queryPreset.aggregations || []).map((agg) => ({
        id: newId(),
        field: agg.field || '',
        function: agg.function,
        distinct: Boolean(agg.distinct),
        alias: agg.alias || '',
      })),
    )
    const presetHaving: HavingRow[] = []
    visitConditions(queryPreset.having, (condition) => {
      presetHaving.push({
        id: newId(),
        alias: condition.field,
        op: condition.op,
        value: valueToInput(condition.value),
      })
    })
    setHavingRows(presetHaving)
    setOrderBy(queryPreset.order_by || [])
    setSortField('')
    setSelectedFields(
      queryPreset.select_fields?.length
        ? queryPreset.select_fields
        : directFieldNames.slice(0, Math.min(directFieldNames.length, 5)),
    )

    if (resultsPreset) {
      setResults(resultsPreset)
      lastRunPayload.current = queryPreset
      setLastRunAt(new Date())
      setSelectedRow(null)
      onPresetApplied?.()
      onResultsPresetApplied?.()
      hasAppliedPreset.current = false
      return
    }

    void handleRunQuery({
      page: queryPreset.page || 1,
      explicitPayload: queryPreset,
    }).finally(() => {
      onPresetApplied?.()
      hasAppliedPreset.current = false
    })
  }, [
    defaultPageSize,
    directFieldNames,
    fallbackField,
    onPresetApplied,
    onResultsPresetApplied,
    queryPreset,
    resultsPreset,
  ])

  useEffect(() => {
    if (!isFieldPickerOpen) {
      return
    }

    const handlePointerDown = (event: PointerEvent) => {
      if (!fieldPickerRef.current?.contains(event.target as Node)) {
        setIsFieldPickerOpen(false)
      }
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsFieldPickerOpen(false)
      }
    }

    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isFieldPickerOpen])

  const updateNode = (
    node: FilterNode,
    id: string,
    updater: (target: FilterNode) => FilterNode,
  ): FilterNode => {
    if (node.id === id) {
      return updater(node)
    }
    if (node.type === 'group') {
      return {
        ...node,
        children: node.children.map((child) => updateNode(child, id, updater)),
      }
    }
    return node
  }

  const removeNode = (node: FilterNode, id: string): FilterNode | null => {
    if (node.id === id) {
      return null
    }
    if (node.type === 'group') {
      return {
        ...node,
        children: node.children
          .map((child) => removeNode(child, id))
          .filter((child): child is FilterNode => child !== null),
      }
    }
    return node
  }

  const appendToGroup = (node: FilterNode, groupId: string, child: FilterNode): FilterNode => {
    if (node.type === 'group' && node.id === groupId) {
      return { ...node, children: [...node.children, child] }
    }
    if (node.type === 'group') {
      return {
        ...node,
        children: node.children.map((current) => appendToGroup(current, groupId, child)),
      }
    }
    return node
  }

  const handleConditionChange = (id: string, patch: Partial<FilterConditionNode>) => {
    setFilters((current) =>
      updateNode(current, id, (node) =>
        node.type === 'condition' ? { ...node, ...patch } : node,
      ) as FilterGroupNode,
    )
  }

  const handleGroupChange = (id: string, patch: Partial<FilterGroupNode>) => {
    setFilters((current) =>
      updateNode(current, id, (node) =>
        node.type === 'group' ? { ...node, ...patch } : node,
      ) as FilterGroupNode,
    )
  }

  const handleAddConditionToGroup = (groupId: string) => {
    if (!fallbackField) {
      return
    }
    setFilters((current) =>
      appendToGroup(current, groupId, newCondition(fallbackField)) as FilterGroupNode,
    )
  }

  const handleAddGroupToGroup = (groupId: string) => {
    if (!fallbackField) {
      return
    }
    setFilters((current) =>
      appendToGroup(current, groupId, newGroup(fallbackField, 'and')) as FilterGroupNode,
    )
  }

  const handleRemoveFilterNode = (id: string) => {
    setFilters((current) => (removeNode(current, id) as FilterGroupNode) || newGroup(fallbackField, 'and'))
  }

  const toggleField = (field: string) => {
    setSelectedFields((current) => {
      if (current.includes(field)) {
        if (current.length === 1) {
          return current
        }
        return current.filter((item) => item !== field)
      }
      return [...current, field]
    })
  }

  const availableFields = selectedFields.length < validLookupNames.size

  const hasIncompleteFilters = useMemo(() => {
    const walk = (node: FilterNode): boolean => {
      if (node.type === 'condition') {
        return !node.field.trim() || !node.value.trim()
      }
      return node.children.some((child) => walk(child))
    }

    return walk(filters)
  }, [filters])

  // count/sum/avg results are numbers; min/max have the type of their field.
  const havingTypeOf = (alias: string) => {
    const agg = aggregations.find((row) => aggregationAlias(row) === alias)
    if (!agg) return undefined
    if (agg.function === 'count') return 'integer'
    if (agg.function === 'sum' || agg.function === 'avg') return 'float'
    return fieldTypeOf(activeMetadata, agg.field.trim())
  }

  const filterProblem = useMemo(() => {
    let problem: string | null = null
    const walk = (node: FilterNode) => {
      if (problem) return
      if (node.type === 'condition') {
        const issue = validateTypedInput(fieldTypeOf(activeMetadata, node.field), node.op, node.value)
        if (issue && node.value.trim()) problem = `${node.field} ${issue}`
        return
      }
      node.children.forEach(walk)
    }
    walk(filters)
    havingRows.forEach((row) => {
      if (problem || !row.value.trim()) return
      const issue = validateTypedInput(havingTypeOf(row.alias), row.op, row.value)
      if (issue) problem = `${row.alias} ${issue}`
    })
    return problem
  }, [filters, havingRows, activeMetadata, aggregations])

  const handleSort = (field: string) => {
    const nextDir: 'asc' | 'desc' = sortField === field && sortDir === 'asc' ? 'desc' : 'asc'
    const multiPage = Boolean(results && (results.next !== null || results.previous !== null))

    if (!multiPage) {
      // Everything is on screen — sort locally.
      setSortField(field)
      setSortDir(nextDir)
      return
    }

    // Results span several pages: sorting only the visible page would be
    // misleading, so ask the backend to order the whole result set.
    const previousOrder = orderBy
    const nextOrder = [`${nextDir === 'desc' ? '-' : ''}${field}`]
    setOrderBy(nextOrder)
    void handleRunQuery({
      page: 1,
      overrides: { order_by: nextOrder },
      keepResultsOnError: true,
      continueLast: true,
    }).then((ok) => {
      if (ok) {
        setSortField(field)
        setSortDir(nextDir)
      } else {
        setOrderBy(previousOrder)
      }
    })
  }

  const isAggregating = aggregations.some((row) => row.field.trim() || row.function === 'count')

  // Grouping by a unique field (pk or unique=True) makes every group a single
  // row — almost never what someone building an aggregation wants.
  const groupsByUniqueField = useMemo(() => {
    const pk = activeMetadata?.primary_key_field || 'id'
    return (
      selectedFields.find(
        (field) =>
          field === pk ||
          activeMetadata?.fields.some((meta) => meta.name === field && (meta.unique || meta.primary_key)),
      ) || null
    )
  }, [activeMetadata, selectedFields])

  const activeAliases = useMemo(
    () => aggregations.filter((row) => row.field.trim() || row.function === 'count').map(aggregationAlias),
    [aggregations],
  )

  const addHavingRow = () => {
    setHavingRows((current) => [
      ...current,
      { id: newId(), alias: activeAliases[0] || '', op: 'gt', value: '' },
    ])
  }

  const updateHavingRow = (id: string, patch: Partial<HavingRow>) => {
    setHavingRows((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)))
  }

  const removeHavingRow = (id: string) => {
    setHavingRows((current) => current.filter((row) => row.id !== id))
  }

  const addAggregation = () => {
    setAggregations((current) => [
      ...current,
      { id: newId(), field: fallbackField, function: 'count', distinct: false, alias: '' },
    ])
  }

  const updateAggregation = (id: string, patch: Partial<AggregationRow>) => {
    setAggregations((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)))
  }

  const removeAggregation = (id: string) => {
    setAggregations((current) => current.filter((row) => row.id !== id))
  }

  const buildPayload = (page: number, overrides: Partial<QueryRequest> = {}): QueryRequest => {
    const activeAggregations = aggregations.filter(
      (row) => row.field.trim() || row.function === 'count',
    )
    const havingConditions = activeAggregations.length
      ? havingRows
          .filter((row) => row.alias && row.value.trim())
          .map((row) => ({
            field: row.alias,
            op: row.op,
            value: inputToValue(row.op, row.value),
          }))
      : []
    return {
      model: activeModel,
      ...(activeAppLabel ? { app_label: activeAppLabel } : {}),
      select_fields: selectedFields.length > 0 ? selectedFields : ['id'],
      filter_fields: serializeFilters(filters),
      ...(activeAggregations.length
        ? {
            aggregations: activeAggregations.map((row) => ({
              ...(row.field.trim() ? { field: row.field.trim() } : {}),
              function: row.function,
              distinct: row.distinct,
              ...(row.alias.trim() ? { alias: row.alias.trim() } : {}),
            })),
          }
        : {}),
      ...(havingConditions.length ? { having: { and_operation: havingConditions } } : {}),
      ...(orderBy.length ? { order_by: orderBy } : {}),
      page,
      page_size: limit,
      title: presetTitle || `${activeModelLabel || activeModel} query`,
      ...(savedQueryId ? { saved_query_id: savedQueryId } : {}),
      ...overrides,
    }
  }

  const openSaveDialog = () => {
    setSaveName(presetTitle || `${activeModelLabel || activeModel} query`)
    setSaveDescription('')
    setSaveState('idle')
    setSaveDialogOpen(true)
  }

  const closeSaveDialog = () => {
    setSaveDialogOpen(false)
    setSaveState('idle')
  }

  const handleRunQuery = async ({
    page = 1,
    explicitPayload,
    overrides,
    keepResultsOnError = false,
    continueLast = false,
  }: {
    page?: number
    explicitPayload?: QueryRequest
    overrides?: Partial<QueryRequest>
    keepResultsOnError?: boolean
    /** Page/sort the query whose results are on screen instead of the builder state. */
    continueLast?: boolean
  } = {}): Promise<boolean> => {
    const continuing = continueLast && lastRunPayload.current !== null
    if (!explicitPayload && !continuing && hasIncompleteFilters) {
      setError('Every filter rule needs a field and a value.')
      return false
    }
    if (!explicitPayload && !continuing && filterProblem) {
      setError(filterProblem)
      return false
    }

    setIsLoading(true)
    setError(null)
    setNotice(null)

    // Paging through a replayed/preset query must keep its exact payload.
    // Paging/sorting continues the query whose results are on screen, even if
    // the builder has been edited since.
    const basePayload =
      explicitPayload ||
      (continuing && lastRunPayload.current
        ? { ...lastRunPayload.current, page, ...overrides }
        : buildPayload(page, overrides))

    try {
      const response = await onRunQuery(basePayload)
      lastRunPayload.current = basePayload
      setResults(response)
      setLastRunAt(new Date())
      setSelectedRow(null)
      if (!overrides?.order_by) {
        // New data arrives in server order — don't re-sort it client-side.
        setSortField('')
      }
      return true
    } catch (runError) {
      if (!keepResultsOnError) {
        setResults(null)
      }
      setError(runError instanceof Error ? runError.message : 'Query failed.')
      return false
    } finally {
      setIsLoading(false)
    }
  }

  const handleCopyJson = async () => {
    if (!results) {
      return
    }

    try {
      await navigator.clipboard.writeText(JSON.stringify(results.results, null, 2))
      setCopyState('done')
      window.setTimeout(() => setCopyState('idle'), 1600)
    } catch {
      setCopyState('error')
      window.setTimeout(() => setCopyState('idle'), 2000)
    }
  }

  const handleExportCsv = () => {
    if (!results || results.results.length === 0) {
      return
    }

    const columns = Array.from(new Set(results.results.flatMap((row) => Object.keys(row))))

    const escapeCsvValue = (value: unknown) => {
      let normalized =
        value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)
      // Neutralise spreadsheet formulas in text cells (CSV injection).
      if (typeof value === 'string' && /^[=+\-@\t\r]/.test(normalized)) {
        normalized = `'${normalized}`
      }
      if (/[",\r\n]/.test(normalized)) {
        return `"${normalized.replace(/"/g, '""')}"`
      }
      return normalized
    }

    const csvLines = [
      columns.join(','),
      ...results.results.map((row) =>
        columns.map((column) => escapeCsvValue(row[column])).join(','),
      ),
    ]

    const blob = new Blob(['\ufeff' + csvLines.join('\n')], {
      type: 'text/csv;charset=utf-8;',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${activeModel.toLowerCase()}-${new Date()
      .toISOString()
      .slice(0, 19)
      .replace(/[:T]/g, '-')}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
    setCsvState('done')
    window.setTimeout(() => setCsvState('idle'), 1600)
  }

  const downloadBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const handleExportAll = async (format: ExportFormat = 'csv') => {
    const payload = lastRunPayload.current
    if (!onExportQuery || !payload) {
      return
    }
    setExportState('working')
    setError(null)
    setNotice(null)
    try {
      const exported = await onExportQuery(payload, format)
      downloadBlob(exported.blob, exported.filename)
      if (exported.truncated) {
        setNotice(
          `Export limited to ${exported.exportedCount?.toLocaleString()} of ${exported.totalCount?.toLocaleString()} rows (MAX_EXPORT_ROWS).`,
        )
      }
      setExportState('done')
      window.setTimeout(() => setExportState('idle'), 1600)
    } catch (exportError) {
      setExportState('idle')
      setError(exportError instanceof Error ? exportError.message : 'Export failed.')
    }
  }

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Clipboard API is unavailable on insecure origins (plain http) — fall
      // back to a prompt the user can copy from.
      window.prompt('Copy this link:', text)
      return true
    }
  }

  const handleShare = async () => {
    if (filterProblem) {
      setError(filterProblem)
      return
    }
    setShareState('working')
    try {
      let link = window.location.href
      if (onShareQuery) {
        const { title: _title, saved_query_id: _savedId, ...payload } = buildPayload(1)
        const token = await onShareQuery(payload)
        const params = new URLSearchParams()
        params.set('model', activeAppLabel ? `${activeAppLabel}::${activeModel}` : activeModel)
        params.set('share', token)
        link = `${window.location.origin}${window.location.pathname}?${params.toString()}`
      }
      await copyText(link)
      setShareState('done')
      window.setTimeout(() => setShareState('idle'), 1600)
    } catch (shareError) {
      setShareState('error')
      setError(shareError instanceof Error ? shareError.message : 'Could not create share link.')
      window.setTimeout(() => setShareState('idle'), 2000)
    }
  }

  const rows = (results?.results || []) as ResultRow[]
  const displayModelLabel = activeModelLabel || activeModel
  const sortedResults = !sortField ? rows : [...rows].sort((left, right) => {
    const leftValue = left[sortField] ?? ''
    const rightValue = right[sortField] ?? ''
    const comparison = String(leftValue).localeCompare(String(rightValue), undefined, {
      numeric: true,
    })
    return sortDir === 'asc' ? comparison : -comparison
  })
  const paginationItems = useMemo(
    () =>
      buildPaginationItems(
        results?.page || 1,
        results?.total_pages ?? (results ? results.page + (results.next !== null ? 1 : 0) : 1),
      ),
    [results?.page, results?.total_pages, results?.next],
  )

  return (
    <div className="tab-panel active queries-page">
      <div className="animate-in">
        <div className="page-title-row">
          <h1 className="page-title">Queries</h1>
        </div>
      </div>

      <div className="card animate-in query-builder-card">
        <div className="card-header">
          <span className="card-title">
            {displayModelLabel}
          </span>
        </div>
        <div className="card-body">
          <ResourceRow activeModel={activeModel} activeModelLabel={activeModelLabel} />

          <div className="field-selector">
            <span
              className="from-label"
              title={isAggregating ? 'With aggregations, these fields define the groups (SQL GROUP BY).' : undefined}
            >
              {isAggregating ? 'GROUP BY' : 'SELECT'}
            </span>
            {metadataLoading && <span className="field-hint">Loading fields…</span>}
            {!metadataLoading && directFieldNames.length === 0 && (
              <span className="field-hint">No fields available.</span>
            )}
            {!metadataLoading &&
              selectedFields.map((field) => (
                <button
                  key={field}
                  type="button"
                  className="resource-chip active"
                  onClick={() => toggleField(field)}
                >
                  {field} ×
                </button>
              ))}
            {!metadataLoading && directFieldNames.length > 0 && (
              <div className="field-picker-wrap">
                <button
                  type="button"
                  className="resource-chip add field-selector-trigger"
                  disabled={!availableFields}
                  onClick={() => {
                    setFieldSearch('')
                    setIsFieldPickerOpen((current) => !current)
                  }}
                >
                  {availableFields ? '+ Add Field' : 'All Fields Added'}
                </button>
                {isFieldPickerOpen && availableFields && (
                  <div className="field-picker-popover" ref={fieldPickerRef}>
                    <div className="field-picker-popover-head">
                      <span className="field-picker-popover-kicker">Add field</span>
                      <button
                        type="button"
                        className="field-picker-close"
                        onMouseDown={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          setIsFieldPickerOpen(false)
                        }}
                        aria-label="Close field picker"
                      >
                        ×
                      </button>
                    </div>
                    <FieldAutocomplete
                      value={fieldSearch}
                      rootModel={activeModel}
                      rootAppLabel={activeAppLabel}
                      rootMetadata={activeMetadata}
                      onRequestMetadata={onRequestMetadata}
                      excludeFields={selectedFields}
                      onChange={setFieldSearch}
                      onSubmit={(path) => {
                        if (!selectedFields.includes(path)) {
                          toggleField(path)
                        }
                        setIsFieldPickerOpen(false)
                        setFieldSearch('')
                      }}
                      autoFocus
                      compact
                      className="field-picker-popover-autocomplete"
                    />
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="aggregation-section">
            <div className="aggregation-header">
              <span className="from-label">AGGREGATE</span>
              <button className="btn btn-ghost mini" type="button" onClick={addAggregation}>
                + Aggregation
              </button>
            </div>
            {aggregations.map((row) => (
              <div key={row.id} className="aggregation-row">
                <FieldAutocomplete
                  value={row.field}
                  rootModel={activeModel}
                  rootAppLabel={activeAppLabel}
                  rootMetadata={activeMetadata}
                  onChange={(field) => updateAggregation(row.id, { field })}
                  onRequestMetadata={onRequestMetadata}
                  className="aggregation-field-autocomplete"
                  compact
                />
                <Dropdown
                  className="dropdown-op"
                  value={row.function}
                  options={AGGREGATION_FUNCTIONS.map((fn) => ({ value: fn, label: fn }))}
                  onChange={(fn) => updateAggregation(row.id, { function: fn })}
                />
                {row.function === 'count' && (
                  <label className="aggregation-distinct">
                    <input
                      type="checkbox"
                      checked={row.distinct}
                      onChange={(event) => updateAggregation(row.id, { distinct: event.target.checked })}
                    />
                    distinct
                  </label>
                )}
                <input
                  className="aggregation-alias"
                  type="text"
                  placeholder={aggregationAlias({ ...row, alias: '' })}
                  title="Result column name (optional)"
                  value={row.alias}
                  onChange={(event) => updateAggregation(row.id, { alias: event.target.value })}
                />
                <button
                  className="remove-btn"
                  type="button"
                  onClick={() => removeAggregation(row.id)}
                  title="Remove aggregation"
                >
                  ×
                </button>
              </div>
            ))}
            {isAggregating && (
              <div className="field-hint">
                One result row per unique combination of{' '}
                <strong>{selectedFields.join(' + ') || '—'}</strong> (GROUP BY above). To count
                rows per group, use count with an empty field.
              </div>
            )}
            {isAggregating && groupsByUniqueField && (
              <div className="field-hint aggregation-warning">
                “{groupsByUniqueField}” is unique per row, so every row becomes its own group
                (count is always 1). Remove it from GROUP BY to aggregate across rows.
              </div>
            )}
            {activeAliases.length > 0 && (
              <div className="having-section">
                <div className="aggregation-header">
                  <span className="from-label" title="Filter groups by their aggregated values (SQL HAVING)">
                    HAVING
                  </span>
                  <button
                    className="btn btn-ghost mini"
                    type="button"
                    title="Filter on an aggregated value"
                    onClick={addHavingRow}
                  >
                    + Condition
                  </button>
                </div>
                {havingRows.map((row) => {
                  const aliasOptions = activeAliases.includes(row.alias) || !row.alias
                    ? activeAliases
                    : [...activeAliases, row.alias]
                  return (
                    <div key={row.id} className="filter-row having-row">
                      <Dropdown
                        className="dropdown-op having-alias"
                        value={row.alias}
                        options={aliasOptions.map((alias) => ({
                          value: alias,
                          label: activeAliases.includes(alias) ? alias : `${alias} (missing)`,
                        }))}
                        onChange={(alias) => updateHavingRow(row.id, { alias })}
                      />
                      <Dropdown
                        className="dropdown-op"
                        value={row.op}
                        options={HAVING_OPERATIONS.map((op) => ({ value: op as string, label: operationLabel(op) }))}
                        onChange={(op) => updateHavingRow(row.id, { op })}
                      />
                      <input
                        className={`fval${row.value.trim() && validateTypedInput(havingTypeOf(row.alias), row.op, row.value) ? ' invalid' : ''}`}
                        type="text"
                        placeholder={typedPlaceholder(havingTypeOf(row.alias), row.op)}
                        value={row.value}
                        title={(row.value.trim() && validateTypedInput(havingTypeOf(row.alias), row.op, row.value)) || undefined}
                        onChange={(event) => updateHavingRow(row.id, { value: event.target.value })}
                      />
                      <button
                        className="remove-btn"
                        type="button"
                        onClick={() => removeHavingRow(row.id)}
                        title="Remove condition"
                      >
                        ×
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <div className="filter-rows">
            <FilterGroupEditor
              node={filters}
              isRoot
              fallbackField={fallbackField}
              rootModel={activeModel}
              rootAppLabel={activeAppLabel}
              rootMetadata={activeMetadata}
              onUpdateGroup={handleGroupChange}
              onUpdateCondition={handleConditionChange}
              onAddCondition={handleAddConditionToGroup}
              onAddGroup={handleAddGroupToGroup}
              onRemoveNode={handleRemoveFilterNode}
              onRequestMetadata={onRequestMetadata}
            />
          </div>

          <div className="qactions">
            <button
              className="btn btn-primary"
              title="Execute the query with the current fields and filters"
              onClick={() => void handleRunQuery({ page: 1 })}
              disabled={
                isLoading ||
                metadataLoading ||
                directFieldNames.length === 0 ||
                selectedFields.length === 0
              }
            >
              <IconPlay />
              {isLoading ? 'Running…' : 'Run Query'}
            </button>
            {onSaveQuery && (
              <button
                className="btn btn-secondary"
                title="Save the current query for later reuse"
                onClick={openSaveDialog}
                disabled={
                  metadataLoading ||
                  directFieldNames.length === 0 ||
                  selectedFields.length === 0
                }
              >
                <IconSave /> Save Query
              </button>
            )}
            <button
              className="btn btn-ghost"
              title="Copy a short link to this query (fields, filters, aggregations, sorting)"
              onClick={() => void handleShare()}
              disabled={directFieldNames.length === 0 || shareState === 'working'}
            >
              <IconShare />{' '}
              {shareState === 'working'
                ? 'Sharing…'
                : shareState === 'done'
                  ? 'Link copied!'
                  : shareState === 'error'
                    ? 'Share failed'
                    : 'Share'}
            </button>
            {filters.children.length > 0 && (
              <button
                className="btn btn-ghost"
                title="Remove all active filter rules"
                onClick={() => setFilters(newGroup(fallbackField, 'and'))}
              >
                Clear
              </button>
            )}
          </div>

          {error && <div className="query-error">{error}</div>}
          {notice && <div className="query-notice">{notice}</div>}
        </div>
      </div>

      <Dialog open={saveDialogOpen} onClose={closeSaveDialog} className="field-picker-overlay">
        <div className="field-picker-backdrop" aria-hidden="true" />
        <div className="field-picker-overlay-shell">
          <DialogPanel className="field-picker-modal save-query-modal">
            <div className="field-picker-modal-head">
              <div>
                <div className="field-picker-modal-kicker">{savedQueryId ? 'Update query' : 'Save query'}</div>
                <DialogTitle className="field-picker-modal-title">
                  {displayModelLabel}
                </DialogTitle>
              </div>
              <button
                type="button"
                className="field-picker-close"
                onPointerDown={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  closeSaveDialog()
                }}
                aria-label="Close save query dialog"
              >
                ×
              </button>
            </div>
            <div className="save-query-summary">{selectedFields.length} fields selected</div>
            <div className="save-query-dialog-grid">
              <input
                className="save-query-input"
                placeholder="Name"
                value={saveName}
                onChange={(event) => setSaveName(event.target.value)}
              />
              <textarea
                className="save-query-textarea"
                placeholder="Description"
                value={saveDescription}
                onChange={(event) => setSaveDescription(event.target.value)}
              />
            </div>
            <div className="save-query-actions">
              <div className="save-query-actions-right">
                <button className="btn btn-ghost" type="button" onClick={closeSaveDialog}>
                  Cancel
                </button>
                <button
                  className="btn btn-primary"
                  type="button"
                  disabled={!saveName.trim() || saveState === 'saving'}
                  onClick={async () => {
                    if (!onSaveQuery) {
                      return
                    }
                    setSaveState('saving')
                    try {
                      await onSaveQuery({
                        name: saveName.trim(),
                        description: saveDescription.trim(),
                        payload: buildPayload(1),
                      })
                      setSaveState('saved')
                      window.setTimeout(() => closeSaveDialog(), 300)
                    } catch {
                      setSaveState('idle')
                    }
                  }}
                >
                  {saveState === 'saving'
                    ? (savedQueryId ? 'Updating…' : 'Saving…')
                    : saveState === 'saved'
                      ? (savedQueryId ? 'Updated' : 'Saved')
                      : (savedQueryId ? 'Update Query' : 'Save Query')}
                </button>
              </div>
            </div>
          </DialogPanel>
        </div>
      </Dialog>

      {results && (
        <div
          className="card animate-in query-results-card"
        >
          <div className="results-meta">
            <span className="result-count">
              {results.count === null ? '—' : `${results.count_exact === false ? '~' : ''}${results.count.toLocaleString()}`}
            </span>
            <span className="result-count-label">{aggregations.length > 0 && results.count !== null ? 'groups' : 'results'}</span>
            <div className="timing-pill">
              <span className="dot">●</span> {results.page_size} / page
            </div>
            {orderBy.length > 0 && (
              <button
                className="timing-pill order-pill"
                type="button"
                title="Clear server-side sorting"
                onClick={() => {
                  setOrderBy([])
                  setSortField('')
                  void handleRunQuery({ page: 1, overrides: { order_by: [] }, keepResultsOnError: true, continueLast: true })
                }}
              >
                sorted by {orderBy.map((entry) => (entry.startsWith('-') ? `${entry.slice(1)} ↓` : `${entry} ↑`)).join(', ')} ×
              </button>
            )}
            {lastRunAt && (
              <div className={`timing-pill${Date.now() - lastRunAt.getTime() > 5 * 60_000 ? ' stale' : ''}`}>
                {relativeTime}
              </div>
            )}
            <div className="card-actions-right">
              <button className="btn btn-ghost" onClick={() => void handleCopyJson()} title="Copy JSON">
                <IconCopy />{' '}
                {copyState === 'done'
                  ? 'Copied'
                  : copyState === 'error'
                    ? 'Copy failed'
                    : 'JSON'}
              </button>
              <button className="btn btn-ghost" onClick={handleExportCsv} title="Download the rows on this page as CSV">
                <IconDownload /> {csvState === 'done' ? 'Downloaded' : 'Page CSV'}
              </button>
              {onExportQuery && (
                <button
                  className="btn btn-secondary"
                  onClick={() => void handleExportAll('csv')}
                  disabled={exportState === 'working' || !lastRunPayload.current}
                  title={`Download all ${results.count === null ? '' : `${results.count.toLocaleString()} `}results (every page) as CSV`}
                >
                  <IconDownload />{' '}
                  {exportState === 'working'
                    ? 'Exporting…'
                    : exportState === 'done'
                      ? 'Downloaded'
                      : 'Export all'}
                </button>
              )}
            </div>
          </div>

          <div className="result-tabs">
            <button
              className={`rtab${resultTab === 'table' ? ' active' : ''}`}
              onClick={() => setResultTab('table')}
            >
              Table
            </button>
            <button
              className={`rtab${resultTab === 'json' ? ' active' : ''}`}
              onClick={() => setResultTab('json')}
            >
              JSON
            </button>
          </div>

          {resultTab === 'table' && (
            <ResultsTable
              rows={sortedResults}
              selectedRow={selectedRow}
              onRowSelect={setSelectedRow}
              sortField={sortField}
              sortDir={sortDir}
              onSort={handleSort}
            />
          )}

          {resultTab === 'json' && (
            <div className="json-view">
              <pre>{JSON.stringify(results.results, null, 2)}</pre>
            </div>
          )}

          <div className="pagination">
            <button
              className="ppage ppage-nav"
              disabled={results.previous === null}
              onClick={() => void handleRunQuery({ page: results.page - 1, continueLast: true })}
            >
              Prev
            </button>
            {paginationItems.map((item, index) =>
              item === 'ellipsis' ? (
                <span key={`ellipsis-${index}`} className="ppage-ellipsis">
                  …
                </span>
              ) : (
                <button
                  key={item}
                  className={`ppage${results.page === item ? ' cur' : ''}`}
                  onClick={() => void handleRunQuery({ page: item, continueLast: true })}
                >
                  {item}
                </button>
              ),
            )}
            <button
              className="ppage ppage-nav"
              disabled={results.next === null}
              onClick={() => void handleRunQuery({ page: results.page + 1, continueLast: true })}
            >
              Next
            </button>
            <span className="page-summary">
              {results.results.length === 0
                ? '0'
                : `${(results.page - 1) * results.page_size + 1}–${(results.page - 1) * results.page_size + results.results.length}`}
              {results.count !== null ? ` of ${results.count.toLocaleString()}` : ''}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
