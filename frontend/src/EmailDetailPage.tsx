import { useMemo, useState } from 'react'
import { Letter } from 'react-letter'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import type { Account, EmailGroup, Job, Message, MessageRef } from './api'
import { AccountPickerModal } from './Modal'
import { useEmails, useMailActions, useMessageDetail } from './hooks'
import './EmailDetail.css'

type Props = {
  accounts: Account[],
  watch: (job: Job) => void,
  mailReady: boolean,
}

function formatDate(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function msgKey(m: Message | MessageRef) {
  return `${m.account_key}|${m.folder}|${m.uid}`
}

export function EmailDetailPage({ accounts, watch, mailReady }: Props) {
  const { groupId = '' } = useParams()
  const [params] = useSearchParams()
  const archived = params.get('archived') === 'true'
  const historyView = params.get('history') === 'full' ? 'full' as const : 'recent' as const
  const navigate = useNavigate()
  const { data, isLoading, error } = useEmails(archived, '', historyView, mailReady)
  const { startAction } = useMailActions(watch)
  const [showAll, setShowAll] = useState(false)
  const [modal, setModal] = useState<null | {
    title: string,
    confirmLabel: string,
    accountKeys: string[],
    messages: MessageRef[],
    action: string,
  }>(null)

  const group = useMemo(
    () => data?.emails.find(e => e.id === groupId) ?? null,
    [data, groupId],
  )

  const primary = useMemo(() => {
    if (!group?.messages.length) return null
    const visible = group.messages.filter(m => m.archived === archived)
    const pool = visible.length ? visible : group.messages
    return [...pool].sort((a, b) => b.date.localeCompare(a.date))[0]
  }, [group, archived])

  const duplicates = useMemo(() => {
    if (!group || !primary) return []
    const key = msgKey(primary)
    return group.messages.filter(m => msgKey(m) !== key)
  }, [group, primary])

  const detailQuery = useMessageDetail(
    primary
      ? { account_key: primary.account_key, folder: primary.folder, uid: primary.uid }
      : null,
    mailReady && Boolean(primary),
  )

  const openAction = (action: string, messages: Message[]) => {
    if (!messages.length) return
    const refs: MessageRef[] = messages.map(m => ({
      account_key: m.account_key,
      folder: m.folder,
      uid: m.uid,
    }))
    const accountKeys = [...new Set(messages.map(m => m.account_key))]
    setModal({
      title: `${action} ${messages.length} message(s)?`,
      confirmLabel: action,
      accountKeys,
      messages: refs,
      action,
    })
  }

  const openGroupAction = (action: string, g: EmailGroup) => {
    openAction(action, g.messages)
  }

  if (!mailReady || isLoading) {
    return <div className="emptyState">Loading…</div>
  }
  if (error) {
    return <div className="errorBanner">{(error as Error).message}</div>
  }
  if (!group || !primary) {
    return (
      <div className="page">
        <header className="pageHeader">
          <Link to="/" className="backLink">← All mail</Link>
        </header>
        <div className="emptyState">Email not found. It may have been deleted or archived.</div>
      </div>
    )
  }

  const senderLabel = primary.from_name || primary.from_email
  const bodyHtml = detailQuery.data?.body_html || ''
  const bodyText = detailQuery.data?.body_text || ''

  return (
    <div className="page detailPage">
      <header className="pageHeader">
        <div className="headerLeft">
          <Link to={archived ? '/?archived=true' : '/'} className="backLink">← All mail</Link>
          <h1 className="detailSubject">{group.subject}</h1>
          <p className="subtle">
            {group.duplicate_count} cop{group.duplicate_count === 1 ? 'y' : 'ies'} across inboxes
            {archived ? ' · archived view' : ''}
          </p>
        </div>
        <div className="headerActions">
          <button
            type="button"
            className="btn"
            onClick={() => openGroupAction(group.flagged ? 'unstar' : 'star', group)}
          >
            {group.flagged ? 'Unstar' : 'Star'}
          </button>
          {!archived && (
            <button type="button" className="btn" onClick={() => openGroupAction('archive', group)}>
              Archive
            </button>
          )}
          <button type="button" className="btn danger" onClick={() => openGroupAction('delete', group)}>
            Delete
          </button>
        </div>
      </header>

      <article className="detailMain">
        <div className="detailMeta">
          <Link
            className="detailSender"
            to={`/sender/${encodeURIComponent(primary.from_email)}`}
          >
            {senderLabel}
          </Link>
          <span className="detailFromEmail">{primary.from_email}</span>
          <span className="detailDate">{formatDate(primary.date)}</span>
          <span className="detailAccount" title={primary.account_key}>
            {primary.account_email}
            {primary.archived ? ' · archived' : ' · inbox'}
          </span>
        </div>

        <div className="detailRowActions">
          <button
            type="button"
            className="iconBtn"
            title="Star"
            onClick={() => openAction(primary.flagged ? 'unstar' : 'star', [primary])}
          >
            {primary.flagged ? '★' : '☆'}
          </button>
          {!primary.archived && (
            <button
              type="button"
              className="iconBtn"
              title="Archive"
              onClick={() => openAction('archive', [primary])}
            >
              ⬇
            </button>
          )}
          <button
            type="button"
            className="iconBtn danger"
            title="Delete"
            onClick={() => openAction('delete', [primary])}
          >
            ⌫
          </button>
        </div>

        <div className="detailBody">
          {detailQuery.isLoading && <p className="subtle">Loading message…</p>}
          {detailQuery.error && (
            <div className="errorBanner">{(detailQuery.error as Error).message}</div>
          )}
          {!detailQuery.isLoading && !detailQuery.error && (bodyHtml || bodyText) && (
            <Letter
              className="detailLetter"
              html={bodyHtml}
              text={bodyText}
              useIframe
              iframeTitle={group.subject}
            />
          )}
          {!detailQuery.isLoading && !detailQuery.error && !bodyHtml && !bodyText && (
            <p className="subtle">(no message body)</p>
          )}
        </div>
      </article>

      {duplicates.length > 0 && (
        <div className="dupeSection">
          <button
            type="button"
            className="dupeBar"
            onClick={() => setShowAll(v => !v)}
            aria-expanded={showAll}
          >
            <span>{showAll ? 'Hide duplicates' : 'See all'}</span>
            <span className="dupeBarCount">{duplicates.length} other cop{duplicates.length === 1 ? 'y' : 'ies'}</span>
          </button>

          {showAll && (
            <ul className="dupeList">
              {duplicates.map(m => (
                <li key={msgKey(m)} className="dupeRow">
                  <div className="dupeInfo">
                    <span className="dupeAccount">{m.account_email}</span>
                    <span className="dupeSubject" title={m.subject}>{m.subject}</span>
                    <span className="dupeMeta">
                      {formatDate(m.date)}
                      {m.archived ? ' · archived' : ' · inbox'}
                      {m.flagged ? ' · starred' : ''}
                    </span>
                  </div>
                  <div className="rowActions">
                    <button
                      type="button"
                      className="iconBtn"
                      title="Star"
                      onClick={() => openAction(m.flagged ? 'unstar' : 'star', [m])}
                    >
                      {m.flagged ? '★' : '☆'}
                    </button>
                    {!m.archived && (
                      <button
                        type="button"
                        className="iconBtn"
                        title="Archive"
                        onClick={() => openAction('archive', [m])}
                      >
                        ⬇
                      </button>
                    )}
                    <button
                      type="button"
                      className="iconBtn danger"
                      title="Delete"
                      onClick={() => openAction('delete', [m])}
                    >
                      ⌫
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
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
            if (modal.action === 'delete' || modal.action === 'archive') {
              // After group-level delete/archive, return to list
              if (modal.messages.length >= (group?.messages.length || 0)) {
                navigate(archived ? '/?archived=true' : '/')
              }
            }
          }}
        />
      )}
    </div>
  )
}
