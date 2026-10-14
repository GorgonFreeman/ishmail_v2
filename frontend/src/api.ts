export type MessageRef = {
  account_key: string,
  folder: string,
  uid: number,
}

/** Slim list member — subject/from live on the group header. */
export type MessageSlim = MessageRef & {
  account_email: string,
  date: string,
  flagged: boolean,
  archived: boolean,
}

/** Full member (detail / sender views). */
export type Message = MessageSlim & {
  from_email: string,
  from_name: string,
  subject: string,
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
  messages: MessageSlim[],
  account_keys: string[],
}

export type EmailGroupDetail = Omit<EmailGroup, 'messages'> & {
  messages: Message[],
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

export type MessageDetail = {
  account_key: string,
  account_email: string,
  folder: string,
  uid: number,
  date: string,
  from_email: string,
  from_name: string,
  subject: string,
  flagged: boolean,
  body_text: string,
  body_html: string | null,
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

export type PoolStatus = {
  ok: boolean,
  fetching: boolean,
  has_cache: boolean,
  history: 'recent' | 'full',
  generation: number,
  total_messages: number,
  errors: string[],
  full_ready: boolean,
  full_fetching: boolean,
}

export const api = {
  accounts: () => request<{ accounts: Account[], names: string[] }>('/api/accounts'),
  status: (history: 'recent' | 'full' = 'recent') =>
    request<PoolStatus>(`/api/status?history=${history}`),
  emails: (opts: {
    archived: boolean,
    q?: string,
    refresh?: boolean,
    history?: 'recent' | 'full',
  }) => {
    const params = new URLSearchParams({
      archived: String(opts.archived),
      history: opts.history || 'recent',
    })
    if (opts.q) params.set('q', opts.q)
    if (opts.refresh) params.set('refresh', 'true')
    return request<{
      emails: EmailGroup[],
      errors: string[],
      total_messages: number,
      fetching?: boolean,
      history?: 'recent' | 'full',
      full_ready?: boolean,
      full_fetching?: boolean,
    }>(`/api/emails?${params}`)
  },
  group: (
    groupId: string,
    opts: { archived?: boolean, history?: 'recent' | 'full' } = {},
  ) => {
    const params = new URLSearchParams({
      archived: String(Boolean(opts.archived)),
      history: opts.history || 'recent',
    })
    return request<EmailGroupDetail>(
      `/api/groups/${encodeURIComponent(groupId)}?${params}`,
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
  message: (ref: MessageRef) => {
    const params = new URLSearchParams({
      account_key: ref.account_key,
      folder: ref.folder,
      uid: String(ref.uid),
    })
    return request<MessageDetail>(`/api/message?${params}`)
  },
  startAction: (body: {
    action: string,
    messages: MessageRef[],
    account_keys?: string[] | null,
  }) => request<Job>('/api/jobs/action', { method: 'POST', body: JSON.stringify(body) }),
  activeFetchJob: (history: 'recent' | 'full' = 'recent') =>
    request<{ job: Job | null }>(
      `/api/jobs/fetch/active?history=${history}`,
    ),
  startFetch: (force = true, fullHistory = false) => {
    const params = new URLSearchParams({
      force: force ? 'true' : 'false',
      full_history: fullHistory ? 'true' : 'false',
    })
    return request<Job>(`/api/jobs/fetch?${params}`, { method: 'POST' })
  },
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
