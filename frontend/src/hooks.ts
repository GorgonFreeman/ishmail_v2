import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type EmailGroup,
  type EmailGroupDetail,
  type Job,
  type Message,
  type MessageRef,
} from './api'

export type HistoryMode = 'recent' | 'full'

export type EmailsPayload = {
  emails: EmailGroup[],
  errors: string[],
  total_messages: number,
  fetching?: boolean,
  history?: 'recent' | 'full',
  full_ready?: boolean,
  full_fetching?: boolean,
}

function msgRefKey(m: { account_key: string, folder: string, uid: number }) {
  return `${m.account_key}|${m.folder}|${m.uid}`
}

function isFullFetchJob(job: Job) {
  return job.progress?.full_history === true || job.progress?.history === 'full'
}

function isRecentFetchJob(job: Job) {
  return job.kind === 'fetch' && !isFullFetchJob(job)
}

function patchEmailsCache(
  queryClient: ReturnType<typeof useQueryClient>,
  action: string,
  messages: MessageRef[],
) {
  const refs = new Set(messages.map(msgRefKey))
  queryClient.setQueriesData<EmailsPayload>({ queryKey: ['emails'] }, (old) => {
    if (!old?.emails) return old

    if (action === 'delete' || action === 'archive') {
      const emails = old.emails
        .map((g) => {
          const remaining = g.messages.filter(m => !refs.has(msgRefKey(m)))
          if (remaining.length === g.messages.length) return g
          if (!remaining.length) return null
          const primary = [...remaining].sort((a, b) =>
            b.date.localeCompare(a.date),
          )[0]
          // List messages are slim — keep group subject/from, refresh date only.
          return {
            ...g,
            messages: remaining,
            duplicate_count: remaining.length,
            flagged: remaining.some(m => m.flagged),
            date: primary.date,
            account_keys: [...new Set(remaining.map(m => m.account_key))].sort(),
          }
        })
        .filter((g): g is EmailGroup => g != null)

      return {
        ...old,
        emails,
        total_messages: emails.reduce((n, g) => n + g.messages.length, 0),
      }
    }

    if (action === 'star' || action === 'unstar') {
      const flagged = action === 'star'
      return {
        ...old,
        emails: old.emails.map((g) => {
          const hit = g.messages.some(m => refs.has(msgRefKey(m)))
          if (!hit) return g
          const messages = g.messages.map(m => (
            refs.has(msgRefKey(m)) ? { ...m, flagged } : m
          ))
          return {
            ...g,
            messages,
            flagged: messages.some(m => m.flagged),
          }
        }),
      }
    }

    return old
  })
}

/** Lightweight pool flags — poll this instead of re-grouping /api/emails. */
export function usePoolStatus(enabled = true) {
  return useQuery({
    queryKey: ['poolStatus'],
    queryFn: () => api.status('recent'),
    enabled,
    refetchInterval: (query) => (
      query.state.data?.full_fetching ? 1_500 : 60_000
    ),
  })
}

/**
 * Single history pool for the list: full when ready, else recent.
 * No client merge — recent ⊂ full once all-time is loaded.
 */
export function useInboxEmails(archived: boolean, q: string, enabled = true) {
  const queryClient = useQueryClient()
  const status = usePoolStatus(enabled)
  const fullReady = Boolean(status.data?.full_ready)
  const fullFetching = Boolean(status.data?.full_fetching)
  const history: HistoryMode = fullReady ? 'full' : 'recent'
  const qKey = q.trim()

  const wasFullReady = useRef(false)
  useEffect(() => {
    if (fullReady && !wasFullReady.current) {
      queryClient.invalidateQueries({ queryKey: ['emails'] })
    }
    wasFullReady.current = fullReady
  }, [fullReady, queryClient])

  const list = useQuery({
    queryKey: ['emails', archived, qKey, history],
    queryFn: () => api.emails({
      archived,
      q: qKey || undefined,
      history,
    }),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  const emails = list.data?.emails || []
  const totalMessages = list.data?.total_messages
    ?? emails.reduce((n, g) => n + g.messages.length, 0)

  return {
    emails,
    totalMessages,
    errors: list.data?.errors || status.data?.errors || [],
    isLoading: list.isLoading,
    isFetching: list.isFetching,
    error: list.error,
    fullReady,
    fullFetching,
    showingAllTime: fullReady,
    history,
    data: list.data,
  }
}

/** One group with full per-message fields (detail page). */
export function useEmailGroup(
  groupId: string | undefined,
  archived: boolean,
  history: HistoryMode = 'recent',
  enabled = true,
) {
  return useQuery<EmailGroupDetail>({
    queryKey: ['group', groupId, archived, history],
    queryFn: () => api.group(groupId!, { archived, history }),
    enabled: Boolean(groupId) && enabled,
    staleTime: 60_000,
  })
}

/** @deprecated Prefer useInboxEmails for the main list. */
export function useEmails(
  archived: boolean,
  q: string,
  history: HistoryMode = 'recent',
  enabled = true,
) {
  return useQuery({
    queryKey: ['emails', archived, q, history],
    queryFn: () => api.emails({
      archived,
      q: q || undefined,
      history,
    }),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
}

export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: () => api.accounts(),
  })
}

export function useSender(email: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['sender', email],
    queryFn: () => api.sender(email!),
    enabled: Boolean(email) && enabled,
  })
}

