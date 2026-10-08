import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Account, EmailGroup, Job, MessageRef } from './api'
import { EmailList } from './EmailList'
import { useEmails, useMailActions } from './hooks'
import { AccountPickerModal } from './Modal'

type Props = {
  accounts: Account[],
  watch: (job: Job) => void,
  fetchJob: Job | null,
  fetching: boolean,
  mailReady: boolean,
  onRefresh: (fullHistory?: boolean) => Promise<unknown>,
  refreshPending: boolean,
  onLoadFullHistory: () => Promise<unknown>,
  loadFullHistoryPending: boolean,
}

function FetchProgress({ job }: { job: Job | null }) {
  const done = typeof job?.progress?.done === 'number' ? job.progress.done : 0
  const total = typeof job?.progress?.total === 'number' ? job.progress.total : 0
  const current =
    typeof job?.progress?.current === 'string' && job.progress.current
      ? job.progress.current
      : typeof job?.progress?.account_key === 'string'
        ? job.progress.account_key
        : null
  const full = Boolean(
    job?.progress?.full_history || job?.progress?.history === 'full',
  )
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  const scope = full ? 'full history' : 'last 6 months'
  const label = total > 0
    ? `Loading ${scope}… ${done}/${total}${current ? ` — ${current}` : ''}`
    : `Loading ${scope} across inboxes…`

  return (
    <div className="fetchProgress">
      <p className="fetchProgressLabel">{label}</p>
      <div className="fetchProgressTrack" aria-hidden>
        <div className="fetchProgressBar" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export function InboxPage({
  accounts,
  watch,
  fetchJob,
  fetching,
  mailReady,
  onRefresh,
  refreshPending,
  onLoadFullHistory,
  loadFullHistoryPending,
}: Props) {
  const [searchParams, setSearchParams] = useSearchParams()
  const archived = searchParams.get('archived') === 'true'
  const setArchived = (value: boolean) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set('archived', 'true')
    else next.delete('archived')
    setSearchParams(next, { replace: true })
  }
  const [q, setQ] = useState('')
  const [search, setSearch] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const { data, isLoading, error, isFetching } = useEmails(archived, search, mailReady)
  const { startAction } = useMailActions(watch)
  const [modal, setModal] = useState<null | {
    title: string,
    confirmLabel: string,
    accountKeys: string[],
    messages: MessageRef[],
    action: string,
  }>(null)

  const emails = data?.emails || []
  const selectedGroups = useMemo(
    () => emails.filter(e => selectedIds.has(e.id)),
    [emails, selectedIds],
  )

  const toggle = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const openAction = (action: string, groups: EmailGroup[]) => {
    const messages: MessageRef[] = groups.flatMap(g =>
      g.messages.map(m => ({
        account_key: m.account_key,
        folder: m.folder,
        uid: m.uid,
      })),
    )
    const accountKeys = [...new Set(groups.flatMap(g => g.account_keys))]
    setModal({
      title: `${action} across ${accountKeys.length} inbox(es)?`,
      confirmLabel: action,
      accountKeys,
      messages,
      action,
    })
  }

  const onRowAction = (action: string, group: EmailGroup) => {
    openAction(action, [group])
  }

  const hasSelection = selectedIds.size > 0
  const showInitialProgress = (fetching || !mailReady) && emails.length === 0
  const showList = Boolean(data)
  const history = data?.history === 'full' || fetchJob?.progress?.history === 'full'
    ? 'full'
    : 'recent'
  const loadingFullHistory = fetching && (
    fetchJob?.progress?.full_history === true
    || fetchJob?.progress?.history === 'full'
  )

  return (
    <div className="page">
      <header className="pageHeader">
        <div className="headerLeft">
          <h1 className="brand">ishmail</h1>
          <p className="subtle">
            {data ? `${emails.length} groups · ${data.total_messages} raw` : 'Loading…'}
            {archived ? ' · archived' : ' · inbox'}
            {history === 'full' ? ' · full history' : ' · last 6 months'}
            {(fetching || data?.fetching) ? ' · loading…' : ''}
          </p>
        </div>
        <div className="headerActions">
          <button
            type="button"
            className="btn ghost"
            disabled={refreshPending || isFetching}
            onClick={() => {
              void onRefresh(history === 'full')
            }}
          >
            Refresh
          </button>
        </div>
      </header>

      {history !== 'full' && (
        <button
          type="button"
          className="historyBanner"
          disabled={refreshPending || loadFullHistoryPending || loadingFullHistory}
          onClick={() => {
            void onLoadFullHistory()
          }}
        >
          {loadingFullHistory || loadFullHistoryPending
            ? 'Loading full history…'
            : 'Showing last 6 months — load full history'}
        </button>
      )}

      <div className="stickyBar">
        <span className="selectionCount">{selectedIds.size} selected</span>
        <button
          type="button"
          className="btn"
          disabled={!hasSelection}
          onClick={() => openAction('star', selectedGroups)}
        >Star</button>
        <button
          type="button"
          className="btn"
          disabled={!hasSelection}
          onClick={() => openAction('unstar', selectedGroups)}
        >Unstar</button>
        <button
          type="button"
          className="btn"
          disabled={!hasSelection}
          onClick={() => openAction('archive', selectedGroups)}
        >Archive</button>
        <button
          type="button"
          className="btn danger"
          disabled={!hasSelection}
          onClick={() => openAction('delete', selectedGroups)}
        >Delete</button>

        <div className="barSpacer" />

        <form
          className="searchForm"
          onSubmit={e => {
            e.preventDefault()
            setSearch(q.trim())
            setSelectedIds(new Set())
          }}
        >
          <input
            className="searchInput"
            placeholder="Search subject or sender…"
            value={q}
            onChange={e => setQ(e.target.value)}
          />
        </form>

        <label className="toggle">
          <input
            type="checkbox"
            checked={archived}
            onChange={e => {
              setArchived(e.target.checked)
              setSelectedIds(new Set())
            }}
          />
          Show archived
        </label>
      </div>

      {fetching && data && emails.length > 0 && (
        <div className="fetchProgressBanner">
          <FetchProgress job={fetchJob} />
        </div>
      )}

      {data?.errors?.length ? (
        <div className="errorBanner">
          Some accounts failed: {data.errors.join(' · ')}
        </div>
      ) : null}
      {error && <div className="errorBanner">{(error as Error).message}</div>}
      {showInitialProgress && (
        <div className="emptyState">
          <FetchProgress job={fetchJob} />
        </div>
      )}
      {mailReady && isLoading && !data && (
        <div className="emptyState">Grouping mail…</div>
      )}
      {showList && (
        <EmailList
          emails={emails}
          selectedIds={selectedIds}
          archivedView={archived}
          onToggle={toggle}
          onAction={onRowAction}
        />
      )}

      {modal && (
        <AccountPickerModal
          title={modal.title}
          accounts={accounts.filter(a => modal.accountKeys.includes(a.key))}
          defaultSelected={modal.accountKeys}
          confirmLabel={modal.confirmLabel}
          onClose={() => setModal(null)}
          onConfirm={(keys) => {
            startAction.mutate({
              action: modal.action,
              messages: modal.messages,
              account_keys: keys,
            })
            setModal(null)
            setSelectedIds(new Set())
          }}
        />
      )}
    </div>
  )
}
