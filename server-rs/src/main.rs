//! The bench-truth streaming server in Rust, fast enough that its own delay
//! stays far below what a benchmark client adds. It speaks the same protocol
//! as `bench_truth/server.py`: the same scenarios, the `x-bt-id` header, and
//! `/truth` records with `arrived` on CLOCK_MONOTONIC (the clock Python's
//! `time.perf_counter()` reads on Linux) and `sent` relative to `arrived`.
//!
//!     bench-truth-server --scenario fast --port 8100

use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, Uri, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::serve::ListenerExt;
use axum::{Json, Router};
use bytes::Bytes;
use serde::Serialize;
use serde_json::{Value, json};
use tokio::time::{Instant, sleep_until};

#[derive(Clone, Copy)]
struct Scenario {
    ttft: f64,
    itl: f64,
    tokens: usize,
    usage_counts_eos: bool,
}

fn scenario(name: &str) -> Scenario {
    let s = |ttft, itl, tokens, eos| Scenario { ttft, itl, tokens, usage_counts_eos: eos };
    match name {
        "uniform" => s(0.05, 0.02, 64, false),
        "slow_first" => s(0.5, 0.01, 64, false),
        "usage_eos" => s(0.05, 0.02, 64, true),
        "fast" => s(0.05, 0.005, 256, false),
        other => panic!("unknown scenario {other}"),
    }
}

#[derive(Serialize)]
struct Record {
    id: Option<String>,
    arrived: f64,
    tokens: usize,
    sent: Vec<f64>,
}

#[derive(Clone)]
struct AppState {
    scenario: Scenario,
    /// With `--word`, every chunk carries this word, so a client that re-tokenizes
    /// the text counts one token per chunk. Without it, chunk i carries `t{i}`.
    word: Option<Arc<str>>,
    records: Arc<Mutex<Vec<Record>>>,
}

fn monotonic() -> f64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: clock_gettime writes into the timespec we pass and has no other effects.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 * 1e-9
}

fn chunk(chat: bool, text: Option<&str>, usage: Option<&Value>) -> Bytes {
    let choice = if chat {
        json!({"index": 0, "delta": text.map_or(json!({}), |t| json!({"content": t})), "finish_reason": null})
    } else {
        json!({"index": 0, "text": text.unwrap_or(""), "finish_reason": null})
    };
    let object = if chat { "chat.completion.chunk" } else { "text_completion" };
    let mut body = json!({"id": "bt", "object": object, "model": "bench-truth",
        "choices": if usage.is_some() { json!([]) } else { json!([choice]) }});
    if let Some(u) = usage {
        body["usage"] = u.clone();
    }
    Bytes::from(format!("data: {body}\n\n"))
}

async fn stream(State(state): State<AppState>, uri: Uri, headers: HeaderMap, Json(req): Json<Value>) -> Response {
    let arrived_instant = Instant::now();
    let arrived = monotonic();
    let id = headers.get("x-bt-id").and_then(|v| v.to_str().ok()).map(str::to_string);
    let chat = uri.path().ends_with("chat/completions");
    let s = state.scenario;
    let n = req["max_tokens"].as_u64().map_or(s.tokens, |m| s.tokens.min(m as usize));
    // Like the OpenAI API, send the usage chunk only when the client asks for it.
    let include_usage = req["stream_options"]["include_usage"].as_bool().unwrap_or(false);
    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Bytes, std::convert::Infallible>>(16);
    tokio::spawn(async move {
        let mut sent = Vec::with_capacity(n);
        for i in 0..n {
            sleep_until(arrived_instant + Duration::from_secs_f64(s.ttft + i as f64 * s.itl)).await;
            let text = state.word.as_deref().map_or_else(|| format!(" t{i}"), |w| format!(" {w}"));
            if tx.send(Ok(chunk(chat, Some(&text), None))).await.is_err() {
                return; // the client went away; no record
            }
            sent.push(monotonic() - arrived);
        }
        if include_usage {
            let completion = n + usize::from(s.usage_counts_eos);
            let usage = json!({"prompt_tokens": 8, "completion_tokens": completion, "total_tokens": 8 + completion});
            let _ = tx.send(Ok(chunk(chat, None, Some(&usage)))).await;
        }
        let _ = tx.send(Ok(Bytes::from_static(b"data: [DONE]\n\n"))).await;
        state.records.lock().unwrap().push(Record { id, arrived, tokens: n, sent });
    });
    let body = Body::from_stream(tokio_stream_from(rx));
    ([(header::CONTENT_TYPE, "text/event-stream")], body).into_response()
}

fn tokio_stream_from<T>(mut rx: tokio::sync::mpsc::Receiver<T>) -> impl futures::Stream<Item = T> {
    futures::stream::poll_fn(move |cx| rx.poll_recv(cx))
}

async fn truth(State(state): State<AppState>) -> impl IntoResponse {
    Json(serde_json::to_value(&*state.records.lock().unwrap()).unwrap())
}

async fn models() -> impl IntoResponse {
    Json(json!({"object": "list", "data": [{"id": "bench-truth", "object": "model"}]}))
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    let flag = |name: &str, default: &str| {
        args.iter().position(|a| a == name).map_or(default.to_string(), |i| args[i + 1].clone())
    };
    let mut chosen = scenario(&flag("--scenario", "uniform"));
    // Optional overrides, so a sweep can vary one timing at a time.
    if args.iter().any(|a| a == "--itl") {
        chosen.itl = flag("--itl", "").parse().expect("--itl takes seconds");
    }
    if args.iter().any(|a| a == "--tokens") {
        chosen.tokens = flag("--tokens", "").parse().expect("--tokens takes a count");
    }
    let word = args.iter().any(|a| a == "--word").then(|| Arc::from(flag("--word", "")));
    let state = AppState { scenario: chosen, word, records: Arc::default() };
    let app = Router::new()
        .route("/v1/completions", post(stream))
        .route("/v1/chat/completions", post(stream))
        .route("/v1/models", get(models))
        .route("/truth", get(truth))
        .with_state(state);
    let port = flag("--port", "8100").parse::<u16>().unwrap();
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await.unwrap().tap_io(|tcp| {
        // Send every chunk at once, as inference servers do. Without this, Nagle's
        // algorithm holds a chunk until the client acknowledges the previous one.
        if let Err(err) = tcp.set_nodelay(true) {
            eprintln!("set_nodelay failed: {err}");
        }
    });
    axum::serve(listener, app).await.unwrap();
}
