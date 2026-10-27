use super::{refuse, Result};
use std::io::{Read, Write};
fn exchange<S: Read + Write>(
    mut stream: S,
    dispatch: &mut impl FnMut(&[u8]) -> Vec<u8>,
) -> Result<()> {
    let mut length = [0; 4];
    stream.read_exact(&mut length)?;
    let length = u32::from_be_bytes(length) as usize;
    if length > 65536 {
        return Err(refuse("Request exceeds 64 KiB"));
    }
    let mut input = vec![0; length];
    stream.read_exact(&mut input)?;
    let output = dispatch(&input);
    if output.len() > 65536 {
        return Err(refuse("Response exceeds 64 KiB"));
    }
    stream.write_all(&(output.len() as u32).to_be_bytes())?;
    stream.write_all(&output)?;
    stream.flush()?;
    Ok(())
}
#[cfg(unix)]
pub fn serve(account: &str, mut dispatch: impl FnMut(&[u8]) -> Vec<u8>) -> Result<()> {
    use std::{
        os::{
            fd::AsRawFd,
            unix::{fs::PermissionsExt, net::UnixListener},
        },
        time::Duration,
    };
    let uid: u32 = account.parse()?;
    let path = format!("/var/run/kllm-network-{uid}.sock");
    if std::path::Path::new(&path).exists() {
        if std::os::unix::net::UnixStream::connect(&path).is_ok() {
            return Err(refuse("Network helper is already running"));
        }
        std::fs::remove_file(&path)?;
    }
    let listener = UnixListener::bind(&path)?;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
    let cpath = std::ffi::CString::new(path)?;
    if unsafe { libc::chown(cpath.as_ptr(), uid, u32::MAX) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    for stream in listener.incoming() {
        let stream = stream?;
        stream.set_read_timeout(Some(Duration::from_secs(5)))?;
        stream.set_write_timeout(Some(Duration::from_secs(5)))?;
        #[cfg(target_os = "macos")]
        let allowed = {
            let (mut peer_uid, mut peer_gid) = (0, 0);
            unsafe {
                libc::getpeereid(stream.as_raw_fd(), &mut peer_uid, &mut peer_gid) == 0
                    && peer_uid == uid
            }
        };
        #[cfg(target_os = "linux")]
        let allowed = {
            let mut cred: libc::ucred = unsafe { std::mem::zeroed() };
            let mut length = std::mem::size_of_val(&cred) as libc::socklen_t;
            unsafe {
                libc::getsockopt(
                    stream.as_raw_fd(),
                    libc::SOL_SOCKET,
                    libc::SO_PEERCRED,
                    &mut cred as *mut _ as *mut _,
                    &mut length,
                ) == 0
                    && cred.uid == uid
            }
        };
        if allowed {
            let _ = exchange(stream, &mut dispatch);
        }
    }
    Ok(())
}
#[cfg(windows)]
fn exchange_windows(
    file: &std::fs::File,
    dispatch: &mut impl FnMut(&[u8]) -> Vec<u8>,
    timeout: std::time::Duration,
) -> Result<()> {
    use std::os::windows::io::AsRawHandle;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc,
    };
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        Storage::FileSystem::FlushFileBuffers,
        System::{
            Threading::{GetCurrentThreadId, OpenThread, THREAD_TERMINATE},
            IO::CancelSynchronousIo,
        },
    };
    let thread = unsafe { OpenThread(THREAD_TERMINATE, 0, GetCurrentThreadId()) };
    if thread.is_null() {
        return Err(std::io::Error::last_os_error().into());
    }
    let thread = thread as usize;
    let (complete, wait) = mpsc::channel::<()>();
    let expired = Arc::new(AtomicBool::new(false));
    let timed_out = Arc::clone(&expired);
    let watchdog = std::thread::spawn(move || {
        if matches!(
            wait.recv_timeout(timeout),
            Err(mpsc::RecvTimeoutError::Timeout)
        ) {
            timed_out.store(true, Ordering::Release);
            loop {
                // A single cancellation can miss the interval before the next
                // synchronous call. The thread handle remains owned until settled.
                unsafe {
                    CancelSynchronousIo(thread as _);
                }
                if !matches!(
                    wait.recv_timeout(std::time::Duration::from_millis(10)),
                    Err(mpsc::RecvTimeoutError::Timeout)
                ) {
                    break;
                }
            }
        }
        unsafe {
            CloseHandle(thread as _);
        }
    });
    let result = (|| {
        exchange(file, dispatch)?;
        // DisconnectNamedPipe discards unread bytes. A file flush is a no-op;
        // native pipe flushing waits for the client under the same deadline.
        if unsafe { FlushFileBuffers(file.as_raw_handle() as _) } == 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(())
    })();
    let _ = complete.send(());
    watchdog
        .join()
        .map_err(|_| refuse("Helper I/O deadline thread failed"))?;
    if expired.load(Ordering::Acquire) {
        return Err(refuse("Helper pipe exchange exceeded its deadline"));
    }
    result
}
#[cfg(windows)]
pub fn serve(account: &str, mut dispatch: impl FnMut(&[u8]) -> Vec<u8>) -> Result<()> {
    use std::{os::windows::io::FromRawHandle, ptr};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, LocalFree, INVALID_HANDLE_VALUE},
        Security::{
            Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW,
            SECURITY_ATTRIBUTES,
        },
        Storage::FileSystem::{FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_ACCESS_DUPLEX},
        System::Pipes::{
            ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_READMODE_BYTE,
            PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_WAIT,
        },
    };
    let wide = |value: &str| value.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
    let name = wide(&format!(r"\\.\pipe\kllm-network-{account}"));
    let sddl = wide(&format!("D:P(A;;GA;;;SY)(A;;GA;;;{account})"));
    let mut descriptor = ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            1,
            &mut descriptor,
            ptr::null_mut(),
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error().into());
    }
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    loop {
        let handle = unsafe {
            CreateNamedPipeW(
                name.as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                65540,
                65540,
                5000,
                &attributes,
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            unsafe {
                LocalFree(descriptor);
            }
            return Err(std::io::Error::last_os_error().into());
        }
        let connected = unsafe { ConnectNamedPipe(handle, ptr::null_mut()) } != 0
            || std::io::Error::last_os_error().raw_os_error() == Some(535);
        if connected {
            let file = unsafe { std::fs::File::from_raw_handle(handle) };
            let _ = exchange_windows(&file, &mut dispatch, std::time::Duration::from_secs(45));
            unsafe {
                DisconnectNamedPipe(handle);
            }
            drop(file);
        } else {
            unsafe {
                CloseHandle(handle);
            }
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::net::UnixStream;
    #[test]
    fn real_local_ipc_roundtrip_and_size_boundary() {
        let (mut client, server) = UnixStream::pair().unwrap();
        let thread = std::thread::spawn(move || {
            exchange(server, &mut |raw| raw.iter().rev().copied().collect())
        });
        client.write_all(&3u32.to_be_bytes()).unwrap();
        client.write_all(b"abc").unwrap();
        let mut response = [0; 7];
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, &[0, 0, 0, 3, b'c', b'b', b'a']);
        thread.join().unwrap().unwrap();
        let (mut client, server) = UnixStream::pair().unwrap();
        client.write_all(&65537u32.to_be_bytes()).unwrap();
        assert!(exchange(server, &mut |_| panic!(
            "Oversized input cannot be dispatched"
        ))
        .is_err());
    }
}

#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;
    use std::{
        os::windows::io::FromRawHandle,
        sync::{
            atomic::{AtomicU64, Ordering},
            mpsc,
        },
        time::Duration,
    };
    use windows_sys::Win32::{
        Foundation::INVALID_HANDLE_VALUE,
        Storage::FileSystem::{FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_ACCESS_DUPLEX},
        System::Pipes::{
            ConnectNamedPipe, CreateNamedPipeW, PIPE_READMODE_BYTE, PIPE_REJECT_REMOTE_CLIENTS,
            PIPE_TYPE_BYTE, PIPE_WAIT,
        },
    };
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);

    fn pair() -> (std::fs::File, std::fs::File) {
        let name = format!(
            r"\\.\pipe\kllm-ipc-test-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        );
        let wide: Vec<_> = name.encode_utf16().chain(Some(0)).collect();
        let handle = unsafe {
            CreateNamedPipeW(
                wide.as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                4096,
                4096,
                1000,
                std::ptr::null(),
            )
        };
        assert_ne!(handle, INVALID_HANDLE_VALUE);
        let server = unsafe { std::fs::File::from_raw_handle(handle as _) };
        let client = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(name)
            .unwrap();
        assert!(
            unsafe { ConnectNamedPipe(handle, std::ptr::null_mut()) } != 0
                || std::io::Error::last_os_error().raw_os_error() == Some(535)
        );
        (client, server)
    }

    #[test]
    fn real_pipe_reply_waits_for_consumption_before_disconnect() {
        let (mut client, server) = pair();
        let (dispatch, dispatched) = mpsc::channel();
        let (complete, finished) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            let result = exchange_windows(
                &server,
                &mut |raw| {
                    dispatch.send(()).unwrap();
                    raw.iter().rev().copied().collect()
                },
                Duration::from_secs(1),
            );
            complete.send(result.is_ok()).unwrap();
        });
        client.write_all(&[0, 0, 0, 3, b'a', b'b', b'c']).unwrap();
        dispatched.recv_timeout(Duration::from_secs(1)).unwrap();
        assert!(
            finished.recv_timeout(Duration::from_millis(50)).is_err(),
            "Server must not discard its unread reply"
        );
        let mut response = [0; 7];
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, &[0, 0, 0, 3, b'c', b'b', b'a']);
        assert!(finished.recv_timeout(Duration::from_secs(1)).unwrap());
        worker.join().unwrap();
    }

    #[test]
    fn partial_frame_is_cancelled_without_dispatch() {
        let (mut client, server) = pair();
        let (complete, finished) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            let failed = exchange_windows(
                &server,
                &mut |_| panic!("Partial input cannot reach helper policy"),
                Duration::from_millis(80),
            )
            .is_err();
            complete.send(failed).unwrap();
        });
        client.write_all(&[0, 0]).unwrap();
        let result = finished.recv_timeout(Duration::from_millis(500));
        drop(client);
        worker.join().unwrap();
        assert!(result.unwrap());
    }

    #[test]
    fn deadline_during_dispatch_still_cancels_later_pipe_flush() {
        let (mut client, server) = pair();
        let (complete, finished) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            let result = exchange_windows(
                &server,
                &mut |_| {
                    std::thread::sleep(Duration::from_millis(120));
                    b"response".to_vec()
                },
                Duration::from_millis(60),
            );
            complete.send(result.is_err()).unwrap();
        });
        client.write_all(&[0, 0, 0, 1, b'x']).unwrap();
        let result = finished.recv_timeout(Duration::from_millis(500));
        drop(client); // A failing regression cannot strand its native test thread.
        worker.join().unwrap();
        assert_eq!(
            result.unwrap(),
            true,
            "No one-shot cancellation gap may leave flush blocked"
        );
    }

    #[test]
    fn oversized_pipe_request_never_dispatches() {
        let (mut client, server) = pair();
        client.write_all(&65537u32.to_be_bytes()).unwrap();
        assert!(exchange_windows(
            &server,
            &mut |_| panic!("Oversized input cannot reach helper policy"),
            Duration::from_secs(1)
        )
        .is_err());
    }
}
