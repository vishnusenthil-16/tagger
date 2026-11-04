mod support;
use serde_json::{Value, json};
use std::{
    process::{Command, Stdio},
    time::Duration,
};
use support::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[tokio::test]
async fn results_order_ties_none_and_provenance() {
    let h = Harness::new(&[]).await;
    for (scores, choice, status, label, candidate) in [
        (
            json!({"alpha":0.8,"beta":0.1,"__none__":0.1}),
            "alpha",
            "classified",
            json!("alpha"),
            "alpha",
        ),
        (
            json!({"alpha":0.1,"beta":0.1,"__none__":0.8}),
            "__none__",
            "no_match",
            Value::Null,
            "__none__",
        ),
        (
            json!({"alpha":0.5,"beta":0.5,"__none__":0}),
            "beta",
            "needs_review",
            Value::Null,
            "alpha",
        ),
        (
            json!({"alpha":0,"beta":0.5,"__none__":0.5}),
            "__none__",
            "needs_review",
            Value::Null,
            "beta",
        ),
        (
            json!({"alpha":0.33,"beta":0.33,"__none__":0.33}),
            "beta",
            "needs_review",
            Value::Null,
            "alpha",
        ),
    ] {
        let mut provider = output(scores.clone(), choice);
        provider.as_object_mut().unwrap().remove("model");
        provider["answers"]["classification"]
            .as_object_mut()
            .unwrap()
            .remove("confidence");
        h.reply.lock().unwrap().body = serde_json::to_vec(&provider).unwrap();
        let response = h.send(payload()).await;
        assert_eq!(response.status(), 200);
        let result: Value = response.json().await.unwrap();
        assert_eq!(
            result,
            json!({"status":status,"label":label,"candidate":candidate,"distribution":[
            {"id":"alpha","probability":scores["alpha"].as_f64().unwrap()},
            {"id":"beta","probability":scores["beta"].as_f64().unwrap()},
            {"id":"__none__","probability":scores["__none__"].as_f64().unwrap()}],
            "provider_confidence":null,"backend":"jev","configured_model":"configured-version","reported_model":null})
        );
    }
    let calls = h.calls.lock().unwrap();
    assert_eq!(calls.len(), 5);
    for call in calls.iter() {
        assert_eq!(call["state"], payload()["text"]);
        assert_eq!(call["model"], "configured-version");
        assert_eq!(
            call.pointer("/questions/classification/criteria")
                .unwrap()
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            vec!["alpha", "beta", "__none__"]
        );
    }
}

#[tokio::test]
async fn strict_validation_and_auth_precedence() {
    let h = Harness::new(&[]).await;
    for (field, value, code) in [
        ("text", json!(" "), "invalid_text"),
        ("text", json!("\u{1c}\u{1f}"), "invalid_text"),
        ("text", Value::Null, "invalid_request"),
        ("text", json!(12), "invalid_request"),
        ("options", json!([]), "invalid_category_count"),
        ("backend", json!("other"), "invalid_request"),
        ("model", json!("override"), "invalid_request"),
        (
            "options",
            json!([{"id":"alpha","description":"a"},{"id":"alpha","description":"b"}]),
            "invalid_category_id",
        ),
        (
            "options",
            json!([{"id":"__none__","description":"a"},{"id":"beta","description":"b"}]),
            "invalid_category_id",
        ),
        (
            "options",
            json!([{"id":"Alpha","description":"a"},{"id":"beta","description":"b"}]),
            "invalid_category_id",
        ),
        (
            "options",
            json!([{"id":"alpha","description":" "},{"id":"beta","description":"b"}]),
            "invalid_category_description",
        ),
        (
            "options",
            json!([{"id":"alpha","description":"a","extra":true},{"id":"beta","description":"b"}]),
            "invalid_request",
        ),
    ] {
        let mut body = payload();
        body[field] = value;
        error(h.send(body).await, 422, code).await;
    }
    for raw in [
        b"null".as_slice(),
        b"[]",
        b"{}",
        b"{\"text\":",
        br#"{"text":"x","text":"y","options":[]}"#,
        br#"{"text":NaN,"options":[]}"#,
        br#"{"text":"\ud800","options":[]}"#,
        b"\xff",
    ] {
        error(
            h.request()
                .header("content-type", "application/json")
                .body(raw)
                .send()
                .await
                .unwrap(),
            422,
            "invalid_request",
        )
        .await;
    }
    for auth in [
        "Bearer wrong",
        "Basic client-secret",
        "Bearer",
        "Bearer  client-secret",
        "Bearer\tclient-secret",
    ] {
        let response = h
            .client
            .post(format!("{}/v1/classify", h.url))
            .header("authorization", auth)
            .header("content-type", "text/plain")
            .body("private")
            .send()
            .await
            .unwrap();
        assert_eq!(response.headers()["www-authenticate"], "Bearer");
        error(response, 401, "unauthorized").await;
    }
    error(
        h.request()
            .header("authorization", "Bearer client-secret")
            .json(&payload())
            .send()
            .await
            .unwrap(),
        401,
        "unauthorized",
    )
    .await;
    error(
        h.request()
            .header("content-type", "text/plain")
            .body("{}")
            .send()
            .await
            .unwrap(),
        415,
        "unsupported_media_type",
    )
    .await;
    error(
        h.request()
            .header("content-encoding", "gzip")
            .json(&payload())
            .send()
            .await
            .unwrap(),
        415,
        "unsupported_media_type",
    )
    .await;
    assert!(h.calls.lock().unwrap().is_empty());
    for scheme in ["Bearer", "bearer", "BEARER", "bEaReR"] {
        assert_eq!(
            h.client
                .post(format!("{}/v1/classify", h.url))
                .header("authorization", format!("{scheme} client-secret"))
                .json(&payload())
                .send()
                .await
                .unwrap()
                .status(),
            200
        );
    }
}