export function useMessageDetail(
  ref: MessageRef | null | undefined,
  enabled = true,
) {
  return useQuery({
    queryKey: ['message', ref?.account_key, ref?.folder, ref?.uid],
    queryFn: () => api.message(ref!),
    enabled: Boolean(ref) && enabled,
  })
}

export function useJobs() {
  return useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.jobs(),
    refetchInterval: (query) => {
      const jobs = query.state.data?.jobs || []
      const active = jobs.some(j => j.status === 'pending' || j.status === 'running')
      return active ? 800 : 5_000
    },
  })
}

type PendingAction = {
  action: string,
  messages: MessageRef[],
}

export function useTrackJob() {
  const queryClient = useQueryClient()
  const [activeJobs, setActiveJobs] = useState<Job[]>([])
  const timers = useRef<Map<string, number>>(new Map())
  const pendingActions = useRef<Map<string, PendingAction>>(new Map())

  const registerAction = useCallback((job: Job, action: string, messages: MessageRef[]) => {
    pendingActions.current.set(job.id, { action, messages })
  }, [])

  const watch = useCallback((job: Job) => {
    setActiveJobs(prev => {
      const without = prev.filter(j => j.id !== job.id)
      return [job, ...without]
    })

    const poll = async () => {
      try {
        const latest = await api.job(job.id)
        setActiveJobs(prev => prev.map(j => (j.id === latest.id ? latest : j)))

        if (latest.status === 'pending' || latest.status === 'running') {
          const t = window.setTimeout(poll, 700)
          timers.current.set(job.id, t)
          return
        }

        timers.current.delete(job.id)

        if (latest.status === 'completed') {
          const pending = pendingActions.current.get(latest.id)
          pendingActions.current.delete(latest.id)
          if (pending) {
            patchEmailsCache(queryClient, pending.action, pending.messages)
            queryClient.invalidateQueries({ queryKey: ['group'] })
            queryClient.invalidateQueries({ queryKey: ['sender'] })
          } else if (latest.kind === 'fetch') {
            // One refresh when a pool finishes — not on every account tick.
            queryClient.invalidateQueries({ queryKey: ['emails'] })
            queryClient.invalidateQueries({ queryKey: ['group'] })
            queryClient.invalidateQueries({ queryKey: ['poolStatus'] })
          } else {
            queryClient.invalidateQueries({ queryKey: ['emails'] })
            queryClient.invalidateQueries({ queryKey: ['group'] })
            queryClient.invalidateQueries({ queryKey: ['sender'] })
          }
        } else if (latest.status === 'failed') {
          pendingActions.current.delete(latest.id)
          queryClient.invalidateQueries({ queryKey: ['emails'] })
          queryClient.invalidateQueries({ queryKey: ['group'] })
          queryClient.invalidateQueries({ queryKey: ['poolStatus'] })
        }

        window.setTimeout(() => {
          setActiveJobs(prev => prev.filter(j =>
            j.id !== job.id || j.status === 'failed',
          ))
        }, latest.status === 'failed' ? 8_000 : 4_000)
      } catch {
        const t = window.setTimeout(poll, 1500)
        timers.current.set(job.id, t)
      }
    }
    poll()
  }, [queryClient])

  useEffect(() => {
    return () => {
      for (const t of timers.current.values()) window.clearTimeout(t)
    }
  }, [])

  return { activeJobs, watch, registerAction }
}

