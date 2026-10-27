//! Dependency-light mDNS master browser used by the Python discovery chain.
//!
//! Prints exactly one JSON object on stdout. Exit code 0 means a browse ran
//! (even when it found nothing); a transport failure prints `"ok": false` with
//! a machine-readable reason and exits 1.

use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::process::ExitCode;
use std::time::{Duration, Instant};

use mdns_sd::{ScopedIp, ServiceDaemon, ServiceEvent};

const DEFAULT_SERVICE: &str = "_kllm._tcp.local.";
const DEFAULT_PORT: u16 = 18765;
const DEFAULT_TIMEOUT: f64 = 2.0;

struct Options {
    timeout: f64,
    key: String,
    service: String,
}

fn parse_args(args: &[String]) -> Result<Options, String> {
    let mut options = Options {
        timeout: DEFAULT_TIMEOUT,
        key: String::new(),
        service: DEFAULT_SERVICE.to_string(),
    };
    let mut index = 0;
    while index < args.len() {
        let flag = args[index].as_str();
        let value = args.get(index + 1);
        match (flag, value) {
            ("--timeout", Some(raw)) => {
                options.timeout = raw
                    .parse::<f64>()
                    .map_err(|_| "invalid --timeout".to_string())?;
                index += 2;
            }
            ("--key", Some(raw)) => {
                options.key = raw.clone();
                index += 2;
            }
            ("--service", Some(raw)) => {
                options.service = raw.clone();
                index += 2;
            }
            ("--timeout" | "--key" | "--service", None) => {
                return Err(format!("missing value for {flag}"));
            }
            _ => return Err(format!("unknown argument {flag}")),
        }
    }
    if !options.timeout.is_finite() || options.timeout < 0.0 {
        return Err("invalid --timeout".to_string());
    }
    Ok(options)
}

fn join_key_id(key: &str) -> String {
    let digest = Sha256::digest(key.as_bytes());
    digest[..8].iter().map(|byte| format!("{byte:02x}")).collect()
}

fn ipv4_of(addresses: &HashSet<ScopedIp>) -> Option<String> {
    addresses
        .iter()
        .filter(|address| address.is_ipv4())
        .map(|address| address.to_ip_addr().to_string())
        .next()
}

fn emit(payload: &serde_json::Value, ok: bool) -> ExitCode {
    println!("{payload}");
    if ok {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

fn failure(reason: &str, detail: &str) -> ExitCode {
    emit(
        &serde_json::json!({
            "ok": false,
            "masters": [],
            "reason": reason,
            "detail": detail,
        }),
        false,
    )
}

fn browse(options: &Options) -> Result<serde_json::Value, (String, String)> {
    let daemon = ServiceDaemon::new().map_err(|error| {
        (
            "MDNS_BLOCKED".to_string(),
            format!("cannot start mDNS daemon: {error}"),
        )
    })?;
    let receiver = daemon
        .browse(&options.service)
        .map_err(|error| ("BROWSE_FAILED".to_string(), error.to_string()))?;
    let want = if options.key.is_empty() {
        None
    } else {
        Some(join_key_id(&options.key))
    };
    let mut masters: Vec<serde_json::Value> = Vec::new();
    let mut seen: HashSet<(String, u16)> = HashSet::new();
    let deadline = Instant::now() + Duration::from_secs_f64(options.timeout.max(0.05));

    loop {
        let now = Instant::now();
        if now >= deadline {
            break;
        }
        let remaining = deadline.saturating_duration_since(now);
        match receiver.recv_timeout(remaining) {
            Ok(ServiceEvent::ServiceResolved(info)) => {
                if info.get_property_val_str("role") != Some("master") {
                    continue;
                }
                let key_id = info.get_property_val_str("kid").map(str::to_string);
                if let Some(want) = want.as_deref() {
                    if key_id.as_deref() != Some(want) {
                        continue;
                    }
                }
                let Some(host) = ipv4_of(&info.addresses) else {
                    continue;
                };
                let port = if info.port == 0 {
                    DEFAULT_PORT
                } else {
                    info.port
                };
                if !seen.insert((host.clone(), port)) {
                    continue;
                }
                masters.push(serde_json::json!({
                    "name": info.fullname,
                    "host": host,
                    "port": port,
                    "key_id": key_id,
                }));
            }
            Ok(_) => {}
            Err(_) => break,
        }
    }
    let _ = daemon.stop_browse(&options.service);
    let _ = daemon.shutdown();
    Ok(serde_json::json!({
        "ok": true,
        "masters": masters,
        "reason": null,
        "detail": null,
    }))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let options = match parse_args(&args) {
        Ok(options) => options,
        Err(error) => return failure("INVALID_ARGUMENT", &error),
    };
    match browse(&options) {
        Ok(payload) => emit(&payload, true),
        Err((reason, detail)) => failure(&reason, &detail),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    #[test]
    fn parses_defaults_and_overrides() {
        let defaults = parse_args(&[]).expect("defaults");
        assert_eq!(defaults.timeout, DEFAULT_TIMEOUT);
        assert_eq!(defaults.service, DEFAULT_SERVICE);
        assert!(defaults.key.is_empty());

        let parsed = parse_args(&args(&["--timeout", "0.5", "--key", "abc", "--service", "_x._tcp.local."]))
            .expect("overrides");
        assert_eq!(parsed.timeout, 0.5);
        assert_eq!(parsed.key, "abc");
        assert_eq!(parsed.service, "_x._tcp.local.");
    }

    #[test]
    fn rejects_bad_arguments() {
        assert!(parse_args(&args(&["--timeout", "soon"])).is_err());
        assert!(parse_args(&args(&["--timeout"])).is_err());
        assert!(parse_args(&args(&["--nope"])).is_err());
    }

    #[test]
    fn join_key_fingerprint_matches_python() {
        assert_eq!(join_key_id("secret-key"), "85dbe15d75ef9308");
    }
}
