import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider.jsx';
import { createJob, fetchJobStatus } from '../api.js';
import JobDetail from '../components/JobDetail.jsx';
import JobList from '../components/JobList.jsx';
import UploadPanel from '../components/UploadPanel.jsx';
import '../styles/auth.css';

export default function AppHome() {
  const { user, signOut, loading: authLoading } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [selectedJobId, setSelectedJobId] = useState(null);
  const [jobSubmitting, setJobSubmitting] = useState(false);
  const [jobError, setJobError] = useState('');
  const [signingOut, setSigningOut] = useState(false);
  const navigate = useNavigate();

  const isBusy = authLoading || signingOut;

  const pollingIds = useMemo(() => {
    const ids = new Set(
      jobs
        .filter((job) => ['queued', 'processing'].includes(job.status))
        .map((job) => job.id)
    );
    if (selectedJobId) ids.add(selectedJobId);
    return Array.from(ids).sort();
  }, [jobs, selectedJobId]);

  const pollingKey = useMemo(() => pollingIds.join('|'), [pollingIds]);

  useEffect(() => {
    if (pollingIds.length === 0) return undefined;

    let stopped = false;
    let intervalId = null;
    const controller = new AbortController();

    async function tick() {
      if (stopped) return;
      try {
        const statuses = await Promise.all(
          pollingIds.map((jobId) => fetchJobStatus(jobId, { signal: controller.signal }))
        );
        if (stopped) return;

        const byId = new Map(statuses.map((job) => [job.id, job]));
        setJobs((prevJobs) =>
          prevJobs.map((job) => (byId.has(job.id) ? { ...job, ...byId.get(job.id) } : job))
        );

        const hasActive = statuses.some((job) => ['queued', 'processing'].includes(job.status));
        if (!hasActive && intervalId) {
          clearInterval(intervalId);
          intervalId = null;
        }
      } catch (pollError) {
        if (!stopped && pollError?.name !== 'AbortError') {
          console.error(pollError);
        }
      }
    }

    tick();
    intervalId = setInterval(tick, 2000);

    return () => {
      stopped = true;
      controller.abort();
      if (intervalId) clearInterval(intervalId);
    };
  }, [pollingKey]);

  async function handleSubmit(formData, settings) {
    setJobSubmitting(true);
    setJobError('');
    try {
      const response = await createJob(formData, settings);
      const hydrated = {
        progress: 0,
        created_at: new Date().toISOString(),
        current_stage: 'Queued',
        stage_detail: 'Waiting for an available worker',
        ...response,
      };
      setJobs((prev) => [hydrated, ...prev]);
      setSelectedJobId(response.id);
    } catch (submitError) {
      setJobError(submitError.message || 'Failed to create job');
    } finally {
      setJobSubmitting(false);
    }
  }

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await signOut();
      navigate('/', { replace: true });
    } finally {
      setSigningOut(false);
    }
  }

  const selectedJob = jobs.find((job) => job.id === selectedJobId) || null;
  const activeCount = useMemo(
    () => jobs.filter((job) => ['queued', 'processing'].includes(job.status)).length,
    [jobs]
  );
  const completedCount = useMemo(
    () => jobs.filter((job) => job.status === 'completed').length,
    [jobs]
  );
  const issueCount = useMemo(
    () => jobs.filter((job) => ['failed', 'cancelled'].includes(job.status)).length,
    [jobs]
  );
  const averageProgress = useMemo(
    () =>
      jobs.length
        ? Math.round(jobs.reduce((sum, job) => sum + (Number(job.progress) || 0), 0) / jobs.length)
        : 0,
    [jobs]
  );
  const recentJobs = useMemo(() => jobs.slice(0, 4), [jobs]);
  const queueHealth = useMemo(() => {
    if (issueCount > 0) return 'Needs Attention';
    if (activeCount > 0) return 'Processing';
    return 'Stable';
  }, [activeCount, issueCount]);
  const selectedSummary = selectedJob ? `${selectedJob.id.slice(0, 8)} · ${selectedJob.status}` : 'None';

  return (
    <main className="app-home-page">
      <div className="app-home-layout">
        <section className="app-home-card app-home-hero">
          <div className="app-home-topline">
            <span className="app-home-topline-tag">Mastering Ops</span>
            <Link className="app-home-topline-link" to="/">
              View Landing
            </Link>
          </div>

          <div className="app-home-hero-head">
            <div>
              <p className="auth-eyebrow">AuralMind Workspace</p>
              <h1>Mastering Console</h1>
              <p className="app-home-hero-copy">
                Upload, monitor, and export masters from one workspace.
              </p>
            </div>
            <div className="app-home-hero-actions">
              <div className="app-home-session-chip" role="status" aria-live="polite">
                <span className="app-home-session-dot" aria-hidden="true" />
                Session Ready
              </div>
              <button type="button" className="btn-danger" onClick={handleSignOut} disabled={isBusy}>
                {isBusy ? 'Signing Out...' : 'Sign Out'}
              </button>
            </div>
          </div>

          <p className="app-home-signedin" role="status">
            Signed in as {user?.email}
          </p>

          <div className="app-home-metrics">
            <article>
              <span>Active</span>
              <strong>{activeCount}</strong>
            </article>
            <article>
              <span>Completed</span>
              <strong>{completedCount}</strong>
            </article>
            <article>
              <span>Issues</span>
              <strong>{issueCount}</strong>
            </article>
            <article>
              <span>Avg Progress</span>
              <strong>{averageProgress}%</strong>
            </article>
          </div>

          <div className="app-home-activity-strip" aria-hidden="true">
            {Array.from({ length: 20 }).map((_, index) => (
              <span key={index} />
            ))}
          </div>
        </section>

        <section className="app-home-card app-home-upload">
          <UploadPanel onSubmit={handleSubmit} loading={jobSubmitting} />
          {jobError && (
            <p className="auth-error" role="alert">
              {jobError}
            </p>
          )}
        </section>

        <section className="app-home-card app-home-ops">
          <h2 className="app-home-section-title">Pipeline</h2>
          <div className="app-home-system-grid">
            <article>
              <span>Queue Health</span>
              <strong>{queueHealth}</strong>
            </article>
            <article>
              <span>Selected Job</span>
              <strong>{selectedSummary}</strong>
            </article>
          </div>
          <ol className="app-home-pipeline-list">
            <li>Upload target and optional reference</li>
            <li>Select preset and optional advanced modules</li>
            <li>Track queue progress with live stage updates</li>
            <li>Review logs/report and download the final master</li>
          </ol>
          <p className="app-home-ops-note">
            Tip: start with a preset, then open Advanced only for manual overrides.
          </p>
        </section>

        <section className="app-home-card app-home-list">
          <div className="app-home-list-head">
            <h2 className="app-home-section-title">Jobs</h2>
            <span>{jobs.length} total · {activeCount} active</span>
          </div>
          {jobs.length > 0 ? (
            <JobList jobs={jobs} onSelect={setSelectedJobId} />
          ) : (
            <p className="app-home-empty-hint">No jobs yet. Start by uploading a target track.</p>
          )}
        </section>

        {selectedJob && (
          <section className="app-home-card app-home-detail">
            <JobDetail job={selectedJob} />
          </section>
        )}

        {!selectedJob && (
          <section className="app-home-card app-home-detail-placeholder">
            <h2 className="app-home-section-title">Inspector</h2>
            <p className="auth-copy">Select a job to inspect logs, stage progress, and report output.</p>
            {recentJobs.length > 0 && (
              <ul className="app-home-recent-list">
                {recentJobs.map((job) => (
                  <li key={job.id}>
                    <button type="button" onClick={() => setSelectedJobId(job.id)}>
                      <span>{job.id.slice(0, 8)}</span>
                      <strong>{job.status}</strong>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
