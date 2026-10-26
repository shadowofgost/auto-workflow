use std::{collections::VecDeque, path::Path};
use warp_supervisor::{Cli, config::Config, configure, trace_connected};

struct FakeCli {
    calls: Vec<Vec<String>>,
    registrations: VecDeque<Option<String>>,
}

impl Cli for FakeCli {
    fn run(&mut self, args: &[&str], _: bool, _: &[&str]) -> Result<Option<String>, String> {
        self.calls
            .push(args.iter().map(|s| s.to_string()).collect());
        if args == ["registration", "show"] {
            Ok(self.registrations.pop_front().unwrap())
        } else {
            Ok(Some("Success".into()))
        }
    }
}

fn settings(env: &[(&str, &str)]) -> Config {
    Config::read(|name| {
        env.iter()
            .find(|(k, _)| *k == name)
            .map(|(_, v)| v.to_string())
    })
    .unwrap()
}

fn cli(registrations: &[Option<&str>]) -> FakeCli {
    FakeCli {
        calls: Vec::new(),
        registrations: registrations
            .iter()
            .map(|s| s.map(str::to_string))
            .collect(),
    }
}

fn called(cli: &FakeCli, args: &[&str]) -> bool {
    cli.calls.iter().any(|call| call == args)
}

#[test]
fn creates_registration_and_attaches_plus_license() {
    let mut cli = cli(&[None, Some("License: old")]);
    configure(
        &settings(&[("WARP_LICENSE_KEY", "new")]),
        &mut cli,
        Path::new("missing-reg.json"),
    )
    .unwrap();
    assert!(called(&cli, &["registration", "new"]));
    assert!(called(&cli, &["registration", "license", "new"]));
    assert_eq!(cli.calls.last().unwrap(), &["connect"]);
}

#[test]
fn preserves_identity_and_does_not_reapply_the_same_license() {
    let mut cli = cli(&[Some("Account: Plus\n  License: same\n")]);
    let config = settings(&[
        ("WARP_LICENSE_KEY", "same"),
        ("WARP_PORT", "41000"),
        ("WARP_CUSTOM_ENDPOINT", "192.0.2.1:443"),
    ]);
    configure(&config, &mut cli, Path::new("missing-reg.json")).unwrap();
    assert!(!called(&cli, &["registration", "new"]));
    assert!(!called(&cli, &["registration", "license", "same"]));
    assert!(called(&cli, &["proxy", "port", "41000"]));
    assert!(called(
        &cli,
        &["tunnel", "endpoint", "set", "192.0.2.1:443"]
    ));
}

#[test]
fn failed_read_with_persisted_identity_never_registers_again() {
    let mut cli = cli(&[None]);
    let existing = std::env::current_exe().unwrap();
    let error = configure(&settings(&[]), &mut cli, &existing).unwrap_err();
    assert!(error.contains("preserving persisted identity"));
    assert!(!called(&cli, &["registration", "new"]));
}

#[test]
fn missing_license_field_refuses_to_guess() {
    let mut cli = cli(&[Some("Account: Free")]);
    let error = configure(
        &settings(&[("WARP_LICENSE_KEY", "secret")]),
        &mut cli,
        Path::new("missing"),
    )
    .unwrap_err();
    assert!(error.contains("no License field"));
    assert!(!called(&cli, &["registration", "license", "secret"]));
}

#[test]
fn full_tunnel_resets_endpoint_and_disables_extra_diagnostics() {
    let mut cli = cli(&[Some("Account: Free")]);
    configure(
        &settings(&[("WARP_PROXY", "FALSE")]),
        &mut cli,
        Path::new("missing"),
    )
    .unwrap();
    assert!(called(&cli, &["mode", "warp"]));
    assert!(
        !cli.calls
            .iter()
            .any(|call| call.first().is_some_and(|s| s == "proxy"))
    );
    assert!(called(&cli, &["tunnel", "endpoint", "reset"]));
    assert!(called(&cli, &["debug", "extra", "stop"]));
    assert!(called(&cli, &["debug", "qlog", "disable"]));
}

#[test]
fn qlog_is_explicit_opt_in() {
    let mut cli = cli(&[Some("License: same")]);
    configure(
        &settings(&[("WARP_ENABLE_QLOG", "yes")]),
        &mut cli,
        Path::new("missing"),
    )
    .unwrap();
    assert!(called(&cli, &["debug", "qlog", "enable"]));
}

#[test]
fn healthcheck_requires_exact_warp_egress_marker() {
    assert!(trace_connected("ip=192.0.2.1\nwarp=on\n"));
    assert!(trace_connected("warp=plus\r\n"));
    for text in ["warp=off", "warp=online", "xwarp=on", "warp=plus-extra"] {
        assert!(!trace_connected(text));
    }
}
