import type { EmailGroup, Message } from './api'

function msgRef(m: Message) {
  return `${m.account_key}|${m.folder}|${m.uid}`
}

/** Merge full-pool groups into the recent list without dropping stable row ids. */
export function mergeEmailGroups(
  recent: EmailGroup[],
  full: EmailGroup[],
): EmailGroup[] {
  if (!full.length) return recent
  const byId = new Map<string, EmailGroup>()
  for (const g of recent) byId.set(g.id, g)

  for (const g of full) {
    const existing = byId.get(g.id)
    if (!existing) {
      byId.set(g.id, g)
      continue
    }
    const seen = new Set(existing.messages.map(msgRef))
    const extra = g.messages.filter(m => !seen.has(msgRef(m)))
    if (!extra.length) {
      // Still refresh duplicate_count / flagged if full knows more.
      if (
        g.duplicate_count <= existing.duplicate_count
        && g.flagged === existing.flagged
      ) {
        continue
      }
    }
    const messages = [...existing.messages, ...extra].sort((a, b) =>
      b.date.localeCompare(a.date),
    )
    const primary = messages[0] || existing.messages[0]
    byId.set(g.id, {
      ...existing,
      subject: primary?.subject || existing.subject,
      from_email: primary?.from_email || existing.from_email,
      from_name: primary?.from_name || existing.from_name,
      date: primary?.date || existing.date,
      flagged: existing.flagged || g.flagged || messages.some(m => m.flagged),
      duplicate_count: messages.length,
      messages,
      account_keys: [...new Set([
        ...existing.account_keys,
        ...g.account_keys,
      ])].sort(),
    })
  }

  return [...byId.values()].sort((a, b) => b.date.localeCompare(a.date))
}
