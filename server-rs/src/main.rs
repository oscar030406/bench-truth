//! The bench-truth streaming server in Rust, fast enough that its own delay
//! stays far below what a benchmark client adds. It speaks the same protocol
//! as `bench_truth/server.py`: the same scenarios, the `x-bt-id` header, and
//! `/truth` records with `arrived` on the clock that Python's
//! `time.perf_counter()` reads, and `sent` relative to `arrived`.
//!
//!     bench-truth-server --scenario fast --port 8100

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::serve::ListenerExt;
use axum::{Json, Router};
use bytes::Bytes;
use clap::{Parser, ValueEnum};
use serde::Serialize;
use serde_json::{Value, json};
use tokio::time::{Instant, sleep_until};

/// Long prompts and base64 images fit, as in the Python server.
const MAX_BODY: usize = 256 << 20;

#[derive(Clone, Copy)]
struct Scenario {
    ttft: f64,
    itl: f64,
    tokens: usize,
    usage_counts_eos: bool,
}

impl Scenario {
    fn due(&self, i: usize) -> Duration {
        Duration::from_secs_f64(self.ttft + i as f64 * self.itl)
    }
}

#[derive(Clone, Copy, ValueEnum)]
#[value(rename_all = "snake_case")]
enum Preset {
    Uniform,
    SlowFirst,
    UsageEos,
    Fast,
}

impl Preset {
    fn scenario(self) -> Scenario {
        let s = |ttft, itl, tokens, eos| Scenario { ttft, itl, tokens, usage_counts_eos: eos };
        match self {
            Preset::Uniform => s(0.05, 0.02, 64, false),
            Preset::SlowFirst => s(0.5, 0.01, 64, false),
            Preset::UsageEos => s(0.05, 0.02, 64, true),
            Preset::Fast => s(0.05, 0.005, 256, false),
        }
    }
}

fn seconds(text: &str) -> Result<f64, String> {
    match text.parse::<f64>() {
        Ok(v) if v.is_finite() && v >= 0.0 => Ok(v),
        _ => Err(format!("{text} is not a time of 0 seconds or more")),
    }
}

fn positive(text: &str) -> Result<usize, String> {
    match text.parse::<usize>() {
        Ok(v) if v >= 1 => Ok(v),
        _ => Err(format!("{text} is not 1 or more")),
    }
}

/// A streaming server with known timings, for checking the numbers that LLM
/// serving benchmarks report.
#[derive(Parser)]
#[command(version)]
struct Args {
    /// Timing preset. --ttft, --itl and --tokens override its values.
    #[arg(long, value_enum, default_value_t = Preset::Uniform)]
    scenario: Preset,
    /// Seconds from a request's arrival to its first chunk.
    #[arg(long, value_parser = seconds)]
    ttft: Option<f64>,
    /// Seconds between later chunks.
    #[arg(long, value_parser = seconds)]
    itl: Option<f64>,
    /// Chunks per response.
    #[arg(long, value_parser = positive)]
    tokens: Option<usize>,
    /// Send this word in every chunk instead of t{i}.
    #[arg(long)]
    word: Option<String>,
    /// Keep only the newest records in /truth. The header x-bt-dropped counts the rest.
    #[arg(long, value_parser = positive, default_value_t = 100_000)]
    max_records: usize,
    /// Address to listen on. Use 0.0.0.0 to accept clients on other machines.
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 8100)]
    port: u16,
}

#[derive(Clone, Serialize)]
struct Record {
    id: Option<String>,
    arrived: f64,
    tokens: usize,
    sent: Vec<f64>,
}

/// Without a limit, a long run that never clears /truth fills the memory.
struct Records {
    list: VecDeque<Record>,
    max: usize,
    dropped: u64,
}

impl Records {
    fn push(&mut self, record: Record) {
        if self.list.len() == self.max {
            self.list.pop_front();
            self.dropped += 1;
        }
        self.list.push_back(record);
    }
}

#[derive(Clone)]
struct AppState {
    scenario: Scenario,
    /// With `--word`, every chunk carries this word, so a client that re-tokenizes
    /// the text counts one token per chunk. Without it, chunk i carries `t{i}`.
    word: Option<Arc<str>>,
    records: Arc<Mutex<Records>>,
}

