import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Job, type MessageRef } from './api'

export function useEmails(archived: boolean, q: string) {
  return useQuery({
    queryKey: ['emails', archived, q],
    queryFn: () => api.emails({ archived, q: q || undefined }),
    refetchInterval: 60_000,
  })
}

export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: () => api.accounts(),
  })
}

export function useSender(email: string | undefined) {
  return useQuery({
    queryKey: ['sender', email],
    queryFn: () => api.sender(email!),
    enabled: Boolean(email),
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

    const poll = async () => {
      try {
        const latest = await api.job(job.id)
        setActiveJobs(prev => prev.map(j => (j.id === latest.id ? latest : j)))
        if (latest.status === 'pending' || latest.status === 'running') {
          const t = window.setTimeout(poll, 700)
          timers.current.set(job.id, t)
        } else {
          timers.current.delete(job.id)
          invalidateMail()
          // keep completed jobs visible briefly
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
  }, [invalidateMail])

  useEffect(() => {
    return () => {
      for (const t of timers.current.values()) window.clearTimeout(t)
    }
  }, [])

  return { activeJobs, watch }
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

  const refresh = useMutation({
    mutationFn: () => api.refresh(),
  })

  return { startAction, unsubscribe, deleteSender, refresh }
}
