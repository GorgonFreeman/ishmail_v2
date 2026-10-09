import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Job, type MessageRef } from './api'

export type HistoryMode = 'recent' | 'full'

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
    // Poll the active view while its pool is fetching; also poll while full
    // history loads in the background so full_ready/full_fetching update.
    refetchInterval: (query) => {
      const d = query.state.data
      if (d?.fetching || d?.full_fetching) return 1_200
      return 60_000
    },
    enabled,
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

export function useTrackJob() {
  const queryClient = useQueryClient()
  const [activeJobs, setActiveJobs] = useState<Job[]>([])
  const timers = useRef<Map<string, number>>(new Map())

  const invalidateMail = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['emails'] })
    queryClient.invalidateQueries({ queryKey: ['sender'] })
    queryClient.invalidateQueries({ queryKey: ['jobs'] })
  }, [queryClient])

  const watch = useCallback((job: Job) => {
    setActiveJobs(prev => {
      const without = prev.filter(j => j.id !== job.id)
      return [job, ...without]
    })

    let lastDone: unknown
    const poll = async () => {
      try {
        const latest = await api.job(job.id)
        setActiveJobs(prev => prev.map(j => (j.id === latest.id ? latest : j)))
        // Refresh email queries as fetch progress lands (per-pool caches).
        if (latest.kind === 'fetch' && latest.progress?.done !== lastDone) {
          lastDone = latest.progress?.done
          queryClient.invalidateQueries({ queryKey: ['emails'] })
        }
        if (latest.status === 'pending' || latest.status === 'running') {
          const t = window.setTimeout(poll, 700)
          timers.current.set(job.id, t)
        } else {
          timers.current.delete(job.id)
          invalidateMail()
          window.setTimeout(() => {
            setActiveJobs(prev => prev.filter(j => j.id !== job.id || j.status === 'failed'))
          }, 4_000)
        }
      } catch {
        const t = window.setTimeout(poll, 1500)
        timers.current.set(job.id, t)
      }
    }
    poll()
  }, [invalidateMail, queryClient])

  useEffect(() => {
    return () => {
      for (const t of timers.current.values()) window.clearTimeout(t)
    }
  }, [])

  return { activeJobs, watch }
}

/** Start a background fetch on mount; Refresh forces a re-fetch of the view pool. */
export function useFetchMail(watch: (job: Job) => void, activeJobs: Job[]) {
  const [fetchJobId, setFetchJobId] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const bootstrapped = useRef(false)

  const attachFetchJob = useCallback((job: Job) => {
    setFetchJobId(job.id)
    watch(job)
  }, [watch])

  const startFetch = useCallback(async (force: boolean, fullHistory = false) => {
    const history: HistoryMode = fullHistory ? 'full' : 'recent'
    const { job: active } = await api.activeFetchJob(history)
    if (active && (active.status === 'pending' || active.status === 'running')) {
      attachFetchJob(active)
      return active
    }
    const job = await api.startFetch(force, fullHistory)
    attachFetchJob(job)
    return job
  }, [attachFetchJob])

  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true
    setReady(true)
    startFetch(false, false).catch(() => setReady(true))
  }, [startFetch])

  const fetchJob =
    activeJobs.find(j => j.id === fetchJobId)
    ?? activeJobs.find(
      j => j.kind === 'fetch' && (j.status === 'pending' || j.status === 'running'),
    )
    ?? null

  useEffect(() => {
    if (fetchJob && fetchJob.id !== fetchJobId) {
      setFetchJobId(fetchJob.id)
    }
  }, [fetchJob, fetchJobId])

  useEffect(() => {
    if (fetchJob?.status === 'completed' || fetchJob?.status === 'failed') {
      setReady(true)
    }
  }, [fetchJob])

  const fetching =
    Boolean(fetchJob) &&
    (fetchJob!.status === 'pending' || fetchJob!.status === 'running')

  const recentFetching = fetching && fetchJob?.progress?.history !== 'full'
  const fullFetching = Boolean(
    activeJobs.some(
      j =>
        j.kind === 'fetch'
        && (j.status === 'pending' || j.status === 'running')
        && (j.progress?.full_history === true || j.progress?.history === 'full'),
    ),
  )

  const refresh = useMutation({
    mutationFn: (vars: { fullHistory?: boolean }) =>
      startFetch(true, Boolean(vars?.fullHistory)),
  })

  const loadFullHistory = useMutation({
    mutationFn: () => startFetch(true, true),
  })

  return {
    fetchJob,
    fetching,
    recentFetching,
    fullFetching,
    ready,
    refresh,
    loadFullHistory,
  }
}

export function useMailActions(watch: (job: Job) => void) {
  const startAction = useMutation({
    mutationFn: (args: {
      action: string,
      messages: MessageRef[],
      account_keys?: string[] | null,
    }) => api.startAction(args),
    onSuccess: (job) => watch(job),
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