impl AppState {
    fn text(&self, i: usize) -> String {
        self.word.as_deref().map_or_else(|| format!(" t{i}"), |w| format!(" {w}"))
    }

    fn record(&self, record: Record) {
        self.records.lock().unwrap().push(record);
    }
}

/// Seconds on the clock that Python's `time.perf_counter()` reads on this
/// platform, so that a client on the same machine can compare its send times
/// with `arrived`.
#[cfg(unix)]
fn monotonic() -> f64 {
    // CPython reads mach_absolute_time() on macOS. CLOCK_UPTIME_RAW is the same
    // clock in nanoseconds. CLOCK_MONOTONIC there also counts time asleep.
    #[cfg(target_vendor = "apple")]
    const CLOCK: libc::clockid_t = libc::CLOCK_UPTIME_RAW;
    #[cfg(not(target_vendor = "apple"))]
    const CLOCK: libc::clockid_t = libc::CLOCK_MONOTONIC;
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: clock_gettime writes into the timespec we pass and has no other effects.
    unsafe { libc::clock_gettime(CLOCK, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 * 1e-9
}

#[cfg(windows)]
fn monotonic() -> f64 {
    use windows_sys::Win32::System::Performance::{QueryPerformanceCounter, QueryPerformanceFrequency};
    let (mut ticks, mut freq) = (0i64, 0i64);
    // SAFETY: both calls write one integer into the pointer we pass, and they
    // cannot fail on Windows XP and later.
    unsafe {
        QueryPerformanceCounter(&mut ticks);
        QueryPerformanceFrequency(&mut freq);
    }
    ticks as f64 / freq as f64
}

/// The scenario length, capped by max_completion_tokens or max_tokens. A value
/// that is not a positive integer counts as unset, as in the Python server.
fn output_tokens(req: &Value, tokens: usize) -> usize {
    ["max_completion_tokens", "max_tokens"]
        .iter()
        .find_map(|key| req[key].as_u64().filter(|&m| m > 0))
        .map_or(tokens, |m| tokens.min(usize::try_from(m).unwrap_or(usize::MAX)))
}

fn usage(n: usize, s: Scenario) -> Value {
    let completion = n + usize::from(s.usage_counts_eos);
    json!({"prompt_tokens": 8, "completion_tokens": completion, "total_tokens": 8 + completion})
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

fn bad_request(message: String) -> Response {
    (StatusCode::BAD_REQUEST, message).into_response()
}

async fn stream(State(state): State<AppState>, request: Request) -> Response {
    // Stamp the arrival before the body is read, as the Python server does, so
    // the upload of a long prompt counts as server time.
    let arrived_instant = Instant::now();
    let arrived = monotonic();
    let (parts, body) = request.into_parts();
    let id = parts.headers.get("x-bt-id").and_then(|v| v.to_str().ok()).map(str::to_string);
    let chat = parts.uri.path().ends_with("chat/completions");
    let bytes = match axum::body::to_bytes(body, MAX_BODY).await {
        Ok(bytes) => bytes,
        Err(err) => return (StatusCode::PAYLOAD_TOO_LARGE, err.to_string()).into_response(),
    };
    let req: Value = match serde_json::from_slice(&bytes) {
        Ok(req @ Value::Object(_)) => req,
        Ok(_) => return bad_request("the body is not a JSON object".into()),
        Err(err) => return bad_request(format!("the body is not JSON: {err}")),
    };
    let s = state.scenario;
    let n = output_tokens(&req, s.tokens);
    if req["stream"] != Value::Bool(true) {
        return whole(state, chat, arrived_instant, arrived, id, n).await;
    }
    // Like the OpenAI API, send the usage chunk only when the client asks for it.
    let include_usage = req["stream_options"]["include_usage"] == Value::Bool(true);
    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Bytes, std::convert::Infallible>>(16);
    tokio::spawn(async move {
        let mut sent = Vec::with_capacity(n);
        for i in 0..n {
            sleep_until(arrived_instant + s.due(i)).await;
            // Record the time before the hand-off, so no client can read a chunk before its record.
            sent.push(monotonic() - arrived);
            if tx.send(Ok(chunk(chat, Some(&state.text(i)), None))).await.is_err() {
                return; // the client went away; no record
            }
        }
        if include_usage {
            let _ = tx.send(Ok(chunk(chat, None, Some(&usage(n, s))))).await;
        }
        let _ = tx.send(Ok(Bytes::from_static(b"data: [DONE]\n\n"))).await;
        state.record(Record { id, arrived, tokens: n, sent });
    });
    let body = Body::from_stream(tokio_stream_from(rx));
    ([(header::CONTENT_TYPE, "text/event-stream")], body).into_response()
}

/// Without stream: true, the whole response leaves when its last token is due.
async fn whole(state: AppState, chat: bool, start: Instant, arrived: f64, id: Option<String>, n: usize) -> Response {
    let s = state.scenario;
    sleep_until(start + s.due(n - 1)).await;
    let text: String = (0..n).map(|i| state.text(i)).collect();
    let finish = if n < s.tokens { "length" } else { "stop" };
    let (object, choice) = if chat {
        (
            "chat.completion",
            json!({"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}),
        )
    } else {
        ("text_completion", json!({"index": 0, "text": text, "finish_reason": finish}))
    };
    let created = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs());
    let body = json!({"id": "bt", "object": object, "created": created, "model": "bench-truth",
        "choices": [choice], "usage": usage(n, s)});
    state.record(Record { id, arrived, tokens: n, sent: vec![monotonic() - arrived] });
    Json(body).into_response()
}

fn tokio_stream_from<T>(mut rx: tokio::sync::mpsc::Receiver<T>) -> impl futures::Stream<Item = T> {
    futures::stream::poll_fn(move |cx| rx.poll_recv(cx))
}

async fn truth(State(state): State<AppState>) -> Response {
    // Copy under the lock and serialize outside it, so that streams that finish
    // meanwhile do not wait for the whole response to be written.
    let (list, dropped) = {
        let records = state.records.lock().unwrap();
        (Vec::from(records.list.clone()), records.dropped)
    };
    let body = serde_json::to_vec(&list).expect("records serialize");
    let headers =
        [(header::CONTENT_TYPE.as_str(), "application/json".to_string()), ("x-bt-dropped", dropped.to_string())];
    (headers, body).into_response()
}

/// Forget the records, so that the next run starts with an empty /truth.
async fn clear(State(state): State<AppState>) -> StatusCode {
    let mut records = state.records.lock().unwrap();
    records.list.clear();
    records.dropped = 0;
    StatusCode::NO_CONTENT
}

async fn models() -> impl IntoResponse {
    Json(json!({"object": "list", "data": [{"id": "bench-truth", "object": "model"}]}))
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let mut chosen = args.scenario.scenario();
    // Optional overrides, so a sweep can vary one timing at a time.
    chosen.ttft = args.ttft.unwrap_or(chosen.ttft);
    chosen.itl = args.itl.unwrap_or(chosen.itl);
    chosen.tokens = args.tokens.unwrap_or(chosen.tokens);
    // Windows wakes timers every 15.6 ms by default, so chunks would leave up to
    // 16 ms late. Ask for 1 ms timers for this process.
    // SAFETY: timeBeginPeriod only sets the timer resolution of this process.
    #[cfg(windows)]
    unsafe {
        windows_sys::Win32::Media::timeBeginPeriod(1);
    }
    let records = Records { list: VecDeque::new(), max: args.max_records, dropped: 0 };
    let state = AppState { scenario: chosen, word: args.word.map(Arc::from), records: Arc::new(Mutex::new(records)) };
    let app = Router::new()
        .route("/v1/completions", post(stream))
        .route("/v1/chat/completions", post(stream))
        .route("/v1/models", get(models))
        .route("/health", get(|| async { StatusCode::OK }))
        .route("/truth", get(truth).delete(clear))
        .with_state(state);
    let listener = match tokio::net::TcpListener::bind((args.host.as_str(), args.port)).await {
        Ok(listener) => listener,
        Err(err) => {
            eprintln!("bench-truth-server: cannot listen on {}:{}: {err}", args.host, args.port);
            std::process::exit(1);
        }
    };
    let listener = listener.tap_io(|tcp| {
        // Send every chunk at once, as inference servers do. Without this, Nagle's
        // algorithm holds a chunk until the client acknowledges the previous one.
        if let Err(err) = tcp.set_nodelay(true) {
            eprintln!("set_nodelay failed: {err}");
        }
    });
    axum::serve(listener, app).await.unwrap();
}
