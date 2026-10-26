#![cfg(unix)]

use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::PathBuf,
    process::{Child, Command, Output, Stdio},
    sync::atomic::{AtomicBool, AtomicUsize, Ordering},
    thread,
    time::{Duration, Instant},
};
use warp_supervisor::process::{ManagedChild, capture, terminate};

static NEXT: AtomicUsize = AtomicUsize::new(0);

struct Fixture {
    root: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "warp-rust-tests-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let fixture = Self { root };
        fixture.script(
            "warp-svc",
            r#"
trap 'echo stopped > "$FIXTURE_DIR/stopped"; exit 0' TERM INT
echo $$ > "$FIXTURE_DIR/daemon-pid"
echo 'INFO hidden noise'
echo 'ERROR fake-license'
echo '  stack frame'
if [ "${FIXTURE_DAEMON_EXIT:-}" ]; then sleep 0.4; exit "$FIXTURE_DAEMON_EXIT"; fi
while :; do sleep 0.05; done
"#,
        );
        fixture.script(
            "warp-cli",
            r#"
shift 2
printf '%s\n' "$*" >> "$FIXTURE_DIR/calls"
case "$*" in
  status)
    [ -f "$FIXTURE_DIR/daemon-pid" ] || exit 1
    if [ "${FIXTURE_CLI_HANG:-}" ]; then exec sleep 30; fi
    echo 'Status: Connected';;
  'registration show') echo 'License: old';;
  'registration license fake-license')
    if [ "${FIXTURE_FAIL:-}" ]; then echo 'ERROR invalid fake-license' >&2; exit 7; fi;;
  'debug extra stop') echo 'Extra logging has not yet been started'; exit 1;;
esac
"#,
        );
        fixture.script(
            "curl",
            r#"
printf '%s\n' "$*" > "$FIXTURE_DIR/curl-args"
echo "warp=${FIXTURE_WARP:-on}"
exit "${FIXTURE_CURL_EXIT:-0}"
"#,
        );
        fixture
    }

    fn script(&self, name: &str, body: &str) {
        let path = self.root.join(name);
        fs::write(&path, format!("#!/bin/sh\nset -eu\n{body}")).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }

    fn command(&self) -> Command {
        let binary = std::env::current_exe()
            .unwrap()
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .join("warp-supervisor");
        let mut command = Command::new(binary);
        let mut paths = vec![self.root.clone()];
        paths.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
        for key in [
            "WARP_LOG_LEVEL",
            "WARP_PROXY",
            "WARP_ENABLE_QLOG",
            "WARP_PORT",
            "WARP_CUSTOM_ENDPOINT",
        ] {
            command.env_remove(key);
        }
        command
            .env("PATH", std::env::join_paths(paths).unwrap())
            .env("FIXTURE_DIR", &self.root)
            .env("WARP_SLEEP", "0")
            .env("WARP_LICENSE_KEY", "fake-license")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        command
    }

    fn await_file(&self, name: &str) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !self.root.join(name).exists() {
            assert!(Instant::now() < deadline, "fixture did not create {name}");
            thread::sleep(Duration::from_millis(20));
        }
    }

    fn wait(&self, mut child: Child) -> Output {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if child.try_wait().unwrap().is_some() {
                return child.wait_with_output().unwrap();
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("supervisor did not exit within five seconds");
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn sigterm_stops_daemon_and_preserves_filtered_redacted_output() {
    let fixture = Fixture::new();
    let mut child = fixture.command().spawn().unwrap();
    fixture.await_file("calls");
    thread::sleep(Duration::from_millis(250));
    terminate(&mut child).unwrap();
    let output = fixture.wait(child);
    assert!(output.status.success());
    assert!(fixture.root.join("stopped").exists());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains("fake-license"));
    assert!(!stdout.contains("hidden noise"));
    assert!(stdout.contains("ERROR [redacted]"));
    assert!(stdout.contains("  stack frame"));
    let calls = fs::read_to_string(fixture.root.join("calls")).unwrap();
    assert!(calls.contains("registration license fake-license"));
    assert!(calls.contains("connect"));
    assert!(!calls.contains("registration new"));
}

#[test]
fn daemon_exit_code_is_propagated() {
    let fixture = Fixture::new();
    let child = fixture
        .command()
        .env("FIXTURE_DAEMON_EXIT", "7")
        .spawn()
        .unwrap();
    assert_eq!(fixture.wait(child).status.code(), Some(7));
}

#[test]
fn failed_configuration_stops_daemon_and_redacts_stderr() {
    let fixture = Fixture::new();
    let child = fixture.command().env("FIXTURE_FAIL", "1").spawn().unwrap();
    let output = fixture.wait(child);
    assert!(!output.status.success());
    assert!(fixture.root.join("stopped").exists());
    assert!(!String::from_utf8_lossy(&output.stdout).contains("fake-license"));
    assert!(!String::from_utf8_lossy(&output.stderr).contains("fake-license"));
    assert!(String::from_utf8_lossy(&output.stdout).contains("invalid [redacted]"));
}

#[test]
fn shutdown_interrupts_a_stuck_cli() {
    let fixture = Fixture::new();
    let mut child = fixture
        .command()
        .env("FIXTURE_CLI_HANG", "1")
        .spawn()
        .unwrap();
    fixture.await_file("calls");
    terminate(&mut child).unwrap();
    assert!(fixture.wait(child).status.success());
    assert!(fixture.root.join("stopped").exists());
}

#[test]
fn command_deadline_kills_and_reaps_child_without_blocking_on_output() {
    let started = Instant::now();
    let result = capture(
        Command::new("sleep").arg("30"),
        &AtomicBool::new(false),
        Duration::from_millis(100),
    );
    assert!(result.unwrap_err().contains("timed out"));
    assert!(started.elapsed() < Duration::from_secs(2));
}

#[test]
fn healthcheck_uses_remote_dns_and_rejects_direct_or_failed_requests() {
    let fixture = Fixture::new();
    for (warp, curl_code, expected) in [
        ("on", "0", true),
        ("plus", "0", true),
        ("off", "0", false),
        ("on", "7", false),
    ] {
        let output = fixture
            .command()
            .arg("healthcheck")
            .env("FIXTURE_WARP", warp)
            .env("FIXTURE_CURL_EXIT", curl_code)
            .output()
            .unwrap();
        assert_eq!(output.status.success(), expected);
    }
    let args = fs::read_to_string(fixture.root.join("curl-args")).unwrap();
    assert!(args.contains("--socks5-hostname 127.0.0.1:40000"));
    assert!(!fixture.root.join("daemon-pid").exists());
}

#[test]
fn command_override_preserves_exit_status_without_starting_warp() {
    let fixture = Fixture::new();
    let output = fixture
        .command()
        .args(["/bin/sh", "-c", "exit 23"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(23));
    assert!(!fixture.root.join("daemon-pid").exists());
}

#[test]
fn nonresponsive_child_is_force_killed_and_reaped() {
    let fixture = Fixture::new();
    fixture.script(
        "stubborn",
        "trap '' TERM; echo ready > \"$FIXTURE_DIR/ready\"; while :; do sleep 0.05; done",
    );
    let child = Command::new(fixture.root.join("stubborn"))
        .env("FIXTURE_DIR", &fixture.root)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let mut child = ManagedChild(child);
    fixture.await_file("ready");
    let status = child.shutdown(Duration::from_millis(100)).unwrap();
    assert!(!status.success());
    assert!(child.0.try_wait().unwrap().is_some());
}
