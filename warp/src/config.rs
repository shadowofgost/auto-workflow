use std::{env, time::Duration};

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Level {
    Info,
    Warn,
    Debug,
}

#[derive(Clone, Debug)]
pub struct Config {
    pub level: Level,
    pub proxy: bool,
    pub qlog: bool,
    pub port: u16,
    pub delay: Duration,
    pub license: String,
    pub endpoint: String,
}

pub fn boolean(value: &str) -> Result<bool, String> {
    match value.to_ascii_lowercase().as_str() {
        "true" | "1" | "yes" => Ok(true),
        "false" | "0" | "no" => Ok(false),
        _ => Err("Boolean values must be true/false, 1/0 or yes/no".into()),
    }
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        Self::read(|name| env::var(name).ok())
    }

    pub fn read(get: impl Fn(&str) -> Option<String>) -> Result<Self, String> {
        let value = |name: &str, default: &str| get(name).unwrap_or_else(|| default.into());
        let level = match value("WARP_LOG_LEVEL", "info")
            .to_ascii_lowercase()
            .as_str()
        {
            "info" => Level::Info,
            "warn" => Level::Warn,
            "debug" => Level::Debug,
            _ => return Err("WARP_LOG_LEVEL must be info, warn or debug".into()),
        };
        let port = value("WARP_PORT", "40000")
            .parse::<u16>()
            .ok()
            .filter(|p| *p > 0)
            .ok_or("WARP_PORT must be 1..65535")?;
        let delay = value("WARP_SLEEP", "2")
            .parse::<f64>()
            .ok()
            .filter(|v| v.is_finite() && (0.0..=60.0).contains(v))
            .ok_or("WARP_SLEEP must be 0..60 seconds")?;
        Ok(Self {
            level,
            proxy: boolean(&value("WARP_PROXY", "TRUE"))?,
            qlog: boolean(&value("WARP_ENABLE_QLOG", "false"))?,
            port,
            delay: Duration::from_secs_f64(delay),
            license: value("WARP_LICENSE_KEY", ""),
            endpoint: value("WARP_CUSTOM_ENDPOINT", ""),
        })
    }

    pub fn redact(&self, value: &str) -> String {
        // Strip CSI terminal escapes, then replace the literal license (not a regex).
        let mut result = String::with_capacity(value.len());
        let mut chars = value.chars().peekable();
        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' && chars.peek() == Some(&'[') {
                chars.next();
                for c in chars.by_ref() {
                    if ('@'..='~').contains(&c) {
                        break;
                    }
                }
            } else {
                result.push(ch);
            }
        }
        if self.license.is_empty() {
            result
        } else {
            result.replace(&self.license, "[redacted]")
        }
    }
}

pub struct LogFilter {
    config: Config,
    keep: bool,
}

impl LogFilter {
    pub fn new(config: Config) -> Self {
        Self { config, keep: true }
    }

    pub fn line(&mut self, line: &str) -> Option<String> {
        let line = self.config.redact(line);
        if let Some(level) = line
            .split(|c: char| !c.is_alphanumeric() && c != '_')
            .find(|word| {
                matches!(
                    *word,
                    "TRACE"
                        | "DEBUG"
                        | "INFO"
                        | "WARN"
                        | "WARNING"
                        | "ERROR"
                        | "CRITICAL"
                        | "FATAL"
                )
            })
        {
            self.keep = self.config.level == Level::Debug
                || matches!(level, "WARN" | "WARNING" | "ERROR" | "CRITICAL" | "FATAL");
        }
        self.keep.then_some(line)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config(values: &[(&str, &str)]) -> Result<Config, String> {
        Config::read(|key| {
            values
                .iter()
                .find(|(k, _)| *k == key)
                .map(|(_, v)| v.to_string())
        })
    }
    #[test]
    fn validates_all_configuration_before_starting() {
        for (key, value) in [
            ("WARP_PORT", "0"),
            ("WARP_PORT", "65536"),
            ("WARP_SLEEP", "-1"),
            ("WARP_SLEEP", "NaN"),
            ("WARP_SLEEP", "inf"),
            ("WARP_PROXY", "maybe"),
            ("WARP_LOG_LEVEL", "silent"),
        ] {
            assert!(config(&[(key, value)]).is_err(), "{key}={value}");
        }
        assert_eq!(
            config(&[("WARP_SLEEP", "0.25")]).unwrap().delay,
            Duration::from_millis(250)
        );
        assert!(boolean("YES").unwrap());
        assert!(!boolean("FALSE").unwrap());
    }
    #[test]
    fn filters_multiline_output_and_redacts_literal_license() {
        let settings = config(&[("WARP_LICENSE_KEY", "secret[.*]")]).unwrap();
        let mut filter = LogFilter::new(settings);
        assert_eq!(filter.line("\u{1b}[31mINFO noisy\u{1b}[0m"), None);
        assert_eq!(filter.line("  continuation"), None);
        assert_eq!(
            filter.line("ERROR secret[.*]"),
            Some("ERROR [redacted]".into())
        );
        assert_eq!(filter.line("  stack frame"), Some("  stack frame".into()));
        assert_eq!(filter.line("DEBUG noisy"), None);
        let mut debug = LogFilter::new(config(&[("WARP_LOG_LEVEL", "debug")]).unwrap());
        assert!(debug.line("DEBUG detail").is_some());
    }
}
