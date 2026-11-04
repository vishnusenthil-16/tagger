use crate::{backend, config::Settings, error::ServiceError, schema};
use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::{Request, Response, body::Incoming, service::service_fn};
use hyper_util::rt::TokioIo;
use std::{
    convert::Infallible,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Instant,
};
use subtle::ConstantTimeEq;
use tokio::{net::TcpListener, sync::Semaphore, task::JoinSet};
struct State {
    settings: Settings,
    client: reqwest::Client,
    slots: Semaphore,
}

// A dropped connection also drops this guard, keeping cancellation observable
// without logging request paths, payloads, credentials, or provider errors.
struct RequestLog {
    start: Instant,
    outcome: &'static str,
}
impl Drop for RequestLog {
    fn drop(&mut self) {
        eprintln!(
            "outcome={} backend=jev duration_ms={:.1}",
            self.outcome,
            self.start.elapsed().as_secs_f64() * 1000.0
        );
    }
}

pub(crate) async fn run(
    host: &str,
    port: u16,
    settings: Settings,
) -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind((host, port)).await?;
    let state = Arc::new(State {
        client: backend::client()?,
        slots: Semaphore::new(settings.concurrency),
        settings,
    });
    let mut tasks = JoinSet::new();
    let shutdown = shutdown_signal();
    tokio::pin!(shutdown);
    loop {
        tokio::select! {
            _ = &mut shutdown => break,
            result = tasks.join_next(), if !tasks.is_empty() => { if result.is_some_and(|r| r.is_err()) { eprintln!("outcome=internal_error backend=jev"); } },
            accepted = listener.accept() => {
                let (stream, _) = accepted?;
                let state = Arc::clone(&state);
                tasks.spawn(async move {
                    // Keeping the service future inside the connection makes dropping
                    // a disconnected connection cancel upstream work and release its permit.
                    let _ = hyper::server::conn::http1::Builder::new().half_close(false)
                        .serve_connection(TokioIo::new(stream), service_fn(move |req| handle(req, Arc::clone(&state)))).await;
                });
            }
        }
    }
    // Shutdown cancels local work; no request tasks or permits outlive this process.
    tasks.abort_all();
    while tasks.join_next().await.is_some() {}
    Ok(())
}
async fn shutdown_signal() {
    #[cfg(unix)]
    {
        if let Ok(mut term) =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            tokio::select! { _ = term.recv() => (), _ = tokio::signal::ctrl_c() => () }
            return;
        }
    }
    let _ = tokio::signal::ctrl_c().await;
}
async fn handle(
    request: Request<Incoming>,
    state: Arc<State>,
) -> Result<Response<Full<Bytes>>, Infallible> {
    let start = Instant::now();
    let mut log = RequestLog {
        start,
        outcome: "cancelled",
    };
    let backend_started = AtomicBool::new(false);
    let result = tokio::time::timeout(
        state.settings.timeout,
        classify(request, &state, &backend_started, start),
    )
    .await;
    let result = match result {
        Ok(result) if start.elapsed() < state.settings.timeout => result,
        _ => Err(if backend_started.load(Ordering::Relaxed) {
            ServiceError::BackendTimeout
        } else {
            ServiceError::RequestTimeout
        }),
    };
    let response = match result {
        Ok(response) => {
            log.outcome = "200";
            response
        }
        Err(error) => {
            log.outcome = error.details().2;
            error.response()
        }
    };
    Ok(response)
}
async fn classify(
    request: Request<Incoming>,
    state: &State,
    started: &AtomicBool,
    start: Instant,
) -> Result<Response<Full<Bytes>>, ServiceError> {
    use ServiceError::*;
    if request.uri().path() != "/v1/classify" {
        return Err(NotFound);
    }
    if request.method() != hyper::Method::POST {
        return Err(MethodNotAllowed);
    }
    let s = &state.settings;
    let (parts, mut body) = request.into_parts();
    let single = |name: &str| -> Option<&hyper::header::HeaderValue> {
        let mut headers = parts.headers.get_all(name).iter();
        let value = headers.next()?;
        if headers.next().is_some() {
            None
        } else {
            Some(value)
        }
    };
    let auth = single("authorization").ok_or(Unauthorized)?.as_bytes();
    let split = auth.iter().position(|b| *b == b' ').ok_or(Unauthorized)?;
    if !auth[..split].eq_ignore_ascii_case(b"bearer")
        || !bool::from(auth[split + 1..].ct_eq(s.api_key.as_bytes()))
    {
        return Err(Unauthorized);
    }
    let media = single("content-type")
        .and_then(|h| h.to_str().ok())
        .ok_or(UnsupportedMediaType)?;
    if !media
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .eq_ignore_ascii_case("application/json")
    {
        return Err(UnsupportedMediaType);
    }
    if parts.headers.contains_key("content-encoding")
        && !single("content-encoding")
            .and_then(|h| h.to_str().ok())
            .is_some_and(|v| v.trim().eq_ignore_ascii_case("identity"))
    {
        return Err(UnsupportedMediaType);
    }
    if parts.headers.contains_key("content-length") {
        let length = single("content-length")
            .and_then(|h| h.to_str().ok())
            .ok_or(InvalidRequest)?;
        if length.is_empty() || !length.bytes().all(|b| b.is_ascii_digit()) {
            return Err(InvalidRequest);
        }
        if length
            .parse::<u64>()
            .map_or(true, |n| n > s.max_request as u64)
        {
            return Err(RequestTooLarge);
        }
    }
    let mut bytes = Vec::new();
    while let Some(frame) = body.frame().await {
        let frame = frame.map_err(|_| InvalidRequest)?;
        if let Some(chunk) = frame.data_ref() {
            if chunk.len() > s.max_request - bytes.len() {
                return Err(RequestTooLarge);
            }
            bytes.extend_from_slice(chunk);
        }
    }
    let request = schema::parse(&bytes, s)?;
    if start.elapsed() >= s.timeout {
        return Err(RequestTimeout);
    }
    let _permit = state.slots.try_acquire().map_err(|_| Overloaded)?;
    let result = backend::classify(&state.client, s, &request, started, start).await?;
    let bytes = serde_json::to_vec(&result).map_err(|_| InternalError)?;
    let mut response = Response::new(Full::new(Bytes::from(bytes)));
    response.headers_mut().insert(
        "content-type",
        "application/json".parse().expect("static header"),
    );
    Ok(response)
}
