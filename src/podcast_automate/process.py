from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from .errors import AppError


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


def run_process(args: list[str], *, timeout: int, cwd: Path | None = None,
                input_text: str | None = None, env: dict | None = None,
                on_stdout_line=None, cancel_check=None) -> subprocess.CompletedProcess:
    """Never pass prompts or project paths through a shell."""
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
    if on_stdout_line is not None or cancel_check is not None:
        return _stream_process(process, input_text, timeout, args, on_stdout_line, cancel_check)
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


def _stream_process(process, input_text, timeout, args, callback, cancel_check):
    """Drain both pipes while reporting JSONL events before the process completes."""
    stdout, stderr = [], []

    def receive(pipe, chunks, observe=False):
        try:
            for line in pipe:
                chunks.append(line)
                if observe and callback is not None:
                    try:
                        callback(line)
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

    threads = [threading.Thread(target=receive, args=(process.stdout, stdout, True), daemon=True),
               threading.Thread(target=receive, args=(process.stderr, stderr), daemon=True),
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
