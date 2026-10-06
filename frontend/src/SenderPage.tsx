import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { Account, Message, MessageRef } from './api'
import { SenderMessageList } from './EmailList'
import { useMailActions, useSender } from './hooks'
import { AccountPickerModal } from './Modal'
import type { Job } from './api'

type Props = {
  accounts: Account[],
  watch: (job: Job) => void,
  mailReady: boolean,
}

function msgKey(m: Message) {
  return `${m.account_key}|${m.folder}|${m.uid}`
}

export function SenderPage({ accounts, watch, mailReady }: Props) {
  const { senderEmail = '' } = useParams()
  const decoded = decodeURIComponent(senderEmail)
  const { data, isLoading, error, refetch, isFetching } = useSender(decoded, mailReady)
  const { startAction, unsubscribe, deleteSender } = useMailActions(watch)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [modal, setModal] = useState<null | {
    title: string,
    confirmLabel: string,
    accountKeys: string[],
    onConfirm: (keys: string[]) => void,
  }>(null)

  const messages = data?.messages || []
  const relevantAccounts = useMemo(() => {
    const keys = new Set(data?.account_keys || [])
    return accounts.filter(a => keys.has(a.key))
  }, [accounts, data])

  const toggle = (key: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const openAction = (action: string, targets: Message[]) => {
    const refs: MessageRef[] = targets.map(m => ({
      account_key: m.account_key,
      folder: m.folder,
      uid: m.uid,
    }))
    const accountKeys = [...new Set(targets.map(m => m.account_key))]
    setModal({
      title: `${action} ${targets.length} message(s)?`,
      confirmLabel: action,
      accountKeys,
      onConfirm: (keys) => {
        setModal(null)
        startAction.mutate({ action, messages: refs, account_keys: keys })
        setSelected(new Set())
      },
    })
  }

  const selectedMessages = messages.filter(m => selected.has(msgKey(m)))

  return (
    <div className="page">
      <header className="pageHeader">
        <div className="headerLeft">
          <Link to="/" className="backLink">← All mail</Link>
          <h1 className="senderTitle">{decoded}</h1>
          <p className="subtle">{data?.count ?? '…'} messages across {relevantAccounts.length} inbox(es)</p>
        </div>
        <div className="headerActions">
          <button
            type="button"
            className="btn"
            disabled={!relevantAccounts.length}
            onClick={() => setModal({
              title: `Unsubscribe ${decoded}?`,
              confirmLabel: 'Unsubscribe',
              accountKeys: relevantAccounts.map(a => a.key),
              onConfirm: (keys) => {
                setModal(null)
                unsubscribe.mutate({ sender: decoded, account_keys: keys })
              },
            })}
          >
            Unsubscribe
          </button>
          <button
            type="button"
            className="btn danger"
            disabled={!relevantAccounts.length}
            onClick={() => setModal({
              title: `Delete all from ${decoded}?`,
              confirmLabel: 'Delete all',
              accountKeys: relevantAccounts.map(a => a.key),
              onConfirm: (keys) => {
                setModal(null)
                deleteSender.mutate({ sender: decoded, account_keys: keys })
              },
            })}
          >
            Delete all
          </button>
          <button type="button" className="btn ghost" onClick={() => refetch()} disabled={isFetching}>
            Refresh
          </button>
        </div>
      </header>

      <div className="stickyBar">
        <span className="selectionCount">{selected.size} selected</span>
        <button
          type="button"
          className="btn"
          disabled={!selected.size}
          onClick={() => openAction('star', selectedMessages)}
        >Star</button>
        <button
          type="button"
          className="btn"
          disabled={!selected.size}
          onClick={() => openAction('archive', selectedMessages.filter(m => !m.archived))}
        >Archive</button>
        <button
          type="button"
          className="btn danger"
          disabled={!selected.size}
          onClick={() => openAction('delete', selectedMessages)}
        >Delete</button>
      </div>

      {!mailReady && <div className="emptyState">Waiting for mail fetch…</div>}
      {mailReady && isLoading && <div className="emptyState">Loading…</div>}
      {error && <div className="errorBanner">{(error as Error).message}</div>}
      {mailReady && !isLoading && (
        <SenderMessageList
          messages={messages}
          selected={selected}
          onToggle={toggle}
          onAction={openAction}
        />
      )}

      {modal && (
        <AccountPickerModal
          title={modal.title}
          accounts={accounts.filter(a => modal.accountKeys.includes(a.key))}
          defaultSelected={modal.accountKeys}
          confirmLabel={modal.confirmLabel}
          onClose={() => setModal(null)}
          onConfirm={modal.onConfirm}
        />
      )}
    </div>
  )
}