/** Boot: wait for recent fetch to finish before exposing the inbox. */
export function useFetchMail(watch: (job: Job) => void, activeJobs: Job[]) {
  const [recentJobId, setRecentJobId] = useState<string | null>(null)
  const [fullJobId, setFullJobId] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const bootstrapped = useRef(false)

  const startFetch = useCallback(async (force: boolean, fullHistory = false) => {
    const history: HistoryMode = fullHistory ? 'full' : 'recent'
    const { job: active } = await api.activeFetchJob(history)
    if (active && (active.status === 'pending' || active.status === 'running')) {
      if (fullHistory) setFullJobId(active.id)
      else setRecentJobId(active.id)
      watch(active)
      return active
    }
    // Warm recent cache: a non-force fetch may complete instantly.
    const job = await api.startFetch(force, fullHistory)
    if (fullHistory) setFullJobId(job.id)
    else setRecentJobId(job.id)
    watch(job)
    return job
  }, [watch])

  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true
    // Force on cold boot so a prior empty/error cache cannot short-circuit load.
    startFetch(true, false).catch(() => setReady(true))
  }, [startFetch])

  const recentFetchJob =
    activeJobs.find(j => j.id === recentJobId)
    ?? activeJobs.find(
      j =>
        isRecentFetchJob(j)
        && (j.status === 'pending' || j.status === 'running' || j.status === 'completed'),
    )
    ?? null

  const fullFetchJob =
    activeJobs.find(j => j.id === fullJobId)
    ?? activeJobs.find(
      j =>
        j.kind === 'fetch'
        && isFullFetchJob(j)
        && (j.status === 'pending' || j.status === 'running'),
    )
    ?? null

  useEffect(() => {
    if (ready) return
    if (!recentFetchJob) return
    if (
      recentFetchJob.status === 'completed'
      || recentFetchJob.status === 'failed'
    ) {
      setReady(true)
    }
  }, [recentFetchJob, ready])

  // If a warm-cache fetch finishes before we attach, poll active once.
  useEffect(() => {
    if (ready || recentJobId) return
    let cancelled = false
    const t = window.setTimeout(async () => {
      try {
        const { job } = await api.activeFetchJob('recent')
        if (cancelled) return
        if (!job) {
          // No job and not ready — try reading emails; warm cache ⇒ ready.
          const snap = await api.emails({ archived: false, history: 'recent' })
          if (!cancelled && snap.emails.length > 0 && !snap.fetching) {
            setReady(true)
          }
        }
      } catch {
        /* ignore */
      }
    }, 2_000)
    return () => {
      cancelled = true
      window.clearTimeout(t)
    }
  }, [ready, recentJobId])

  const recentFetching = Boolean(
    recentFetchJob
    && (recentFetchJob.status === 'pending' || recentFetchJob.status === 'running'),
  )
  const fullFetching = Boolean(
    fullFetchJob
    && (fullFetchJob.status === 'pending' || fullFetchJob.status === 'running'),
  )

  const refresh = useMutation({
    mutationFn: () => startFetch(true, false),
  })

  const loadFullHistory = useMutation({
    mutationFn: () => startFetch(true, true),
  })

  return {
    recentFetchJob,
    fullFetchJob,
    fetchJob: recentFetching ? recentFetchJob : (fullFetchJob || recentFetchJob),
    fetching: recentFetching || fullFetching,
    recentFetching,
    fullFetching,
    ready,
    refresh,
    loadFullHistory,
  }
}

export function useMailActions(
  watch: (job: Job) => void,
  registerAction?: (job: Job, action: string, messages: MessageRef[]) => void,
) {
  const startAction = useMutation({
    mutationFn: (args: {
      action: string,
      messages: MessageRef[],
      account_keys?: string[] | null,
    }) => api.startAction(args),
    onSuccess: (job, vars) => {
      registerAction?.(job, vars.action, vars.messages)
      watch(job)
    },
  })

  const unsubscribe = useMutation({
    mutationFn: (args: { sender: string, account_keys?: string[] | null }) =>
      api.unsubscribe(args.sender, args.account_keys),
    onSuccess: (job) => watch(job),
  })

  const deleteSender = useMutation({
    mutationFn: (args: { sender: string, account_keys?: string[] | null }) =>
      api.deleteSender(args.sender, args.account_keys),
    onSuccess: (job) => watch(job),
  })

  return { startAction, unsubscribe, deleteSender }
}

export type { Message }
