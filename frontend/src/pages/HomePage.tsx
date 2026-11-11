import React from 'react'
import './HomePage.css'

interface ReleaseEntry {
  version: string
  summary: string
}

interface HomeStats {
  totalModels: number
  totalApps: number
  queriesThisWeek: number
  savedQueriesCount: number
}

interface RoadmapItem {
  label: string
  status: 'In progress' | 'Planned'
}

const CURRENT_VERSION = 'v0.5.0'
const CURRENT_RELEASE_DATE = 'Nov 11, 2026'
const CURRENT_RELEASE_NOTES = [
  'Export all: download every result row as CSV in one go, not just the current page.',
  'HAVING: filter aggregated groups by their computed values (e.g. only groups with count ≥ 10). count no longer needs a field.',
  'Saved queries: edit filter values inline in the Saved tab without reopening the Query Builder.',
  'Share links are now short server-side links, so large queries can be shared too. They include aggregations and sorting.',
  'Sorting: order_by in the API; clicking a column header sorts the whole result set when it spans several pages.',
  'More filter operators (in, not in, between, starts with, ends with), offered per field type in the builder.',
  'Count modes (exact, estimate, none) for faster paging on large tables, plus a typeahead endpoint for field values.',
  'Fixed: grouping by a field other than the primary key returned one row per record instead of one per group.',
  'Fixed: invalid payloads now return a clear 400 error instead of a server error, and misspelled filter keys are rejected instead of silently ignored.',
  'Fixed: a custom get_queryset() now also applies to exports and saved-query runs (configure QUERY_VIEWSET).',
]

const PREVIOUS_RELEASES: ReleaseEntry[] = [
  { version: 'v0.4.x', summary: 'Aggregations, UI redesign, pagination and saved-query fixes.' },
  { version: 'v0.3.x', summary: 'Frontend integration, model metadata, and pagination fixes.' },
]

const ROADMAP: RoadmapItem[] = []

interface HomePageProps {
  stats: HomeStats
}

export const HomePage: React.FC<HomePageProps> = ({ stats }) => (
  <div className="home-page">
    <div>
      <div className="home-eyebrow">Current Release</div>
      <div className="home-card">
        <div className="home-release-head">
          <span className="home-release-version">{CURRENT_VERSION}</span>
          <span className="home-release-date">Released {CURRENT_RELEASE_DATE}</span>
        </div>
        <ul className="home-release-notes">
          {CURRENT_RELEASE_NOTES.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </div>
      <div className="home-history">
        {PREVIOUS_RELEASES.map((release) => (
          <div key={release.version} className="home-history-row">
            <span className="home-history-version">{release.version}</span>
            <span className="home-history-summary">{release.summary}</span>
          </div>
        ))}
      </div>
    </div>

    <div>
      <div className="home-eyebrow">This Environment</div>
      <div className="home-stats-grid">
        <div className="home-stat-card">
          <div className="home-stat-value">{stats.totalModels}</div>
          <div className="home-stat-label">Registered models</div>
        </div>
        <div className="home-stat-card">
          <div className="home-stat-value">{stats.totalApps}</div>
          <div className="home-stat-label">Registered apps</div>
        </div>
        <div className="home-stat-card">
          <div className="home-stat-value">{stats.queriesThisWeek}</div>
          <div className="home-stat-label">Queries run this week</div>
        </div>
        <div className="home-stat-card">
          <div className="home-stat-value">{stats.savedQueriesCount}</div>
          <div className="home-stat-label">Saved queries</div>
        </div>
      </div>
    </div>

    <div>
      <div className="home-eyebrow">Roadmap</div>
      <div className="home-card home-roadmap">
        {ROADMAP.length === 0 && (
          <div className="home-roadmap-row">
            <span className="home-roadmap-label">Nothing planned right now — ideas welcome on GitHub.</span>
          </div>
        )}
        {ROADMAP.map((item) => (
          <div key={item.label} className="home-roadmap-row">
            <span className="home-roadmap-label">{item.label}</span>
            <span className={`home-roadmap-badge${item.status === 'In progress' ? ' active' : ''}`}>
              {item.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  </div>
)