#[tokio::test]
async fn backend_status_mapping_no_retries_or_redirects() {
    let h = Harness::new(&[]).await;
    for (index, (status, body, expected, code)) in [
        (400, json!({"error":"unknown"}), 502, "backend_error"),
        (
            422,
            json!({"detail":"invalid fields"}),
            502,
            "backend_error",
        ),
        (
            400,
            json!({"error":{"code":"max_tokens_exceeded"}}),
            422,
            "backend_input_limit_exceeded",
        ),
        (
            422,
            json!({"code":"max_tokens_exceeded"}),
            422,
            "backend_input_limit_exceeded",
        ),
        (
            400,
            json!({"error":"max_tokens_exceeded"}),
            422,
            "backend_input_limit_exceeded",
        ),
        (
            400,
            json!({"message":"max_tokens_exceeded"}),
            502,
            "backend_error",
        ),
        (401, json!({"error":"private"}), 502, "backend_auth_failed"),
        (403, json!({}), 502, "backend_auth_failed"),
        (413, json!({}), 422, "backend_input_limit_exceeded"),
        (429, json!({}), 503, "backend_unavailable"),
        (529, json!({}), 503, "backend_unavailable"),
        (500, json!({}), 502, "backend_error"),
        (503, json!({}), 502, "backend_error"),
        (302, json!({}), 502, "backend_error"),
    ]
    .into_iter()
    .enumerate()
    {
        *h.reply.lock().unwrap() = Reply {
            status,
            body: serde_json::to_vec(&body).unwrap(),
            ..Reply::default()
        };
        error(h.send(payload()).await, expected, code).await;
        assert_eq!(h.calls.lock().unwrap().len(), index + 1);
    }
}

