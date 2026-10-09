import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
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

function Shell() {
  const { data, isLoading, error } = useAccounts()
  const { activeJobs, watch } = useTrackJob()
  const {
    fetchJob,
    fetching,
    fullFetching,
    ready,
    refresh,
    loadFullHistory,
  } = useFetchMail(watch, activeJobs)
  const accounts = data?.accounts || []

  return (
    <div className="appShell">
      {activeJobs.length > 0 && (
        <div className="jobToast">
          {activeJobs.map(j => (
            <div key={j.id} className={`jobItem status-${j.status}`}>
              <strong>{j.kind}</strong>
              <span>{j.status}</span>
              {j.progress && typeof j.progress.done === 'number' && (
                <span>{String(j.progress.done)}/{String(j.progress.total ?? '?')}</span>
              )}
              {typeof j.progress?.current === 'string' && j.progress.current && (
                <span className="jobCurrent">{String(j.progress.current)}</span>
              )}
              {j.error && <span className="jobError">{j.error.split('\n')[0]}</span>}
            </div>
          ))}
        </div>
      )}

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
                fetchJob={fetchJob}
                fetching={fetching}
                fullFetching={fullFetching}
                mailReady={ready}
                onRefresh={(fullHistory = false) =>
                  refresh.mutateAsync({ fullHistory })
                }
                refreshPending={refresh.isPending || fetching}
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
