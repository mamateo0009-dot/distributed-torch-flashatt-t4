use dashmap::DashMap;
use parking_lot::Mutex;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use crate::types::{ProxyStatsResponse, ShareLog, WorkerStats};
use crate::upstream::UpstreamManager;

#[derive(Clone)]
pub struct AppState {
    pub pool_host: String,
    pub pool_port: u16,
    pub default_wallet: String,
    #[allow(dead_code)]
    pub default_worker: String,
    #[allow(dead_code)]
    pub agent: String,
    pub admin_pass: String,
    #[allow(dead_code)]
    pub custom_diff: String,
    pub start_time: i64,
    pub upstream_manager: Arc<UpstreamManager>,
    pub total_accepted: Arc<AtomicU64>,
    pub total_rejected: Arc<AtomicU64>,
    pub workers_stats: Arc<DashMap<String, WorkerStats>>,
    pub share_logs: Arc<Mutex<VecDeque<ShareLog>>>,
}

impl AppState {
    pub fn new(
        pool_host: String,
        pool_port: u16,
        default_wallet: String,
        default_worker: String,
        agent: String,
        admin_pass: String,
        custom_diff: String,
    ) -> Self {
        let upstream_manager = Arc::new(UpstreamManager::new(
            pool_host.clone(),
            pool_port,
            default_wallet.clone(),
            agent.clone(),
            custom_diff.clone(),
        ));

        Self {
            pool_host,
            pool_port,
            default_wallet,
            default_worker,
            agent,
            admin_pass,
            custom_diff,
            start_time: chrono::Utc::now().timestamp(),
            upstream_manager,
            total_accepted: Arc::new(AtomicU64::new(0)),
            total_rejected: Arc::new(AtomicU64::new(0)),
            workers_stats: Arc::new(DashMap::new()),
            share_logs: Arc::new(Mutex::new(VecDeque::with_capacity(100))),
        }
    }

    pub fn update_worker_seen(&self, worker_id: &str, ip: &str) {
        let now = chrono::Utc::now().timestamp();
        self.workers_stats
            .entry(worker_id.to_string())
            .and_modify(|entry| {
                entry.last_seen = now;
                if !ip.is_empty() {
                    entry.ip = ip.to_string();
                }
            })
            .or_insert_with(|| WorkerStats {
                worker_id: worker_id.to_string(),
                ip: ip.to_string(),
                first_seen: now,
                last_seen: now,
                shares_accepted: 0,
                shares_rejected: 0,
                reported_hashrate: 0.0,
            });
    }

    pub async fn record_share(&self, worker_id: &str, accepted: bool, hs: f64, job_id: &str) {
        if accepted {
            self.total_accepted.fetch_add(1, Ordering::Relaxed);
        } else {
            self.total_rejected.fetch_add(1, Ordering::Relaxed);
        }

        let now = chrono::Utc::now().timestamp();

        self.workers_stats
            .entry(worker_id.to_string())
            .and_modify(|w| {
                w.last_seen = now;
                if hs > 0.0 {
                    w.reported_hashrate = hs;
                }
                if accepted {
                    w.shares_accepted += 1;
                } else {
                    w.shares_rejected += 1;
                }
            })
            .or_insert_with(|| WorkerStats {
                worker_id: worker_id.to_string(),
                ip: "127.0.0.1".to_string(),
                first_seen: now,
                last_seen: now,
                shares_accepted: if accepted { 1 } else { 0 },
                shares_rejected: if accepted { 0 } else { 1 },
                reported_hashrate: hs,
            });

        let mut logs = self.share_logs.lock();
        if logs.len() >= 100 {
            logs.pop_back();
        }
        logs.push_front(ShareLog {
            timestamp: now,
            worker_id: worker_id.to_string(),
            job_id: job_id.to_string(),
            accepted,
            hashrate: hs,
        });
    }

    pub fn get_stats(&self) -> ProxyStatsResponse {
        let now = chrono::Utc::now().timestamp();
        let mut total_hashrate = 0.0;
        let mut active_workers = 0;
        let mut workers_list = Vec::with_capacity(self.workers_stats.len());

        for entry in self.workers_stats.iter() {
            let w = entry.value();
            if now - w.last_seen < 60 {
                active_workers += 1;
                total_hashrate += w.reported_hashrate;
            }
            workers_list.push(w.clone());
        }

        // Sort workers by last_seen descending
        workers_list.sort_by(|a, b| b.last_seen.cmp(&a.last_seen));

        let recent_shares = {
            let logs = self.share_logs.lock();
            logs.iter().cloned().collect()
        };

        // Find latest job height from any active session
        let mut latest_job_id = None;
        let mut current_block_height = None;
        for entry in self.upstream_manager.sessions.iter() {
            let session = entry.value();
            if let Some(ref job) = *session.latest_job.read() {
                latest_job_id = Some(job.job_id.clone());
                current_block_height = job.height;
                break;
            }
        }

        ProxyStatsResponse {
            pool_host: format!("{}:{}", self.pool_host, self.pool_port),
            default_wallet: self.default_wallet.clone(),
            uptime_seconds: (now - self.start_time).max(0) as u64,
            active_workers,
            total_shares_accepted: self.total_accepted.load(Ordering::Relaxed),
            total_shares_rejected: self.total_rejected.load(Ordering::Relaxed),
            total_hashrate,
            current_job_id: latest_job_id,
            current_block_height,
            workers: workers_list,
            recent_shares,
        }
    }
}
