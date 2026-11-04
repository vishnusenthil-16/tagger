use clap::Parser;
use serde_json::{Value, json};
use std::{process::ExitCode, time::Duration};

#[derive(Parser)]
#[command(
    version,
    about = "Send three synthetic contract checks to Tagger (real backend calls)"
)]
struct Args {
    #[arg(long, default_value = "http://127.0.0.1:8080")]
    url: String,
    #[arg(long, value_parser = ["jev"])]
    expect_backend: String,
    #[arg(long, default_value_t = 35.0)]
    timeout: f64,
}
fn valid(result: &Value, ids: &[&str], backend: &str) -> bool {
    let Some(distribution) = result.get("distribution").and_then(Value::as_array) else {
        return false;
    };
    if result.get("backend").and_then(Value::as_str) != Some(backend)
        || distribution.len() != ids.len()
    {
        return false;
    }
    let mut scores = Vec::new();
    for (p, id) in distribution.iter().zip(ids) {
        if p.as_object().is_none_or(|p| p.len() != 2) {
            return false;
        }
        if p.get("id").and_then(Value::as_str) != Some(id) {
            return false;
        }
        let Some(score) = p
            .get("probability")
            .and_then(Value::as_f64)
            .filter(|p| p.is_finite() && (0.0..=1.0).contains(p))
        else {
            return false;
        };
        scores.push(score);
    }
    let max = scores.iter().copied().fold(0.0, f64::max);
    let winners: Vec<_> = ids
        .iter()
        .zip(&scores)
        .filter(|(_, p)| **p == max)
        .map(|(id, _)| *id)
        .collect();
    let Some(candidate) = winners.first() else {
        return false;
    };
    let status = if winners.len() > 1 {
        "needs_review"
    } else if *candidate == "__none__" {
        "no_match"
    } else {
        "classified"
    };
    let label = if status == "classified" {
        json!(candidate)
    } else {
        Value::Null
    };
    result.get("status").and_then(Value::as_str) == Some(status)
        && result.get("candidate").and_then(Value::as_str) == Some(candidate)
        && result.get("label") == Some(&label)
        && result.as_object().is_some_and(|o| o.len() == 8)
        && ["configured_model", "reported_model"].iter().all(|key| {
            result
                .get(key)
                .is_some_and(|v| v.is_null() || v.as_str().is_some_and(|s| !s.trim().is_empty()))
        })
        && result
            .get("provider_confidence")
            .is_some_and(|v| v.is_null() || v.as_f64().is_some_and(|p| (0.0..=1.0).contains(&p)))
}
#[tokio::main]
async fn main() -> ExitCode {
    let args = Args::parse();
    let Ok(url) = reqwest::Url::parse(&args.url) else {
        eprintln!("Invalid service URL.");
        return ExitCode::from(2);
    };
    let key = std::env::var("TAGGER_API_KEY").unwrap_or_default();
    let duration = Duration::try_from_secs_f64(args.timeout)
        .ok()
        .filter(|d| !d.is_zero() && std::time::Instant::now().checked_add(*d).is_some());
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || key.is_empty()
        || !key.bytes().all(|b| (33..=126).contains(&b))
        || duration.is_none()
    {
        eprintln!("Use a valid service URL, positive timeout, and TAGGER_API_KEY.");
        return ExitCode::from(2);
    }
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .timeout(duration.expect("validated timeout"))
        .build();
    let Ok(client) = client else {
        eprintln!("Could not initialize HTTP client.");
        return ExitCode::FAILURE;
    };
    let cases = [
        (
            "The application closes unexpectedly when I open a file.",
            "defect",
            "Software defects",
            "request",
            "Requests for new functionality",
        ),
        (
            "A ceramic coffee mug.",
            "kitchen",
            "Kitchenware",
            "clothing",
            "Clothing and accessories",
        ),
        (
            "Rain is expected tomorrow.",
            "sports",
            "Sports results",
            "music",
            "Music performances",
        ),
    ];
    let mut checks = Vec::new();
    for (text, a, ad, b, bd) in cases {
        let response = client.post(format!("{}/v1/classify", args.url.trim_end_matches('/'))).bearer_auth(&key)
            .json(&json!({"text":text,"options":[{"id":a,"description":ad},{"id":b,"description":bd}]})).send().await;
        let result = async {
            let mut response = response.ok()?.error_for_status().ok()?;
            let mut bytes = Vec::new();
            while let Some(chunk) = response.chunk().await.ok()? {
                if chunk.len() > 1024 * 1024 - bytes.len() {
                    return None;
                }
                bytes.extend_from_slice(&chunk);
            }
            serde_json::from_slice::<Value>(&bytes).ok()
        }
        .await;
        let Some(result) = result.filter(|v| valid(v, &[a, b, "__none__"], &args.expect_backend))
        else {
            eprintln!(
                "{}",
                json!({"passed":checks.len(),"total":3,"result":"failed"})
            );
            return ExitCode::FAILURE;
        };
        checks.push(json!({"status":result["status"],"backend":result["backend"],"configured_model":result["configured_model"],"reported_model":result["reported_model"]}));
    }
    println!(
        "{}",
        json!({"passed":3,"total":3,"result":"passed","checks":checks})
    );
    ExitCode::SUCCESS
}
