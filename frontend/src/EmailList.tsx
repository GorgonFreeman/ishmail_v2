import { Link } from 'react-router-dom'
import type { EmailGroup, Message } from './api'
import './EmailList.css'

function formatDate(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

type RowProps = {
  group: EmailGroup,
  selected: boolean,
  archivedView: boolean,
  historyView?: 'recent' | 'full',
  onToggle: () => void,
  onAction: (action: string, group: EmailGroup) => void,
}

export function EmailRow({
  group,
  selected,
  archivedView,
  historyView = 'recent',
  onToggle,
  onAction,
}: RowProps) {
  const senderLabel = group.from_name || group.from_email
  const params = new URLSearchParams()
  if (archivedView) params.set('archived', 'true')
  if (historyView === 'full') params.set('history', 'full')
  const qs = params.toString()
  const detailTo = `/email/${encodeURIComponent(group.id)}${qs ? `?${qs}` : ''}`

  return (
    <div className={`emailRow ${selected ? 'isSelected' : ''} ${group.flagged ? 'isFlagged' : ''}`}>
      <label className="rowCheck">
        <input type="checkbox" checked={selected} onChange={onToggle} />
      </label>
      <Link
        className="rowSender"
        to={`/sender/${encodeURIComponent(group.from_email)}`}
        title={group.from_email}
      >
        {senderLabel}
      </Link>
      <div className="rowSubject" title={group.subject}>
        <Link className="subjectText" to={detailTo}>{group.subject}</Link>
        <span className="rowDate">{formatDate(group.date)}</span>
      </div>
      <div className="rowActions">
        <button type="button" className="iconBtn" title="Star" onClick={() => onAction(group.flagged ? 'unstar' : 'star', group)}>
          {group.flagged ? '★' : '☆'}
        </button>
        <button type="button" className="iconBtn" title="Archive" onClick={() => onAction('archive', group)}>
          ⬇
        </button>
        <button type="button" className="iconBtn danger" title="Delete" onClick={() => onAction('delete', group)}>
          ⌫
        </button>
      </div>
      <div className="rowDupes" title={`${group.duplicate_count} copies across inboxes`}>
        ×{group.duplicate_count}
      </div>
    </div>
  )
}

type ListProps = {
  emails: EmailGroup[],
  selectedIds: Set<string>,
  archivedView?: boolean,
  historyView?: 'recent' | 'full',
  onToggle: (id: string) => void,
  onAction: (action: string, group: EmailGroup) => void,
}

export function EmailList({
  emails,
  selectedIds,
  archivedView = false,
  historyView = 'recent',
  onToggle,
  onAction,
}: ListProps) {
  if (!emails.length) {
    return <div className="emptyState">No emails match this view.</div>
  }
  return (
    <div className="emailList">
      {emails.map(g => (
        <EmailRow
          key={g.id}
          group={g}
          selected={selectedIds.has(g.id)}
          archivedView={archivedView}
          historyView={historyView}
          onToggle={() => onToggle(g.id)}
          onAction={onAction}
        />
      ))}
    </div>
  )
}

type SenderListProps = {
  messages: Message[],
  selected: Set<string>,
  onToggle: (key: string) => void,
  onAction: (action: string, messages: Message[]) => void,
}

function msgKey(m: Message) {
  return `${m.account_key}|${m.folder}|${m.uid}`
}

export function SenderMessageList({ messages, selected, onToggle, onAction }: SenderListProps) {
  if (!messages.length) {
    return <div className="emptyState">No messages from this sender.</div>
  }
  return (
    <div className="emailList">
      {messages.map(m => {
        const key = msgKey(m)
        return (
          <div key={key} className={`emailRow ${selected.has(key) ? 'isSelected' : ''}`}>
            <label className="rowCheck">
              <input type="checkbox" checked={selected.has(key)} onChange={() => onToggle(key)} />
            </label>
            <div className="rowSender muted" title={m.account_email}>{m.account_email}</div>
            <div className="rowSubject" title={m.subject}>
              <span className="subjectText">{m.subject}</span>
              <span className="rowDate">{formatDate(m.date)}</span>
            </div>
            <div className="rowActions">
              <button type="button" className="iconBtn" onClick={() => onAction(m.flagged ? 'unstar' : 'star', [m])}>
                {m.flagged ? '★' : '☆'}
              </button>
              {!m.archived && (
                <button type="button" className="iconBtn" onClick={() => onAction('archive', [m])}>⬇</button>
              )}
              <button type="button" className="iconBtn danger" onClick={() => onAction('delete', [m])}>⌫</button>
            </div>
            <div className="rowDupes">{m.archived ? 'A' : 'I'}</div>
          </div>
        )
      })}
    </div>
  )
}
