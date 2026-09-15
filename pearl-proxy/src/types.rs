use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MiningNotify {
    pub job_id: String,
    pub header: String,
    pub target: String,
    #[serde(default = "default_diff")]
    pub diff: f64,
    #[serde(default = "default_cert_version")]
    pub cert_version: u32,
    pub height: Option<u64>,
}

fn default_diff() -> f64 {
    1.0
}

fn default_cert_version() -> u32 {
    3
}

impl MiningNotify {
    pub fn from_json_value(val: &serde_json::Value, fallback_diff: f64) -> Option<Self> {
        if let Some(obj) = val.as_object() {
            let job_id = obj.get("job_id")?.as_str()?.to_string();
            let header = obj.get("header")?.as_str()?.to_string();
            let target = obj.get("target").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let diff = obj.get("diff").and_then(|v| v.as_f64()).unwrap_or(fallback_diff);
            let cert_version = obj.get("cert_version").and_then(|v| v.as_u64()).map(|v| v as u32).unwrap_or(3);
            let height = obj.get("height").and_then(|v| v.as_u64());

            Some(MiningNotify {
                job_id,
                header,
                target,
                diff,
                cert_version,
                height,
            })
        } else if let Some(arr) = val.as_array() {
            if arr.len() < 3 {
                return None;
            }
            let job_id = match &arr[0] {
                serde_json::Value::String(s) => s.clone(),
                serde_json::Value::Number(n) => n.to_string(),
                _ => return None,
            };
            let header = arr[1].as_str()?.to_string();
            let target = arr.get(2).and_then(|v| v.as_str()).unwrap_or("").to_string();
            let cert_version = arr.get(3).and_then(|v| {
                if let Some(n) = v.as_u64() {
                    Some(n as u32)
                } else if let Some(s) = v.as_str() {
                    s.parse::<u32>().ok()
                } else {
                    None
                }
            }).unwrap_or(3);
            let height = arr.get(4).and_then(|v| {
                if let Some(n) = v.as_u64() {
                    Some(n)
                } else if let Some(s) = v.as_str() {
                    s.parse::<u64>().ok()
                } else {
                    None
                }
            });

            Some(MiningNotify {
                job_id,
                header,
                target,
                diff: fallback_diff,
                cert_version,
                height,
            })
        } else {
            None
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PoolJsonRpc {
    pub id: Option<serde_json::Value>,
    pub method: Option<String>,
    pub params: Option<serde_json::Value>,
    pub result: Option<serde_json::Value>,
    pub error: Option<serde_json::Value>,
    #[serde(rename = "type")]
    pub protocol_type: Option<String>,
}

impl PoolJsonRpc {
    pub fn get_u64_id(&self) -> Option<u64> {
        match &self.id {
            Some(serde_json::Value::Number(n)) => n.as_u64(),
            Some(serde_json::Value::String(s)) => s.parse::<u64>().ok(),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatCompletionRequest {
    pub model: Option<String>,
    #[serde(default)]
    pub messages: Vec<ChatMessage>,
    pub stream: Option<bool>,
    pub temperature: Option<f64>,
    pub max_tokens: Option<u32>,
    pub user: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatChoiceDelta {
    pub content: Option<String>,
    pub role: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatChoice {
    pub index: u32,
    pub delta: ChatChoiceDelta,
    pub finish_reason: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatCompletionChunk {
    pub id: String,
    pub object: String,
    pub created: i64,
    pub model: String,
    pub choices: Vec<ChatChoice>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbeddingRequest {
    pub model: Option<String>,
    pub input: serde_json::Value,
    pub user: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbeddingItem {
    pub object: String,
    pub index: usize,
    pub embedding: Vec<f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbeddingUsage {
    pub prompt_tokens: u32,
    pub total_tokens: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbeddingResponse {
    pub object: String,
    pub data: Vec<EmbeddingItem>,
    pub model: String,
    pub usage: EmbeddingUsage,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelItem {
    pub id: String,
    pub object: String,
    pub created: i64,
    pub owned_by: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelListResponse {
    pub object: String,
    pub data: Vec<ModelItem>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkerStats {
    pub worker_id: String,
    pub ip: String,
    pub first_seen: i64,
    pub last_seen: i64,
    pub shares_accepted: u64,
    pub shares_rejected: u64,
    pub reported_hashrate: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShareLog {
    pub timestamp: i64,
    pub worker_id: String,
    pub job_id: String,
    pub accepted: bool,
    pub hashrate: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProxyStatsResponse {
    pub pool_host: String,
    pub default_wallet: String,
    pub uptime_seconds: u64,
    pub active_workers: usize,
    pub total_shares_accepted: u64,
    pub total_shares_rejected: u64,
    pub total_hashrate: f64,
    pub current_job_id: Option<String>,
    pub current_block_height: Option<u64>,
    pub workers: Vec<WorkerStats>,
    pub recent_shares: Vec<ShareLog>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mining_notify_dict_parsing() {
        let json_str = r#"{"job_id": "1001", "header": "abcdef", "target": "0000ffff", "diff": 64.0, "cert_version": 3, "height": 45000}"#;
        let v: serde_json::Value = serde_json::from_str(json_str).unwrap();
        let notify = MiningNotify::from_json_value(&v, 1.0).unwrap();
        assert_eq!(notify.job_id, "1001");
        assert_eq!(notify.diff, 64.0);
        assert_eq!(notify.height, Some(45000));
    }

    #[test]
    fn test_mining_notify_array_parsing() {
        let json_str = r#"["2002", "fedcba", "00001111", 3, 50000]"#;
        let v: serde_json::Value = serde_json::from_str(json_str).unwrap();
        let notify = MiningNotify::from_json_value(&v, 32.0).unwrap();
        assert_eq!(notify.job_id, "2002");
        assert_eq!(notify.diff, 32.0);
        assert_eq!(notify.height, Some(50000));
    }

    #[test]
    fn test_pool_rpc_id_parsing() {
        let rpc_num = PoolJsonRpc {
            id: Some(serde_json::json!(42)),
            method: None,
            params: None,
            result: Some(serde_json::json!(true)),
            error: None,
            protocol_type: None,
        };
        assert_eq!(rpc_num.get_u64_id(), Some(42));

        let rpc_str = PoolJsonRpc {
            id: Some(serde_json::json!("108")),
            method: None,
            params: None,
            result: Some(serde_json::json!(true)),
            error: None,
            protocol_type: None,
        };
        assert_eq!(rpc_str.get_u64_id(), Some(108));
    }
}
