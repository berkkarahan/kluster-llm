//! Rule expiry runs independently of client connections and agent availability.

use super::{Helper, Result};
use std::{
    sync::{mpsc, Mutex, Weak},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

pub(super) struct Watchdog {
    stop: mpsc::Sender<()>,
    thread: Option<JoinHandle<()>>,
}

impl Watchdog {
    pub(super) fn start(
        helper: Weak<Mutex<Helper>>,
        interval: Duration,
        expire: impl Fn(&mut Helper, Instant) -> Result<()> + Send + 'static,
    ) -> Self {
        let (stop, receiver) = mpsc::channel();
        let thread = thread::spawn(move || {
            while receiver.recv_timeout(interval) == Err(mpsc::RecvTimeoutError::Timeout) {
                let Some(helper) = helper.upgrade() else {
                    break;
                };
                let Ok(mut helper) = helper.lock() else {
                    break;
                };
                // Failures retain their durable cleanup record and are retried.
                let _ = expire(&mut helper, Instant::now());
            }
        });
        Self {
            stop,
            thread: Some(thread),
        }
    }
}

impl Drop for Watchdog {
    fn drop(&mut self) {
        let _ = self.stop.send(());
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
