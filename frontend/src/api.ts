export type MessageRef = {
  account_key: string,
  folder: string,
  uid: number,
}

export type Message = MessageRef & {
  account_email: string,
  date: string,
  from_email: string,
  from_name: string,
  subject: string,
  flagged: boolean,
  archived: boolean,
}

export type EmailGroup = {
  id: string,
  subject: string,
  subject_normalized: string,
  from_email: string,
  from_name: string,
  date: string,
  flagged: boolean,
  archived: boolean,
  duplicate_count: number,
  messages: Message[],
  account_keys: string[],
}

export type Account = {
  key: string,
  email: string,
  provider: string,
  name: string,
  auth: string,
}

export type Job = {
  id: string,
  kind: string,
  status: 'pending' | 'running' | 'completed' | 'failed',
  created_at: string,
  started_at: string | null,
  finished_at: string | null,
  progress: Record<string, unknown>,
  result: Record<string, unknown> | null,
  error: string | null,
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json() as Promise<T>
}

export const api = {
  accounts: () => request<{ accounts: Account[], names: string[] }>('/api/accounts'),
  emails: (opts: { archived: boolean, q?: string, refresh?: boolean }) => {
    const params = new URLSearchParams({
      archived: String(opts.archived),
    })
    if (opts.q) params.set('q', opts.q)
    if (opts.refresh) params.set('refresh', 'true')
    return request<{ emails: EmailGroup[], errors: string[], total_messages: number }>(
      `/api/emails?${params}`,
    )
  },
  sender: (email: string, refresh = false) => {
    const params = refresh ? '?refresh=true' : ''
    return request<{
      sender: string,
      count: number,
      account_keys: string[],
      messages: Message[],
      errors: string[],
    }>(`/api/senders/${encodeURIComponent(email)}${params}`)
  },
  startAction: (body: {
    action: string,
    messages: MessageRef[],
    account_keys?: string[] | null,
  }) => request<Job>('/api/jobs/action', { method: 'POST', body: JSON.stringify(body) }),
  startFetch: (force = true) =>
    request<Job>(`/api/jobs/fetch?force=${force ? 'true' : 'false'}`, { method: 'POST' }),
  unsubscribe: (sender: string, account_keys?: string[] | null) =>
    request<Job>('/api/jobs/unsubscribe', {
      method: 'POST',
      body: JSON.stringify({ sender, account_keys }),
    }),
  deleteSender: (sender: string, account_keys?: string[] | null) =>
    request<Job>('/api/jobs/delete-sender', {
      method: 'POST',
      body: JSON.stringify({ sender, account_keys }),
    }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  jobs: () => request<{ jobs: Job[] }>('/api/jobs'),
  refresh: () => request<{ total_messages: number, errors: string[] }>('/api/refresh', { method: 'POST' }),
}