#[tokio::test]
async fn malformed_provider_output_and_confidence() {
    let h = Harness::new(&[]).await;
    for scores in [
        json!({"alpha":0.8,"beta":0.2}),
        json!({"alpha":0.8,"beta":0.1,"__none__":0.1,"extra":0}),
        json!({"alpha":true,"beta":0,"__none__":0}),
        json!({"alpha":"0.8","beta":0.1,"__none__":0.1}),
        json!({"alpha":1.1,"beta":-0.1,"__none__":0}),
        json!({"alpha":0.4,"beta":0.2,"__none__":0.1}),
        json!({"alpha":0.98,"beta":0,"__none__":0}),
        json!({"alpha":0,"beta":0,"__none__":0}),
    ] {
        h.reply.lock().unwrap().body = serde_json::to_vec(&output(scores, "alpha")).unwrap();
        error(h.send(payload()).await, 502, "backend_invalid_output").await;
    }
    for (confidence, expected) in [
        (Value::Null, 200),
        (json!("not numeric"), 200),
        (json!(true), 200),
        (json!(0.37), 200),
        (json!(-0.1), 502),
        (json!(1.1), 502),
    ] {
        let mut value = output(json!({"alpha":0.8,"beta":0.1,"__none__":0.1}), "alpha");
        value["answers"]["classification"]["confidence"] = confidence.clone();
        h.reply.lock().unwrap().body = serde_json::to_vec(&value).unwrap();
        let response = h.send(payload()).await;
        assert_eq!(response.status(), expected);
        if expected == 200 {
            let result: Value = response.json().await.unwrap();
            assert_eq!(
                result["provider_confidence"],
                if confidence.is_number() {
                    confidence
                } else {
                    Value::Null
                }
            );
        }
    }
    for body in [
        b"private-provider-body".to_vec(),
        br#"{"answers":{},"answers":{}}"#.to_vec(),
        vec![b'x'; 1_048_577],
        b"[]".to_vec(),
    ] {
        h.reply.lock().unwrap().body = body;
        error(h.send(payload()).await, 502, "backend_invalid_output").await;
    }
    *h.reply.lock().unwrap() = Reply {
        encoding: "gzip",
        ..Reply::default()
    };
    error(h.send(payload()).await, 502, "backend_invalid_output").await;
    *h.reply.lock().unwrap() = Reply {
        media: "text/html",
        ..Reply::default()
    };
    error(h.send(payload()).await, 502, "backend_invalid_output").await;
}

