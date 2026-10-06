import { useMemo, useState } from 'react'
import type { Account, EmailGroup, Job, MessageRef } from './api'
import { EmailList } from './EmailList'
import { useEmails, useMailActions } from './hooks'
import { AccountPickerModal } from './Modal'

type Props = {
  accounts: Account[],
  watch: (job: Job) => void,
}

export function InboxPage({ accounts, watch }: Props) {
  const [archived, setArchived] = useState(false)
  const [q, setQ] = useState('')
  const [search, setSearch] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const { data, isLoading, error, refetch, isFetching } = useEmails(archived, search)
  const { startAction, refresh } = useMailActions(watch)
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

  return (
    <div className="page">
      <header className="pageHeader">
        <div className="headerLeft">
          <h1 className="brand">ishmail</h1>
          <p className="subtle">
            {data ? `${emails.length} groups · ${data.total_messages} raw` : 'Loading…'}
            {archived ? ' · archived' : ' · inbox'}
          </p>
        </div>
        <div className="headerActions">
          <button
            type="button"
            className="btn ghost"
            disabled={isFetching || refresh.isPending}
            onClick={async () => {
              await refresh.mutateAsync()
              refetch()
            }}
          >
            Refresh
          </button>
        </div>
      </header>

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

      {data?.errors?.length ? (
        <div className="errorBanner">
          Some accounts failed: {data.errors.join(' · ')}
        </div>
      ) : null}
      {error && <div className="errorBanner">{(error as Error).message}</div>}
      {isLoading && <div className="emptyState">Loading mail across inboxes…</div>}
      {!isLoading && (
        <EmailList
          emails={emails}
          selectedIds={selectedIds}
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
