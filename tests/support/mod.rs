use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::{Request, Response, body::Incoming, service::service_fn};
use hyper_util::rt::TokioIo;
use serde_json::{Value, json};
use std::{
    convert::Infallible,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    net::{TcpListener, TcpStream},
    task::JoinHandle,
};

pub fn payload() -> Value {
    json!({"text":"Some content","options":[{"id":"alpha","description":"First category"},{"id":"beta","description":"Second category"}]})
}
pub fn output(scores: Value, choice: &str) -> Value {
    json!({"model":"reported-version","answers":{"classification":{"type":"choice","choice":choice,"probabilities":scores,"confidence":0.7}}})
}
#[derive(Clone)]
pub struct Reply {
    pub status: u16,
    pub body: Vec<u8>,
    pub media: &'static str,
    pub encoding: &'static str,
    pub delay: Duration,
    pub dynamic: bool,
}
impl Default for Reply {
    fn default() -> Self {
        Self {
            status: 200,
            body: serde_json::to_vec(&output(
                json!({"alpha":0.8,"beta":0.1,"__none__":0.1}),
                "alpha",
            ))
            .unwrap(),
            media: "application/json",
            encoding: "identity",
            delay: Duration::ZERO,
            dynamic: false,
        }
    }
}
pub struct Harness {
    pub process: Child,
    pub url: String,
    pub client: reqwest::Client,
    pub reply: Arc<Mutex<Reply>>,
    pub calls: Arc<Mutex<Vec<Value>>>,
    task: JoinHandle<()>,
}
impl Harness {
    pub async fn new(extra: &[(&str, &str)]) -> Self {
        let provider = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}/prefix", provider.local_addr().unwrap());
        let reply = Arc::new(Mutex::new(Reply::default()));
        let calls = Arc::new(Mutex::new(Vec::new()));
        let r = reply.clone();
        let c = calls.clone();
        let task = tokio::spawn(async move {
            let mut connections = tokio::task::JoinSet::new();
            loop {
                tokio::select! {
                    _ = connections.join_next(), if !connections.is_empty() => (),
                    accepted = provider.accept() => {
                        let (stream,_) = accepted.unwrap();
                        let reply = r.clone(); let calls = c.clone();
                        connections.spawn(async move {
                            let service = service_fn(move |req: Request<Incoming>| {
                                let reply = reply.clone(); let calls = calls.clone();
                                async move {
                                    assert_eq!(req.uri().path(),"/prefix/v1/systemone");
                                    assert_eq!(req.headers()["authorization"],"Bearer upstream-secret");
                                    assert_eq!(req.headers()["accept-encoding"],"identity");
                                    let bytes = req.into_body().collect().await.unwrap().to_bytes();
                                    let payload: Value = serde_json::from_slice(&bytes).unwrap();
                                    calls.lock().unwrap().push(payload.clone());
                                    let mut reply = reply.lock().unwrap().clone();
                                    tokio::time::sleep(reply.delay).await;
                                    if reply.dynamic {
                                        let criteria = payload.pointer("/questions/classification/criteria").unwrap().as_object().unwrap();
                                        let choice = criteria.keys().next().unwrap();
                                        let probabilities: serde_json::Map<String,Value> = criteria.keys().map(|id| (id.clone(),json!(u8::from(id==choice)))).collect();
                                        reply.body = serde_json::to_vec(&output(Value::Object(probabilities),choice)).unwrap();
                                    }
                                    Ok::<_,Infallible>(Response::builder().status(reply.status).header("content-type",reply.media).header("content-encoding",reply.encoding)
                                        .header("location","http://127.0.0.1:1/do-not-follow").body(Full::new(Bytes::from(reply.body))).unwrap())
                                }
                            });
                            let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(stream),service).await;
                        });
                    }
                }
            }
        });
        let reservation = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = reservation.local_addr().unwrap().port();
        drop(reservation);
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_tagger"));
        cmd.env_clear()
            .env("TOKIO_WORKER_THREADS", "2")
            .env("TAGGER_BACKEND", "jev")
            .env("TAGGER_BACKEND_BASE_URL", base)
            .env("TAGGER_API_KEY", "client-secret")
            .env("TAGGER_BACKEND_API_KEY", "upstream-secret")
            .env("TAGGER_BACKEND_MODEL", "configured-version")
            .env("HTTP_PROXY", "http://127.0.0.1:1")
            .env("HTTPS_PROXY", "http://127.0.0.1:1")
            .env("ALL_PROXY", "http://127.0.0.1:1")
            .args(["--port", &port.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        for (key, value) in extra {
            cmd.env(key, value);
        }
        let process = cmd.spawn().unwrap();
        let mut h = Self {
            process,
            url: format!("http://127.0.0.1:{port}"),
            client: reqwest::Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap(),
            reply,
            calls,
            task,
        };
        let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        loop {
            assert!(
                h.process.try_wait().unwrap().is_none(),
                "service exited during startup"
            );
            if TcpStream::connect(("127.0.0.1", port)).await.is_ok() {
                break;
            }
            assert!(tokio::time::Instant::now() < deadline, "startup timed out");
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        h
    }
    pub fn request(&self) -> reqwest::RequestBuilder {
        self.client
            .post(format!("{}/v1/classify", self.url))
            .bearer_auth("client-secret")
    }
    pub async fn send(&self, body: Value) -> reqwest::Response {
        self.request().json(&body).send().await.unwrap()
    }
    pub async fn wait_calls(&self, n: usize) {
        tokio::time::timeout(Duration::from_secs(2), async {
            while self.calls.lock().unwrap().len() < n {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("backend was not called");
    }
}
impl Drop for Harness {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
        self.task.abort();
    }
}
pub async fn error(response: reqwest::Response, status: u16, code: &str) {
    assert_eq!(response.status().as_u16(), status);
    let value: Value = response.json().await.unwrap();
    assert_eq!(value["error"]["code"], code);
    assert_eq!(value.as_object().unwrap().len(), 1);
    assert_eq!(value["error"].as_object().unwrap().len(), 3);
    assert_eq!(
        value["error"]["retryable"],
        json!(matches!(
            code,
            "request_timeout" | "backend_timeout" | "backend_unavailable" | "overloaded"
        ))
    );
}
