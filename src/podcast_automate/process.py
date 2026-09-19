from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from .errors import AppError

# A streaming model call that sends nothing for this long is treated as hung and stopped, well
# before the absolute deadline; the caller repeats it once. Absolute deadlines stay in project.yaml.
STALL_TIMEOUT_SECONDS = 600


def stop_process_tree(process: subprocess.Popen) -> None:
    """Stop only this owned worker and its descendants, including new sessions."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=10, check=False)
    else:
        import psutil

        stopped = []
        try:
            parent = psutil.Process(process.pid)
            # Model calls can run in multiple threads and separate sessions;
            # killing the Studio process group alone would leave those alive.
            pending = [parent]
            while pending:
                child = pending.pop()
                try:
                    child.suspend()
                    stopped.append(child)
                    # Enumerate after suspending: this parent cannot create
                    # another child between enumeration and termination.
                    pending.extend(child.children())
                except psutil.NoSuchProcess:
                    pass
            for child in reversed(stopped):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(stopped[1:], timeout=5)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            # Never leave surviving processes frozen after a failed stop.
            for child in stopped:
                try:
                    child.resume()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            raise AppError("Der Arbeitsprozess konnte nicht angehalten werden. Zugriffsrechte prüfen.",
                           code="worker_stop") from exc
    process.wait(timeout=10)


def _stop_tree(process: subprocess.Popen) -> None:
    stop_process_tree(process)
    process.communicate()


def stall_error(seconds) -> AppError:
    minutes = seconds // 60
    duration = f"{minutes} Minuten" if seconds % 60 == 0 else f"{seconds} Sekunden"
    return AppError(f"Der Modellaufruf hat {duration} lang keine Ausgabe geliefert und wurde als hängend beendet.",
                    code="stall")


def run_process(args: list[str], *, timeout: int, cwd: Path | None = None,
                input_text: str | None = None, env: dict | None = None,
                on_stdout_line=None, on_stderr_line=None, cancel_check=None,
                stall_timeout: int | None = None) -> subprocess.CompletedProcess:
    """Never pass prompts or project paths through a shell.

    ``stall_timeout`` applies only to processes that stream their progress on stdout: when no line
    arrives for that long the process is stopped with ``AppError(code="stall")``.
    """
    try:
        process = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except OSError as exc:
        raise AppError(f"Programm konnte nicht gestartet werden: {args[0]}",
                       code="missing_executable", status="blocked") from exc
    if any(value is not None for value in (on_stdout_line, on_stderr_line, cancel_check, stall_timeout)):
        return _stream_process(process, input_text, timeout, args, on_stdout_line, cancel_check, on_stderr_line,
                               stall_timeout)
    try:
        stdout, stderr = process.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _stop_tree(process)
        raise AppError("Zeitlimit erreicht; der Auftrag kann wiederaufgenommen werden.",
                       code="timeout") from exc
    except KeyboardInterrupt:
        _stop_tree(process)
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _stream_process(process, input_text, timeout, args, callback, cancel_check, stderr_callback=None,
                    stall_timeout=None):
    """Drain both pipes while reporting JSONL events before the process completes."""
    stdout, stderr = [], []
    last_output = [time.monotonic()]

    def receive(pipe, chunks, observer=None, track=False):
        try:
            for line in pipe:
                chunks.append(line)
                if track:
                    last_output[0] = time.monotonic()
                if observer is not None:
                    try:
                        observer(line)
                    except Exception:
                        pass  # Optional status reporting must never break generation.
        finally:
            pipe.close()

    def send():
        try:
            if input_text:
                process.stdin.write(input_text)
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    threads = [threading.Thread(target=receive, args=(process.stdout, stdout, callback, True), daemon=True),
               threading.Thread(target=receive, args=(process.stderr, stderr, stderr_callback), daemon=True),
               threading.Thread(target=send, daemon=True)]
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.start()
    try:
        while True:
            if cancel_check is not None and cancel_check():
                raise AppError("Modellaufruf angehalten.", code="interrupted", status="interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppError("Zeitlimit erreicht; der Auftrag kann wiederaufgenommen werden.", code="timeout")
            if stall_timeout is not None and time.monotonic() - last_output[0] > stall_timeout:
                raise stall_error(stall_timeout)
            try:
                process.wait(timeout=min(1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        stop_process_tree(process)
        raise
    finally:
        for thread in threads:
            thread.join(timeout=10)
    return subprocess.CompletedProcess(args, process.returncode, "".join(stdout), "".join(stderr))
