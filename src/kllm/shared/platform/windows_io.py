"""Settle synchronous Windows I/O before leaving a bounded local operation."""

from contextlib import contextmanager
import threading
import time


@contextmanager
def synchronous_io_deadline(deadline: float):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
    kernel.CancelSynchronousIo.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenThread(0x0001, False, kernel.GetCurrentThreadId())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    complete, expired = threading.Event(), threading.Event()

    def cancel():
        try:
            if complete.wait(max(0.0, deadline - time.monotonic())):
                return
            expired.set()
            # Cancellation can race the gap between calls. Retry while the
            # original thread/operation is owned, never cancel a reused thread ID.
            while not complete.is_set():
                kernel.CancelSynchronousIo(handle)
                complete.wait(0.01)
        finally:
            kernel.CloseHandle(handle)

    timer = threading.Thread(target=cancel, name="kllm-helper-io-deadline")
    try:
        timer.start()
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        yield
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("Local helper I/O exceeded its deadline")
    except OSError as error:
        if expired.is_set():
            raise TimeoutError("Local helper I/O exceeded its deadline") from error
        raise
    finally:
        complete.set()
        timer.join()  # No cancellation worker survives the owned operation.


def open_pipe(path: str, deadline: float):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.WaitNamedPipeW.restype = wintypes.BOOL
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Local helper pipe stayed busy")
        try:
            return open(path, "r+b", buffering=0)
        except OSError as error:
            if error.winerror != 231:  # ERROR_PIPE_BUSY
                raise
            kernel.WaitNamedPipeW(path, max(1, min(100, int(remaining * 1000))))
