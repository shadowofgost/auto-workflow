use std::{
    env,
    process::{Command, ExitCode},
};
use warp_supervisor::config::Config;
#[cfg(not(unix))]
use warp_supervisor::process;

fn main() -> ExitCode {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if let Some(command) = args.first() {
        if command == "--version" {
            println!("warp-supervisor {}", env!("CARGO_PKG_VERSION"));
            return ExitCode::SUCCESS;
        }
        if command != "healthcheck" || args.len() != 1 {
            let mut command = Command::new(command);
            command.args(&args[1..]);
            #[cfg(unix)]
            {
                use std::os::unix::process::CommandExt;
                eprintln!("ERROR Cannot execute command: {}", command.exec());
                return ExitCode::from(127);
            }
            #[cfg(not(unix))]
            return match command.status() {
                Ok(status) => ExitCode::from(process::code(status) as u8),
                Err(error) => {
                    eprintln!("ERROR Cannot execute command: {error}");
                    ExitCode::from(127)
                }
            };
        }
    }
    let result = Config::from_env().and_then(|config| {
        if args.is_empty() {
            warp_supervisor::run(config)
        } else {
            warp_supervisor::healthcheck(&config)
        }
    });
    match result {
        Ok(code) => ExitCode::from(code as u8),
        Err(error) => {
            eprintln!("ERROR WARP configuration or startup failed: {error}");
            ExitCode::FAILURE
        }
    }
}
