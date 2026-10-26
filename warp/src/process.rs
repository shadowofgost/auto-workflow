use std::{
    io::{self, Read},
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

pub fn stopped(stop: &AtomicBool) -> bool {
    stop.load(Ordering::Relaxed)
}

pub fn sleep(stop: &AtomicBool, duration: Duration) -> bool {
    let end = Instant::now() + duration;
    while !stopped(stop) {
        let left = end.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return true;
        }
        thread::sleep(left.min(Duration::from_millis(50)));
    }
    false
}

pub fn signals() -> io::Result<Arc<AtomicBool>> {
    let stop = Arc::new(AtomicBool::new(false));
    #[cfg(unix)]
    for signal in [signal_hook::consts::SIGTERM, signal_hook::consts::SIGINT] {
        signal_hook::flag::register(signal, Arc::clone(&stop))?;
    }
    Ok(stop)
}

pub fn code(status: ExitStatus) -> i32 {
    if let Some(code) = status.code() {
        return code;
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        128 + status.signal().unwrap_or(1)
    }
    #[cfg(not(unix))]
    {
        1
    }
}

pub fn terminate(child: &mut Child) -> io::Result<()> {
    if child.try_wait()?.is_some() {
        return Ok(());
    }
    #[cfg(unix)]
    {
        // SAFETY: kill takes an integer PID and signal, without pointer arguments.
        // The child has not been reaped, so its PID cannot have been recycled.
        let result = unsafe { libc::kill(child.id() as libc::pid_t, libc::SIGTERM) };
        if result != 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(error);
            }
        }
        Ok(())
    }
    #[cfg(not(unix))]
    {
        child.kill()
    }
}

pub struct ManagedChild(pub Child);

impl ManagedChild {
    pub fn shutdown(&mut self, grace: Duration) -> io::Result<ExitStatus> {
        if let Some(status) = self.0.try_wait()? {
            return Ok(status);
        }
        if terminate(&mut self.0).is_err() {
            let _ = self.0.kill();
        }
        let deadline = Instant::now() + grace;
        loop {
            if let Some(status) = self.0.try_wait()? {
                return Ok(status);
            }
            if Instant::now() >= deadline {
                self.0.kill()?;
                return self.0.wait();
            }
            thread::sleep(Duration::from_millis(25));
        }
    }
}

impl Drop for ManagedChild {
    fn drop(&mut self) {
        if self.0.try_wait().ok().flatten().is_none() {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}

#[derive(Debug)]
pub struct Captured {
    pub status: ExitStatus,
    pub text: String,
}

pub fn capture(
    command: &mut Command,
    stop: &AtomicBool,
    timeout: Duration,
) -> Result<Captured, String> {
    if stopped(stop) {
        return Err("Stopping WARP".into());
    }
    let (reader, writer) = io::pipe().map_err(|e| e.to_string())?;
    let stderr = writer.try_clone().map_err(|e| e.to_string())?;
    let child = command
        .stdin(Stdio::null())
        .stdout(writer)
        .stderr(stderr)
        .spawn();
    // Command retains its copies of Stdio handles. Close them before joining the reader.
    command.stdout(Stdio::null()).stderr(Stdio::null());
    let mut child = ManagedChild(child.map_err(|e| e.to_string())?);
    let output = thread::spawn(move || {
        let mut reader = reader;
        let mut collected = Vec::new();
        let mut buffer = [0; 4096];
        loop {
            match reader.read(&mut buffer) {
                Ok(0) => break,
                Ok(n) => {
                    let remaining = (1024_usize * 1024).saturating_sub(collected.len());
                    collected.extend_from_slice(&buffer[..n.min(remaining)]);
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
        String::from_utf8_lossy(&collected).into_owned()
    });
    let deadline = Instant::now() + timeout;
    let result = loop {
        match child.0.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Err(e) => break Err(e.to_string()),
            Ok(None) => (),
        }
        if stopped(stop) {
            break Err("Stopping WARP".into());
        }
        if Instant::now() >= deadline {
            break Err("Command timed out".into());
        }
        thread::sleep(Duration::from_millis(25));
    };
    if result.is_err() {
        let _ = child.0.kill();
        let _ = child.0.wait();
    }
    // All commands here are direct WARP CLI/curl children, without a shell pipeline.
    let reader_deadline = Instant::now() + Duration::from_millis(250);
    while !output.is_finished() && Instant::now() < reader_deadline {
        thread::sleep(Duration::from_millis(10));
    }
    if !output.is_finished() {
        return Err("Command output pipe did not close".into());
    }
    let text = output.join().map_err(|_| "Command output reader failed")?;
    result.map(|status| Captured { status, text })
}
