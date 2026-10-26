pub mod config;
pub mod process;

use config::{Config, Level, LogFilter};
use process::{ManagedChild, capture, stopped};
use std::{
    fs,
    io::{self, BufRead, BufReader, Write},
    path::Path,
    process::{Command, Stdio},
    sync::atomic::AtomicBool,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub trait Cli {
    fn run(
        &mut self,
        args: &[&str],
        required: bool,
        allowed: &[&str],
    ) -> Result<Option<String>, String>;
}

struct WarpCli<'a> {
    config: &'a Config,
    stop: &'a AtomicBool,
}

impl Cli for WarpCli<'_> {
    fn run(
        &mut self,
        args: &[&str],
        required: bool,
        allowed: &[&str],
    ) -> Result<Option<String>, String> {
        let result = capture(
            Command::new("warp-cli")
                .args(["--accept-tos", "--no-ansi"])
                .args(args),
            self.stop,
            Duration::from_secs(8),
        );
        match result {
            Ok(result)
                if result.status.success() || allowed.iter().any(|s| result.text.contains(s)) =>
            {
                Ok(Some(result.text))
            }
            Ok(result) if required => Err(self.config.redact(&format!(
                "WARP command failed: {}: {}",
                args.iter().take(2).copied().collect::<Vec<_>>().join(" "),
                result.text.trim()
            ))),
            Err(error) if required => Err(self.config.redact(&format!(
                "WARP command failed: {}: {error}",
                args.iter().take(2).copied().collect::<Vec<_>>().join(" ")
            ))),
            _ => Ok(None),
        }
    }
}

pub fn configure(
    config: &Config,
    cli: &mut impl Cli,
    registered_file: &Path,
) -> Result<(), String> {
    let registration = match cli.run(&["registration", "show"], false, &[])? {
        Some(value) => {
            log(config, "INFO", "Reusing existing WARP registration");
            value
        }
        None => {
            if registered_file.exists() {
                return Err(
                    "Cannot read existing registration; preserving persisted identity".into(),
                );
            }
            cli.run(&["registration", "new"], true, &[])?;
            let registration = cli
                .run(&["registration", "show"], true, &[])?
                .ok_or("Registration could not be read after creation")?;
            log(config, "INFO", "Registered WARP client");
            registration
        }
    };
    if !config.license.is_empty() {
        let clean = config.redact_ansi_only(&registration);
        let license = clean
            .lines()
            .find_map(|line| {
                let (key, value) = line.trim().split_once(':')?;
                key.trim()
                    .eq_ignore_ascii_case("License")
                    .then(|| value.trim())
            })
            .ok_or("Registration output has no License field; refusing to guess license state")?;
        if license != config.license {
            cli.run(&["registration", "license", &config.license], true, &[])?;
            log(config, "INFO", "Updated WARP account license");
        }
    }
    cli.run(
        &["mode", if config.proxy { "proxy" } else { "warp" }],
        true,
        &[],
    )?;
    if config.proxy {
        cli.run(&["proxy", "port", &config.port.to_string()], true, &[])?;
    }
    if config.endpoint.is_empty() {
        cli.run(&["tunnel", "endpoint", "reset"], true, &[])?;
    } else {
        cli.run(&["tunnel", "endpoint", "set", &config.endpoint], true, &[])?;
    }
    cli.run(
        &["debug", "extra", "stop"],
        true,
        &["Extra logging has not yet been started"],
    )?;
    cli.run(
        &[
            "debug",
            "qlog",
            if config.qlog { "enable" } else { "disable" },
        ],
        true,
        &[],
    )?;
    cli.run(&["connect"], true, &[])?;
    log(
        config,
        "INFO",
        &format!(
            "Configured mode={} port={} qlog={}",
            if config.proxy { "proxy" } else { "warp" },
            config.port,
            config.qlog
        ),
    );
    Ok(())
}

impl Config {
    fn redact_ansi_only(&self, value: &str) -> String {
        let mut config = self.clone();
        config.license.clear();
        config.redact(value)
    }
}

pub fn log(config: &Config, level: &str, message: &str) {
    if config.level == Level::Warn && level == "INFO" {
        return;
    }
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let _ = writeln!(
        io::stdout().lock(),
        "{timestamp} {level} {}",
        config.redact(message)
    );
}

fn daemon_logs(reader: impl io::Read, config: Config) {
    let mut filter = LogFilter::new(config);
    let mut reader = BufReader::new(reader);
    let mut bytes = Vec::new();
    loop {
        bytes.clear();
        match reader.read_until(b'\n', &mut bytes) {
            Ok(0) | Err(_) => break,
            Ok(_) => {
                let text = String::from_utf8_lossy(&bytes);
                if let Some(line) = filter.line(text.trim_end_matches(['\r', '\n']))
                    && writeln!(io::stdout().lock(), "{line}").is_err()
                {
                    break;
                }
            }
        }
    }
}

