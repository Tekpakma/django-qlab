import { QueryCondition, QueryFilterGroup } from './api'

/** Every filter operation the backend understands. */
export const ALL_OPERATIONS = [
  'is',
  'is_not',
  'icontains',
  'istartswith',
  'iendswith',
  'lt',
  'lte',
  'gt',
  'gte',
  'between',
  'in',
  'not_in',
] as const

/** Operations allowed in HAVING conditions (on aggregated values). */
export const HAVING_OPERATIONS = ['is', 'is_not', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in'] as const

/** Operations whose value is a list instead of a single string. */
export const LIST_OPERATIONS = new Set(['in', 'not_in', 'between'])

export const OPERATION_LABELS: Record<string, string> = {
  is: 'is',
  is_not: 'is not',
  icontains: 'contains',
  istartswith: 'starts with',
  iendswith: 'ends with',
  lt: '<',
  lte: '≤',
  gt: '>',
  gte: '≥',
  between: 'between',
  in: 'in',
  not_in: 'not in',
}

export const operationLabel = (op: string) => OPERATION_LABELS[op] || op

/**
 * Placeholder for the value input of an operation. List operations are typed
 * as comma separated values in the UI.
 */
export const valuePlaceholder = (op: string) =>
  op === 'between' ? 'from, to' : LIST_OPERATIONS.has(op) ? 'a, b, c' : 'value…'

/** Convert an API condition value into the text shown in an input. */
export function valueToInput(value: QueryCondition['value'] | boolean | number | null | undefined): string {
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).join(', ')
  }
  return value == null ? '' : String(value)
}

/** Convert the text of an input into the value shape the operation needs. */
export function inputToValue(op: string, input: string): string | string[] {
  if (!LIST_OPERATIONS.has(op)) {
    return input
  }
  return input
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part.length > 0)
}

/** Return a user-facing problem with a value, or null when it is fine. */
export function validateInput(op: string, input: string): string | null {
  if (!input.trim()) {
    return 'needs a value'
  }
  if (op === 'between' && (inputToValue(op, input) as string[]).length !== 2) {
    return 'between needs exactly two values: from, to'
  }
  return null
}

/** Walk a filter tree and visit every condition (in document order). */
export function visitConditions(
  group: QueryFilterGroup | undefined,
  visit: (condition: QueryCondition, path: string) => void,
  path = 'root',
) {
  if (!group) return
  ;(['and_operation', 'or_operation', 'not_operation'] as const).forEach((key) => {
    group[key]?.forEach((child, index) => {
      const childPath = `${path}.${key}.${index}`
      if ('field' in child) {
        visit(child as QueryCondition, childPath)
      } else {
        visitConditions(child as QueryFilterGroup, visit, childPath)
      }
    })
  })
}

/** Return a copy of a filter tree with each condition passed through `map`. */
export function mapConditions(
  group: QueryFilterGroup | undefined,
  map: (condition: QueryCondition, path: string) => QueryCondition,
  path = 'root',
): QueryFilterGroup | undefined {
  if (!group) return group
  const next: QueryFilterGroup = { ...group }
  ;(['and_operation', 'or_operation', 'not_operation'] as const).forEach((key) => {
    const children = group[key]
    if (!children) return
    next[key] = children.map((child, index) => {
      const childPath = `${path}.${key}.${index}`
      return 'field' in child
        ? map(child as QueryCondition, childPath)
        : (mapConditions(child as QueryFilterGroup, map, childPath) as QueryFilterGroup)
    })
  })
  return next
}

// ─── URL-safe, UTF-8-safe base64 ─────────────────────────────────────────────
// btoa() throws on any non-Latin-1 character (ä, €, emoji …), which used to
// crash the query builder as soon as such a filter value was typed.

export function encodeBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function decodeBase64Url(encoded: string): string {
  const normalized = encoded.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
  const binary = atob(padded)
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
  return new TextDecoder().decode(bytes)
}

// ─── Type-aware value checks (mirrors the backend validation) ────────────────

const INTEGER_TYPES = new Set(['integer', 'bigint', 'smallint', 'positiveint'])
const DECIMAL_TYPES = new Set(['float', 'decimal'])
const TEXT_SEARCH_OPS = new Set(['icontains', 'istartswith', 'iendswith'])

const isInteger = (value: string) => /^[+-]?\d+$/.test(value)
const isDecimal = (value: string) => /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(value)
const isDate = (value: string) =>
  /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value))
const isDateTime = (value: string) =>
  isDate(value) ||
  (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/.test(value) &&
    !Number.isNaN(Date.parse(value.replace(' ', 'T'))))
const isTime = (value: string) => /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d+)?)?$/.test(value)
const isUuid = (value: string) =>
  /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i.test(value)

const TYPE_CHECKS: Array<{ types: Set<string>; check: (v: string) => boolean; expects: string; placeholder: string }> = [
  { types: INTEGER_TYPES, check: isInteger, expects: 'a whole number', placeholder: 'number…' },
  { types: DECIMAL_TYPES, check: isDecimal, expects: 'a number', placeholder: 'number…' },
  { types: new Set(['boolean']), check: (v) => v === 'true' || v === 'false', expects: 'true or false', placeholder: 'true / false' },
  { types: new Set(['datetime']), check: isDateTime, expects: 'a date like 2026-01-31 (optionally with time)', placeholder: 'YYYY-MM-DD[ HH:MM]' },
  { types: new Set(['date']), check: isDate, expects: 'a date like 2026-01-31', placeholder: 'YYYY-MM-DD' },
  { types: new Set(['time']), check: isTime, expects: 'a time like 13:45', placeholder: 'HH:MM' },
  { types: new Set(['uuid']), check: isUuid, expects: 'a UUID', placeholder: 'uuid…' },
]

const checkFor = (fieldType: string | undefined, op: string) =>
  fieldType && !TEXT_SEARCH_OPS.has(op)
    ? TYPE_CHECKS.find((entry) => entry.types.has(fieldType))
    : undefined

/** Placeholder for a value input, aware of the field type. */
export function typedPlaceholder(fieldType: string | undefined, op: string): string {
  const check = checkFor(fieldType, op)
  if (!check) return valuePlaceholder(op)
  if (op === 'between') return `${check.placeholder.replace('…', '')} from, to`
  if (LIST_OPERATIONS.has(op)) return `${check.placeholder.replace('…', '')}, …`
  return check.placeholder
}

/**
 * Validate an input against the operation and the field type.
 * Returns a user-facing message or null.
 */
export function validateTypedInput(fieldType: string | undefined, op: string, input: string): string | null {
  const shapeProblem = validateInput(op, input)
  if (shapeProblem) return shapeProblem
  const check = checkFor(fieldType, op)
  if (!check) return null
  const values = LIST_OPERATIONS.has(op) ? (inputToValue(op, input) as string[]) : [input.trim()]
  const bad = values.find((value) => !check.check(value))
  return bad === undefined ? null : `expects ${check.expects}, got '${bad}'`
}

/** Metadata type of the value a filter on `fieldType` compares against. */
export function isBooleanType(fieldType: string | undefined) {
  return fieldType === 'boolean'
}