#[tokio::test]
async fn utf8_and_body_boundaries() {
    let h = Harness::new(&[
        ("TAGGER_MAX_TEXT_BYTES", "4"),
        ("TAGGER_MAX_CATEGORY_DESCRIPTION_BYTES", "4"),
        ("TAGGER_MAX_REQUEST_BYTES", "256"),
    ])
    .await;
    let mut body = payload();
    body["text"] = json!("éé");
    for c in body["options"].as_array_mut().unwrap() {
        c["description"] = json!("éé");
    }
    assert_eq!(h.send(body.clone()).await.status(), 200);
    body["text"] = json!("ééa");
    error(h.send(body.clone()).await, 422, "invalid_text").await;
    body["text"] = json!("éé");
    body["options"][0]["description"] = json!("ééa");
    error(h.send(body).await, 422, "invalid_category_description").await;
    error(
        h.request()
            .header("content-type", "application/json")
            .body(" ".repeat(257))
            .send()
            .await
            .unwrap(),
        413,
        "request_too_large",
    )
    .await;
    let mut stream = tokio::net::TcpStream::connect(h.url.trim_start_matches("http://"))
        .await
        .unwrap();
    stream.write_all(b"POST /v1/classify HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer client-secret\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n101\r\n").await.unwrap();
    stream.write_all(&vec![b' '; 257]).await.unwrap();
    stream.write_all(b"\r\n0\r\n\r\n").await.unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).await.unwrap();
    assert!(response.contains("413"));
    assert!(response.contains("request_too_large"));
    assert_eq!(h.calls.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn overload_timeout_and_capacity_recovery() {
    let h = Harness::new(&[("TAGGER_REQUEST_TIMEOUT_SECONDS", "0.25")]).await;
    h.reply.lock().unwrap().delay = Duration::from_secs(2);
    let first = tokio::spawn(h.request().json(&payload()).send());
    h.wait_calls(1).await;
    error(h.send(payload()).await, 503, "overloaded").await;
    error(first.await.unwrap().unwrap(), 504, "backend_timeout").await;
    h.reply.lock().unwrap().delay = Duration::ZERO;
    assert_eq!(h.send(payload()).await.status(), 200);
}

#[tokio::test]
async fn upload_deadline_and_disconnect_release_capacity() {
    let h = Harness::new(&[("TAGGER_REQUEST_TIMEOUT_SECONDS", "5")]).await;
    h.reply.lock().unwrap().delay = Duration::from_secs(5);
    let mut stream = tokio::net::TcpStream::connect(h.url.trim_start_matches("http://"))
        .await
        .unwrap();
    let body = payload().to_string();
    stream.write_all(format!("POST /v1/classify HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer client-secret\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",body.len(),body).as_bytes()).await.unwrap();
    h.wait_calls(1).await;
    drop(stream);
    h.reply.lock().unwrap().delay = Duration::ZERO;
    tokio::time::timeout(Duration::from_millis(800), async {
        loop {
            let result = h.send(payload()).await;
            if result.status() == 200 {
                break;
            }
            assert_eq!(result.status(), 503);
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("disconnect did not cancel local work");
    let short = Harness::new(&[("TAGGER_REQUEST_TIMEOUT_SECONDS", "0.1")]).await;
    let mut stream = tokio::net::TcpStream::connect(short.url.trim_start_matches("http://"))
        .await
        .unwrap();
    stream.write_all(b"POST /v1/classify HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer client-secret\r\nContent-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{").await.unwrap();
    let mut bytes = vec![0; 4096];
    let n = tokio::time::timeout(Duration::from_secs(1), stream.read(&mut bytes))
        .await
        .unwrap()
        .unwrap();
    let response = String::from_utf8_lossy(&bytes[..n]);
    assert!(response.contains("408"));
    assert!(response.contains("request_timeout"));
    assert!(short.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn smoke_cli_routing_and_sanitized_logs() {
    let mut h = Harness::new(&[]).await;
    h.reply.lock().unwrap().dynamic = true;
    let url = h.url.clone();
    let smoke = tokio::task::spawn_blocking(move || {
        Command::new(env!("CARGO_BIN_EXE_tagger-smoke"))
            .env_clear()
            .env("TAGGER_API_KEY", "client-secret")
            .args(["--url", &url, "--expect-backend", "jev"])
            .output()
            .unwrap()
    })
    .await
    .unwrap();
    assert!(
        smoke.status.success(),
        "{}",
        String::from_utf8_lossy(&smoke.stderr)
    );
    let report: Value = serde_json::from_slice(&smoke.stdout).unwrap();
    assert_eq!(report["passed"], 3);
    assert_eq!(h.calls.lock().unwrap().len(), 3);
    let result = h
        .client
        .get(format!("{}/v1/classify", h.url))
        .send()
        .await
        .unwrap();
    assert_eq!(result.headers()["allow"], "POST");
    error(result, 405, "method_not_allowed").await;
    for path in ["/missing", "/v1/classify/", "/docs", "/openapi.json"] {
        error(
            h.client
                .get(format!("{}{path}", h.url))
                .send()
                .await
                .unwrap(),
            404,
            "not_found",
        )
        .await;
    }
    h.process.kill().unwrap();
    h.process.wait().unwrap();
    let mut stderr = String::new();
    std::io::Read::read_to_string(h.process.stderr.as_mut().unwrap(), &mut stderr).unwrap();
    for private in [
        "client-secret",
        "upstream-secret",
        "ceramic coffee",
        "closes unexpectedly",
        "/prefix/v1/systemone",
    ] {
        assert!(!stderr.contains(private));
    }
}

#[test]
fn invalid_configuration_fails_without_disclosing_values() {
    for (name, value) in [
        ("TAGGER_BACKEND", "laya"),
        ("TAGGER_BACKEND_BASE_URL", "http:example.com"),
        ("TAGGER_BACKEND_BASE_URL", "http:///example.com"),
        (
            "TAGGER_BACKEND_BASE_URL",
            "https://private:secret@example.com",
        ),
        ("TAGGER_BACKEND_BASE_URL", "https://example.com/../x"),
        ("TAGGER_BACKEND_BASE_URL", "https://example.com/#"),
        ("TAGGER_API_KEY", "upstream-secret"),
        ("TAGGER_API_KEY", ""),
        ("TAGGER_API_KEY", "é"),
        ("TAGGER_BACKEND_MODEL", " "),
        ("TAGGER_MAX_CATEGORIES", "255"),
        ("TAGGER_MAX_CATEGORIES", "1"),
        ("TAGGER_MAX_REQUEST_BYTES", "33554433"),
        ("TAGGER_MAX_CONCURRENT_REQUESTS", "0"),
        ("TAGGER_MAX_TEXT_BYTES", "true"),
        ("TAGGER_REQUEST_TIMEOUT_SECONDS", "NaN"),
        ("TAGGER_REQUEST_TIMEOUT_SECONDS", "inf"),
        ("TAGGER_REQUEST_TIMEOUT_SECONDS", "-1"),
        ("TAGGER_REQUEST_TIMEOUT_SECONDS", "1e300"),
    ] {
        let mut child = Command::new(env!("CARGO_BIN_EXE_tagger"))
            .env_clear()
            .env("TAGGER_BACKEND", "jev")
            .env("TAGGER_BACKEND_BASE_URL", "https://example.com")
            .env("TAGGER_API_KEY", "client-secret")
            .env("TAGGER_BACKEND_API_KEY", "upstream-secret")
            .env("TAGGER_BACKEND_MODEL", "test-model")
            .env(name, value)
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while child.try_wait().unwrap().is_none() {
            if std::time::Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("invalid configuration did not exit: {name}");
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let out = child.wait_with_output().unwrap();
        assert_eq!(out.status.code(), Some(2), "{name}");
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("Invalid configuration"));
        assert!(!stderr.contains("secret"));
        assert!(!stderr.contains("private"));
    }
}

#[tokio::test]
async fn category_boundaries_and_large_distributions() {
    let h = Harness::new(&[("TAGGER_MAX_CATEGORIES", "254")]).await;
    h.reply.lock().unwrap().dynamic = true;
    for count in [2, 19, 199, 254] {
        let options: Vec<Value> = (0..count)
            .map(|i| json!({"id":format!("category_{i}"),"description":"  arbitrary 🐈  "}))
            .collect();
        let input = json!({"text":"  preserve Unicode 🐈 and whitespace  ","options":options});
        let response = h.send(input.clone()).await;
        assert_eq!(response.status(), 200);
        let result: Value = response.json().await.unwrap();
        assert_eq!(result["distribution"].as_array().unwrap().len(), count + 1);
        let calls = h.calls.lock().unwrap();
        let call = calls.last().unwrap();
        assert_eq!(call["state"], input["text"]);
        assert_eq!(
            call.pointer("/questions/classification/criteria/category_0")
                .unwrap(),
            "  arbitrary 🐈  "
        );
    }
    let mut input = payload();
    input["options"][0]["id"] = json!("a".repeat(64));
    assert_eq!(h.send(input.clone()).await.status(), 200);
    input["options"][0]["id"] = json!("a".repeat(65));
    error(h.send(input).await, 422, "invalid_category_id").await;
    let options: Vec<Value> = (0..255)
        .map(|i| json!({"id":format!("category_{i}"),"description":"x"}))
        .collect();
    error(
        h.send(json!({"text":"x","options":options})).await,
        422,
        "invalid_category_count",
    )
    .await;
    let options: Vec<Value> = (0..254)
        .map(|i| json!({"id":format!("category_{i}"),"description":"x"}))
        .collect();
    let mut probabilities: serde_json::Map<String, Value> = (0..254)
        .map(|i| (format!("category_{i}"), json!(0)))
        .collect();
    probabilities.insert("__none__".into(), json!(0));
    *h.reply.lock().unwrap() = Reply {
        body: serde_json::to_vec(&output(Value::Object(probabilities.clone()), "category_0"))
            .unwrap(),
        ..Reply::default()
    };
    error(
        h.send(json!({"text":"x","options":options})).await,
        502,
        "backend_invalid_output",
    )
    .await;
    for i in 0..3 {
        probabilities.insert(format!("category_{i}"), json!(0.33));
    }
    h.reply.lock().unwrap().body =
        serde_json::to_vec(&output(Value::Object(probabilities), "category_0")).unwrap();
    let result: Value = h
        .send(json!({"text":"x","options":options}))
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(result["status"], "needs_review");
}

#[tokio::test]
async fn two_slots_and_error_recovery() {
    let h = Harness::new(&[("TAGGER_MAX_CONCURRENT_REQUESTS", "2")]).await;
    *h.reply.lock().unwrap() = Reply {
        status: 500,
        delay: Duration::from_millis(200),
        ..Reply::default()
    };
    let first = tokio::spawn(h.request().json(&payload()).send());
    let second = tokio::spawn(h.request().json(&payload()).send());
    h.wait_calls(2).await;
    error(h.send(payload()).await, 503, "overloaded").await;
    error(first.await.unwrap().unwrap(), 502, "backend_error").await;
    error(second.await.unwrap().unwrap(), 502, "backend_error").await;
    *h.reply.lock().unwrap() = Reply::default();
    assert_eq!(h.send(payload()).await.status(), 200);
}

#[tokio::test]
async fn total_deadline_is_not_reset_after_upload() {
    let h = Harness::new(&[("TAGGER_REQUEST_TIMEOUT_SECONDS", "0.8")]).await;
    h.reply.lock().unwrap().delay = Duration::from_millis(500);
    let mut stream = tokio::net::TcpStream::connect(h.url.trim_start_matches("http://"))
        .await
        .unwrap();
    let body = payload().to_string();
    stream.write_all(format!("POST /v1/classify HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer client-secret\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",body.len(),&body[..10]).as_bytes()).await.unwrap();
    tokio::time::sleep(Duration::from_millis(500)).await;
    stream.write_all(&body.as_bytes()[10..]).await.unwrap();
    let mut response = String::new();
    tokio::time::timeout(Duration::from_secs(2), stream.read_to_string(&mut response))
        .await
        .unwrap()
        .unwrap();
    assert!(response.contains("504"));
    assert!(response.contains("backend_timeout"));
    assert_eq!(h.calls.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn invalid_provider_shape_choice_and_provenance() {
    let h = Harness::new(&[]).await;
    let original = output(json!({"alpha":0.8,"beta":0.1,"__none__":0.1}), "alpha");
    for (pointer, value) in [
        ("/answers", json!([])),
        ("/answers/classification", json!([])),
        ("/answers/classification/type", json!("noul")),
        ("/answers/classification/choice", json!("beta")),
        ("/answers/classification/choice", json!("unknown")),
        ("/answers/classification/choice", json!(true)),
        ("/model", json!(17)),
        ("/model", json!(" ")),
    ] {
        let mut changed = original.clone();
        *changed.pointer_mut(pointer).unwrap() = value;
        h.reply.lock().unwrap().body = serde_json::to_vec(&changed).unwrap();
        error(h.send(payload()).await, 502, "backend_invalid_output").await;
    }
    let mut extra = original;
    extra["answers"]["extra"] = json!({});
    h.reply.lock().unwrap().body = serde_json::to_vec(&extra).unwrap();
    error(h.send(payload()).await, 502, "backend_invalid_output").await;
    for raw in [br#"{"model":"\ud800"}"#.to_vec(),br#"{"answers":{"classification":{"type":"choice","choice":"alpha","probabilities":{"alpha":1e400,"beta":0,"__none__":0}}}}"#.to_vec(),br#"{"ignored":{"x":1,"x":2}}"#.to_vec(),vec![b'[';1500]] {
        h.reply.lock().unwrap().body=raw;
        error(h.send(payload()).await,502,"backend_invalid_output").await;
    }
}

#[tokio::test]
async fn unreachable_backend_is_sanitized() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    let h = Harness::new(&[("TAGGER_BACKEND_BASE_URL", &url)]).await;
    error(h.send(payload()).await, 503, "backend_unavailable").await;
    assert!(h.calls.lock().unwrap().is_empty());
}

#[test]
fn openapi_describes_the_retained_http_contract() {
    let schema: Value = serde_json::from_str(include_str!("../docs/openapi.json")).unwrap();
    let operation = &schema["paths"]["/v1/classify"]["post"];
    assert_eq!(operation["security"], json!([{"BearerAuth":[]} ]));
    assert_eq!(
        schema["components"]["schemas"]["ClassifyRequest"]["additionalProperties"],
        false
    );
    let response = &schema["components"]["schemas"]["ClassifyResponse"];
    for field in [
        "status",
        "label",
        "candidate",
        "distribution",
        "provider_confidence",
        "backend",
        "configured_model",
        "reported_model",
    ] {
        assert!(
            response["required"]
                .as_array()
                .unwrap()
                .contains(&json!(field))
        );
    }
    for status in [
        "200", "401", "404", "405", "408", "413", "415", "422", "500", "502", "503", "504",
    ] {
        assert!(
            operation["responses"].get(status).is_some(),
            "missing status {status}"
        );
    }
}
