import {useState} from 'react'
import {dashboardModel} from './mockDashboard'
import {SignalsFeed} from './signalsFeed'
import {TrackerFeed} from './trackerFeed'

export function App() {
  const [activePage, setActivePage] = useState(dashboardModel.pages[0]?.id ?? 'tracker')

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__brand">kelly-watcher</div>
        <nav aria-label="Dashboard pages" className="topbar__nav">
          {dashboardModel.pages.map((page) => {
            const isActive = page.id === activePage
            return (
              <button
                key={page.id}
                type="button"
                className={`topbar__link${isActive ? ' topbar__link--active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => setActivePage(page.id)}
              >
                {page.label}
              </button>
            )
          })}
        </nav>
      </header>
      <main aria-label={`${activePage} page`} className="page-canvas">
        {activePage === 'tracker' ? (
          <TrackerFeed mode={dashboardModel.mode} mockEvents={dashboardModel.trackerEvents} />
        ) : activePage === 'signals' ? (
          <SignalsFeed mode={dashboardModel.mode} mockEvents={dashboardModel.signalEvents} />
        ) : null}
      </main>
    </div>
  )
}
