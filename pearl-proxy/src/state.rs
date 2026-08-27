use std::collections::HashMap;
use std::sync::Arc;
use serde::{Deserialize, Serialize};
use tokio::sync::{broadcast, mpsc, oneshot, RwLock};

use crate::types::{MiningNotify, WorkerStats};

pub enum UpstreamCommand {
    SubmitShare {
        job_id: String,
        plain_proof: String,
        hs: f64,
        worker_id: String,
        response_tx: oneshot::Sender<Result<bool, String>>,
    },
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct ShareLog {
    pub timestamp: i64,
    pub worker_id: String,
    pub job_id: String,
    pub accepted: bool,
    pub hashrate: f64,
}

#[derive(Clone)]
pub struct AppState {
    pub pool_host: String,
    pub pool_port: u16,
    pub default_wallet: String,
    pub default_worker: String,
    pub agent: String,
    pub admin_pass: String,
    pub start_time: i64,
    pub job_broadcast_tx: broadcast::Sender<MiningNotify>,
    pub latest_job: Arc<RwLock<Option<MiningNotify>>>,
    pub workers: Arc<RwLock<HashMap<String, WorkerStats>>>,
    pub upstream_tx: mpsc::Sender<UpstreamCommand>,
    pub total_accepted: Arc<RwLock<u64>>,
    pub total_rejected: Arc<RwLock<u64>>,
    pub share_logs: Arc<RwLock<Vec<ShareLog>>>,
}

impl AppState {
    pub fn new(
        pool_host: String,
        pool_port: u16,
        default_wallet: String,
        default_worker: String,
        agent: String,
        admin_pass: String,
        upstream_tx: mpsc::Sender<UpstreamCommand>,
    ) -> Self {
        let (job_broadcast_tx, _) = broadcast::channel(64);
        Self {
            pool_host,
            pool_port,
            default_wallet,
            default_worker,
            agent,
            admin_pass,
            start_time: chrono::Utc::now().timestamp(),
            job_broadcast_tx,
            latest_job: Arc::new(RwLock::new(None)),
            workers: Arc::new(RwLock::new(HashMap::new())),
            upstream_tx,
            total_accepted: Arc::new(RwLock::new(0)),
            total_rejected: Arc::new(RwLock::new(0)),
            share_logs: Arc::new(RwLock::new(Vec::new())),
        }
    }

    pub async fn update_worker_seen(&self, worker_id: &str, ip: &str) {
        let mut workers = self.workers.write().await;
        let now = chrono::Utc::now().timestamp();
        let entry = workers.entry(worker_id.to_string()).or_insert_with(|| WorkerStats {
            worker_id: worker_id.to_string(),
            ip: ip.to_string(),
            first_seen: now,
            last_seen: now,
            shares_accepted: 0,
            shares_rejected: 0,
            reported_hashrate: 0.0,
        });
        entry.last_seen = now;
        entry.ip = ip.to_string();
    }

    pub async fn record_share(&self, worker_id: &str, accepted: bool, hs: f64, job_id: &str) {
        if accepted {
            let mut acc = self.total_accepted.write().await;
            *acc += 1;
        } else {
            let mut rej = self.total_rejected.write().await;
            *rej += 1;
        }

        let now = chrono::Utc::now().timestamp();
        {
            let mut logs = self.share_logs.write().await;
            logs.insert(
                0,
                ShareLog {
                    timestamp: now,
                    worker_id: worker_id.to_string(),
                    job_id: job_id.to_string(),
                    accepted,
                    hashrate: hs,
                },
            );
            if logs.len() > 100 {
                logs.truncate(100);
            }
        }

        let mut workers = self.workers.write().await;
        if let Some(w) = workers.get_mut(worker_id) {
            if accepted {
                w.shares_accepted += 1;
            } else {
                w.shares_rejected += 1;
            }
            if hs > 0.0 {
                w.reported_hashrate = hs;
            }
            w.last_seen = now;
        }
    }
}