pub fn run(config: Config) -> Result<i32, String> {
    let stop = process::signals().map_err(|e| e.to_string())?;
    let version =
        fs::read_to_string("/etc/warp-package-version").unwrap_or_else(|_| "unknown".into());
    log(
        &config,
        "INFO",
        &format!("Starting WARP {}", version.trim()),
    );
    let (reader, writer) = io::pipe().map_err(|e| e.to_string())?;
    let stderr = writer.try_clone().map_err(|e| e.to_string())?;
    let mut daemon = ManagedChild(
        Command::new("warp-svc")
            .arg("--accept-tos")
            .stdin(Stdio::null())
            .stdout(writer)
            .stderr(stderr)
            .spawn()
            .map_err(|e| e.to_string())?,
    );
    let filter_config = config.clone();
    let logger = thread::spawn(move || daemon_logs(reader, filter_config));
    let result = supervise(&config, &stop, &mut daemon);
    let cleanup = daemon.shutdown(Duration::from_secs(10));
    // A faulty descendant must not hold shutdown open by retaining a pipe handle.
    let deadline = Instant::now() + Duration::from_secs(2);
    while !logger.is_finished() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(25));
    }
    if logger.is_finished() {
        let _ = logger.join();
    }
    if stopped(&stop) {
        return Ok(0);
    }
    if let Err(error) = result {
        log(&config, "ERROR", &error);
        return Ok(cleanup
            .ok()
            .map(process::code)
            .filter(|code| *code > 0 && *code < 128)
            .unwrap_or(1));
    }
    cleanup.map_err(|e| e.to_string())?;
    result
}

fn supervise(config: &Config, stop: &AtomicBool, daemon: &mut ManagedChild) -> Result<i32, String> {
    if !process::sleep(stop, config.delay) {
        return Ok(0);
    }
    let mut cli = WarpCli { config, stop };
    let deadline = Instant::now() + Duration::from_secs(60);
    loop {
        if stopped(stop) {
            return Ok(0);
        }
        if let Some(status) = daemon.0.try_wait().map_err(|e| e.to_string())? {
            let code = process::code(status);
            log(
                config,
                "ERROR",
                &format!("WARP daemon exited before becoming ready with status {code}"),
            );
            return Ok(if code == 0 { 1 } else { code });
        }
        if Instant::now() >= deadline {
            return Err("WARP daemon did not become ready within 60 seconds".into());
        }
        if cli.run(&["status"], false, &[])?.is_some() {
            break;
        }
        process::sleep(stop, Duration::from_secs(1));
    }
    if stopped(stop) {
        return Ok(0);
    }
    configure(
        config,
        &mut cli,
        Path::new("/var/lib/cloudflare-warp/reg.json"),
    )?;
    let mut previous = None;
    let mut next_status = Instant::now();
    while !stopped(stop) {
        if let Some(status) = daemon.0.try_wait().map_err(|e| e.to_string())? {
            let code = process::code(status);
            log(
                config,
                "ERROR",
                &format!("WARP daemon exited with status {code}"),
            );
            return Ok(code);
        }
        if Instant::now() >= next_status {
            match cli.run(&["status"], false, &[])? {
                None => log(
                    config,
                    "WARN",
                    "Status query failed; daemon is still running",
                ),
                Some(output) => {
                    let status = config.redact(output.trim());
                    if previous.as_ref() != Some(&status) {
                        log(config, "INFO", &format!("Connection state: {status}"));
                        previous = Some(status);
                    }
                }
            }
            next_status = Instant::now() + Duration::from_secs(30);
        }
        process::sleep(stop, Duration::from_millis(100));
    }
    Ok(0)
}

pub fn healthcheck(config: &Config) -> Result<i32, String> {
    let stop = process::signals().map_err(|e| e.to_string())?;
    let mut command = Command::new("curl");
    command.args([
        "--noproxy",
        "",
        "--connect-timeout",
        "2",
        "--max-time",
        "4",
        "-fsS",
    ]);
    if config.proxy {
        command.args(["--socks5-hostname", &format!("127.0.0.1:{}", config.port)]);
    }
    command.arg("https://cloudflare.com/cdn-cgi/trace");
    let output = capture(&mut command, &stop, Duration::from_millis(4500))?;
    Ok(
        if output.status.success() && trace_connected(&output.text) {
            0
        } else {
            1
        },
    )
}

pub fn trace_connected(text: &str) -> bool {
    text.lines()
        .any(|line| matches!(line.trim_end_matches('\r'), "warp=on" | "warp=plus"))
}
