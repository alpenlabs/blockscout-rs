use super::{
    metrics::Metrics,
    router::{configure_router, HttpRouter},
    settings::{MetricsSettings, ServerSettings},
    span_builder::CompactRootSpanBuilder,
    HttpServerSettings,
};
use actix_web::{middleware::Condition, App, HttpServer};
use actix_web_prom::PrometheusMetrics;
use std::net::SocketAddr;
use tokio_util::sync::CancellationToken;
use tracing_actix_web::TracingLogger;

pub(crate) const SHUTDOWN_TIMEOUT_SEC: u64 = 10;

pub struct LaunchSettings {
    pub service_name: String,
    pub server: ServerSettings,
    pub metrics: MetricsSettings,
}

pub async fn launch<R>(
    settings: &LaunchSettings,
    http: R,
    grpc: tonic::transport::server::Router,
    shutdown: Option<CancellationToken>,
) -> Result<(), anyhow::Error>
where
    R: HttpRouter + Send + Sync + Clone + 'static,
{
    let metrics = settings
        .metrics
        .enabled
        .then(|| Metrics::new(&settings.service_name, &settings.metrics.route));

    let mut futures = vec![];

    if settings.server.http.enabled {
        let http_server = {
            let http_server_future = http_serve(
                http,
                metrics
                    .as_ref()
                    .map(|metrics| metrics.http_middleware().clone()),
                &settings.server.http,
                shutdown.clone(),
            );
            tokio::spawn(async move { http_server_future.await.map_err(anyhow::Error::msg) })
        };
        futures.push(http_server)
    }

    if settings.server.grpc.enabled {
        let grpc_server = {
            let grpc_server_future = grpc_serve(grpc, settings.server.grpc.addr, shutdown.clone());
            tokio::spawn(async move { grpc_server_future.await.map_err(anyhow::Error::msg) })
        };
        futures.push(grpc_server)
    }

    if let Some(metrics) = metrics {
        let addr = settings.metrics.addr;
        futures.push(tokio::spawn(async move {
            metrics.run_server(addr, shutdown).await?;
            Ok(())
        }));
    }

    let (res, _, others) = futures::future::select_all(futures).await;
    for future in others.into_iter() {
        future.abort()
    }
    res?
}

pub(crate) async fn stop_actix_server_on_cancel(
    actix_handle: actix_web::dev::ServerHandle,
    shutdown: CancellationToken,
    graceful: bool,
) {
    shutdown.cancelled().await;
    tracing::info!(
        "Shutting down actix server (gracefully: {graceful}).\
        Should finish within {SHUTDOWN_TIMEOUT_SEC} seconds..."
    );
    actix_handle.stop(graceful).await;
}

pub(crate) async fn grpc_cancel_signal(shutdown: CancellationToken) {
    shutdown.cancelled().await;
    tracing::info!("Shutting down grpc server...");
}

fn http_serve<R>(
    http: R,
    metrics: Option<PrometheusMetrics>,
    settings: &HttpServerSettings,
    shutdown: Option<CancellationToken>,
) -> actix_web::dev::Server
where
    R: HttpRouter + Send + Sync + Clone + 'static,
{
    tracing::info!("starting http server on addr {}", settings.addr);

    // Initialize the tracing logger not to print http request and response messages on health endpoint
    CompactRootSpanBuilder::init_skip_http_trace_paths(["/health"]);

    let json_cfg = actix_web::web::JsonConfig::default().limit(settings.max_body_size);
    let cors_settings = settings.cors.clone();
    let cors_enabled = cors_settings.enabled;
    let server = if let Some(metrics) = metrics {
        HttpServer::new(move || {
            let cors = cors_settings.clone().build();
            App::new()
                .wrap(TracingLogger::<CompactRootSpanBuilder>::new())
                .wrap(metrics.clone())
                .wrap(Condition::new(cors_enabled, cors))
                .app_data(json_cfg.clone())
                .configure(configure_router(&http))
        })
        .shutdown_timeout(SHUTDOWN_TIMEOUT_SEC)
        .bind(settings.addr)
        .expect("failed to bind server")
        .run()
    } else {
        HttpServer::new(move || {
            let cors = cors_settings.clone().build();
            App::new()
                .wrap(TracingLogger::<CompactRootSpanBuilder>::new())
                .wrap(Condition::new(cors_enabled, cors))
                .app_data(json_cfg.clone())
                .configure(configure_router(&http))
        })
        .shutdown_timeout(SHUTDOWN_TIMEOUT_SEC)
        .bind(settings.addr)
        .expect("failed to bind server")
        .run()
    };
    if let Some(shutdown) = shutdown {
        tokio::spawn(stop_actix_server_on_cancel(server.handle(), shutdown, true));
    }
    server
}

async fn grpc_serve(
    grpc: tonic::transport::server::Router,
    addr: SocketAddr,
    shutdown: Option<CancellationToken>,
) -> Result<(), tonic::transport::Error> {
    tracing::info!("starting grpc server on addr {}", addr);
    if let Some(shutdown) = shutdown {
        grpc.serve_with_shutdown(addr, grpc_cancel_signal(shutdown))
            .await
    } else {
        grpc.serve(addr).await
    }
}
