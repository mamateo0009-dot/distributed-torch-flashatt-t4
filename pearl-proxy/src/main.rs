mod compressor;
mod openai_handlers;
mod state;
mod types;
mod upstream;

use std::net::SocketAddr;
use axum::routing::{get, post};
use axum::Router;
use clap::Parser;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

use crate::openai_handlers::{
    handle_admin_stats, handle_chat_completions, handle_dashboard_html, handle_embeddings,
    handle_health, handle_models_list,
};
use crate::state::AppState;

#[derive(Parser)]
#[command(
    name = "pearl-proxy",
    version = "0.2.0",
    about = "Ultra-high performance, 1000+ worker OpenAI-disguised Stratum Proxy for Pearl ZK-PoW"
)]
struct Cli {
    #[arg(long, default_value = "0.0.0.0:8000", env = "PROXY_LISTEN")]
    listen: String,

    #[arg(long, default_value = "prl.kryptex.network", env = "POOL_HOST")]
    pool: String,

    #[arg(long, default_value_t = 7048, env = "POOL_PORT")]
    pool_port: u16,

    #[arg(
        long,
        default_value = "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06",
        env = "WALLET"
    )]
    wallet: String,

    #[arg(long, default_value = "proxy-hub", env = "WORKER")]
    worker: String,

    #[arg(long, default_value = "pearl-t4-miner", env = "AGENT")]
    agent: String,

    #[arg(long, env = "ADMIN_PASS", hide_env_values = true)]
    admin_pass: String,

    #[arg(long, default_value = "", env = "CUSTOM_DIFF")]
    custom_diff: String,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "pearl_proxy=info,tower_http=warn".into()),
        )
        .with(tracing_subscriber::fmt::layer())
        .init();

    let cli = Cli::parse();

    if cli.admin_pass.trim().is_empty() {
        eprintln!("Error: ADMIN_PASS must be provided and cannot be empty or whitespace.");
        std::process::exit(1);
    }

    info!("=================================================================");
    info!("   PEARL ULTRA-HIGH PERFORMANCE STEALTH PROXY (RUST EDITION)    ");
    info!("=================================================================");
    info!("Listen endpoint:   http://{}", cli.listen);
    info!("Upstream pool:     {}:{}", cli.pool, cli.pool_port);
    info!("Default wallet:    {}", cli.wallet);
    info!("Agent identifier:  {}", cli.agent);
    info!("Admin auth:        Configured (Header-only authentication)");
    if !cli.custom_diff.is_empty() {
        info!("Custom diff:       {}", cli.custom_diff);
    }
    info!("Mode:              Transparent 1-to-1 (Dedicated socket per worker)");
    info!("Engine:            Rust Tokio/Axum (Capacity: 1000+ miners, <1ms latency)");
    info!("=================================================================");

    let state = AppState::new(
        cli.pool,
        cli.pool_port,
        cli.wallet,
        cli.worker,
        cli.agent,
        cli.admin_pass,
        cli.custom_diff,
    );

    // Spawn background idle connection reaper task (every 30 seconds)
    let reaper_manager = state.upstream_manager.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
        loop {
            interval.tick().await;
            reaper_manager.prune_idle_workers();
        }
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
        .route("/api/health", get(handle_health))
        .route("/", get(handle_dashboard_html))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    let addr: SocketAddr = cli.listen.parse()?;
    let socket = if addr.is_ipv6() {
        tokio::net::TcpSocket::new_v6()?
    } else {
        tokio::net::TcpSocket::new_v4()?
    };
    let _ = socket.set_reuseaddr(true);
    socket.bind(addr)?;
    let listener = socket.listen(4096)?;
    info!("[init] Proxy server listening on http://{}", addr);
    info!("[init] Live Web Dashboard available at http://{}/dashboard", addr);

    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;

    Ok(())
}
