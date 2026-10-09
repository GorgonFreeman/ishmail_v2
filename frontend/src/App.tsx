import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import type { Job } from './api'
import { EmailDetailPage } from './EmailDetailPage'
import { InboxPage } from './InboxPage'
import { SenderPage } from './SenderPage'
import { useAccounts, useFetchMail, useTrackJob } from './hooks'
import './App.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 15_000,
    },
  },
})

const ACTION_LABELS: Record<string, string> = {
  delete: 'Delete',
  archive: 'Archive',
  star: 'Star',
  unstar: 'Unstar',
  unsubscribe: 'Unsubscribe',
  'delete-sender': 'Delete sender',
  fetch: 'Fetch',
}

function jobLabel(job: Job) {
  if (job.kind === 'fetch') {
    const full = job.progress?.full_history === true || job.progress?.history === 'full'
    return full ? 'Load all time' : 'Load 6 months'
  }
  return ACTION_LABELS[job.kind] || job.kind
}

function JobToast({ jobs }: { jobs: Job[] }) {
  if (!jobs.length) return null

  // Prefer action jobs over fetch noise in the primary slot.
  const sorted = [...jobs].sort((a, b) => {
    const aFetch = a.kind === 'fetch' ? 1 : 0
    const bFetch = b.kind === 'fetch' ? 1 : 0
    return aFetch - bFetch
  })

  return (
    <div className="jobToast">
      {sorted.map(j => {
        const done = typeof j.progress?.done === 'number' ? j.progress.done : null
        const total = typeof j.progress?.total === 'number' ? j.progress.total : null
        const current =
          typeof j.progress?.current === 'string' && j.progress.current
            ? j.progress.current
            : null
        return (
          <div key={j.id} className={`jobItem status-${j.status}`}>
            <strong>{jobLabel(j)}</strong>
            <span>{j.status}</span>
            {done != null && total != null && total > 0 && (
              <span>{done}/{total}</span>
            )}
            {current && <span className="jobCurrent">{current}</span>}
            {j.error && <span className="jobError">{j.error.split('\n')[0]}</span>}
          </div>
        )
      })}
    </div>
  )
}

function Shell() {
  const { data, isLoading, error } = useAccounts()
  const { activeJobs, watch, registerAction } = useTrackJob()
  const {
    recentFetchJob,
    fullFetchJob,
    recentFetching,
    fullFetching,
    ready,
    refresh,
    loadFullHistory,
  } = useFetchMail(watch, activeJobs)
  const accounts = data?.accounts || []

  return (
    <div className="appShell">
      <JobToast jobs={activeJobs} />

      {isLoading && <div className="emptyState">Loading accounts…</div>}
      {error && (
        <div className="errorBanner">
          Failed to load accounts. Is the API running? {(error as Error).message}
        </div>
      )}
      {!isLoading && !error && (
        <Routes>
          <Route
            path="/"
            element={
              <InboxPage
                accounts={accounts}
                watch={watch}
                registerAction={registerAction}
                recentFetchJob={recentFetchJob}
                fullFetchJob={fullFetchJob}
                recentFetching={recentFetching}
                fullFetching={fullFetching}
                mailReady={ready}
                onRefresh={() => refresh.mutateAsync()}
                refreshPending={refresh.isPending || recentFetching}
                onLoadFullHistory={() => loadFullHistory.mutateAsync()}
                loadFullHistoryPending={loadFullHistory.isPending}
              />
            }
          />
          <Route
            path="/sender/:senderEmail"
            element={
              <SenderPage
                accounts={accounts}
                watch={watch}
                mailReady={ready}
              />
            }
          />
          <Route
            path="/email/:groupId"
            element={
              <EmailDetailPage
                accounts={accounts}
                watch={watch}
                registerAction={registerAction}
                mailReady={ready}
              />
            }
          />
        </Routes>
      )}
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
