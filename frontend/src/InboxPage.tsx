import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Account, EmailGroup, Job, MessageRef } from './api'
import { EmailList } from './EmailList'
import { useInboxEmails, useMailActions } from './hooks'
import { AccountPickerModal } from './Modal'

type Props = {
  accounts: Account[],
  watch: (job: Job) => void,
  registerAction: (job: Job, action: string, messages: MessageRef[]) => void,
  recentFetchJob: Job | null,
  fullFetchJob: Job | null,
  recentFetching: boolean,
  fullFetching: boolean,
  mailReady: boolean,
  onRefresh: () => Promise<unknown>,
  refreshPending: boolean,
  onLoadFullHistory: () => Promise<unknown>,
  loadFullHistoryPending: boolean,
}

export function FetchProgress({
  job,
  labelScope,
}: {
  job: Job | null,
  labelScope?: string,
}) {
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
  const scope = labelScope || (full ? 'all time' : 'last 6 months')
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
  registerAction,
  recentFetchJob,
  fullFetchJob,
  recentFetching,
  fullFetching,
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
  const {
    emails,
    totalMessages,
    errors,
    isLoading,
    isFetching,
    error,
    fullFetching: fullFetchingFlag,
    showingAllTime,
  } = useInboxEmails(archived, search, mailReady)
  const { startAction } = useMailActions(watch, registerAction)
  const [modal, setModal] = useState<null | {
    title: string,
    confirmLabel: string,
    accountKeys: string[],
    messages: MessageRef[],
    action: string,
  }>(null)

  const selectedGroups = useMemo(
    () => emails.filter(e => selectedIds.has(e.id)),
    [emails, selectedIds],
  )

  const loadingFullHistory = fullFetching || loadFullHistoryPending || fullFetchingFlag

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

  // Boot gate: spinner + progress only until recent pool is ready.
  if (!mailReady) {
    return (
      <div className="page">
        <header className="pageHeader">
          <div className="headerLeft">
            <h1 className="brand">ishmail</h1>
            <p className="subtle">Loading last 6 months…</p>
          </div>
        </header>
        <div className="emptyState bootState">
          <FetchProgress job={recentFetchJob} labelScope="last 6 months" />
        </div>
      </div>
    )
  }

  const fullDone = typeof fullFetchJob?.progress?.done === 'number'
    ? fullFetchJob.progress.done
    : null
  const fullTotal = typeof fullFetchJob?.progress?.total === 'number'
    ? fullFetchJob.progress.total
    : null

  let bannerLabel = 'Showing last 6 months — click to load all time'
  if (showingAllTime) {
    bannerLabel = 'Showing all time'
  } else if (loadingFullHistory) {
    bannerLabel = fullDone != null && fullTotal != null
      ? `Loading all time… ${fullDone}/${fullTotal}`
      : 'Loading all time…'
  }

  return (
    <div className="page">
      <header className="pageHeader">
        <div className="headerLeft">
          <h1 className="brand">ishmail</h1>
          <p className="subtle">
            {`${emails.length} groups · ${totalMessages} raw`}
            {archived ? ' · archived' : ' · inbox'}
            {showingAllTime ? ' · all time' : ' · last 6 months'}
            {recentFetching ? ' · refreshing…' : ''}
          </p>
        </div>
        <div className="headerActions">
          <button
            type="button"
            className="btn ghost"
            disabled={refreshPending || isFetching}
            onClick={() => {
              void onRefresh()
            }}
          >
            Refresh
          </button>
        </div>
      </header>

      {!showingAllTime ? (
        <button
          type="button"
          className="historyBanner"
          disabled={refreshPending || loadingFullHistory}
          onClick={() => {
            void onLoadFullHistory()
          }}
        >
          {bannerLabel}
        </button>
      ) : (
        <div className="historyBanner historyBannerStatic">{bannerLabel}</div>
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

      {errors.length ? (
        <div className="errorBanner">
          Some accounts failed: {errors.join(' · ')}
        </div>
      ) : null}
      {error && <div className="errorBanner">{(error as Error).message}</div>}
      {mailReady && isLoading && emails.length === 0 && (
        <div className="emptyState">Grouping mail…</div>
      )}
      {emails.length > 0 || !isLoading ? (
        <EmailList
          emails={emails}
          selectedIds={selectedIds}
          archivedView={archived}
          emptyMessage={
            errors.length
              ? 'Couldn’t load mail from any account. Check the errors above, then Refresh.'
              : search
                ? 'No emails match this search.'
                : 'No emails in this view.'
          }
          onToggle={toggle}
          onAction={onRowAction}
        />
      ) : null}

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
