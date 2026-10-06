import { useEffect, useState } from 'react'
import type { Account } from './api'
import './Modal.css'

type Props = {
  title: string,
  accounts: Account[],
  defaultSelected?: string[],
  confirmLabel: string,
  onConfirm: (accountKeys: string[]) => void,
  onClose: () => void,
}

export function AccountPickerModal({
  title,
  accounts,
  defaultSelected,
  confirmLabel,
  onConfirm,
  onClose,
}: Props) {
  const initial = new Set(defaultSelected ?? accounts.map(a => a.key))
  const [selected, setSelected] = useState<Set<string>>(initial)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'Enter') {
        e.preventDefault()
        onConfirm([...selected])
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onConfirm, selected])

  const toggle = (key: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div className="modalBackdrop" onClick={onClose}>
      <div className="modal" role="dialog" onClick={e => e.stopPropagation()}>
        <h2>{title}</h2>
        <p className="modalHint">Untick inboxes to skip. Press Enter to confirm.</p>
        <ul className="accountList">
          {accounts.map(a => (
            <li key={a.key}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.has(a.key)}
                  onChange={() => toggle(a.key)}
                />
                <span className="accountEmail">{a.email}</span>
                <span className="accountKey">{a.key}</span>
              </label>
            </li>
          ))}
        </ul>
        <div className="modalActions">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn primary"
            disabled={selected.size === 0}
            onClick={() => onConfirm([...selected])}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
