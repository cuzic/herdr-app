"""Replay the observed IME sequence on a disposable Windows CI desktop."""
import ctypes as ct
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import subprocess
import sys
import time


class Keyboard(ct.Structure):
    _fields_ = [("vk", wt.WORD), ("scan", wt.WORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("extra", ct.c_size_t)]


class Mouse(ct.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG), ("data", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD), ("extra", ct.c_size_t)]


class Payload(ct.Union):
    _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]


class Input(ct.Structure):
    _fields_ = [("type", wt.DWORD), ("data", Payload)]


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "win32":
        raise SystemExit("Run only on the disposable Windows Actions runner")
    out = Path("ime-diagnostics").resolve()
    out.mkdir(exist_ok=True)
    app = Path("target/debug/herdr-app.exe").resolve()
    home = Path(os.environ["RUNNER_TEMP"]) / "herdr-ime-home"
    home.mkdir(exist_ok=True)
    (home / "shared.json").write_text(
        json.dumps({"frame.welcomed": True, "layout": "main"}), encoding="utf-8"
    )
    env = dict(os.environ, HERDR_APP_HOME=str(home), HC_SESSION="ime-ci",
               HERDR_TELEMETRY="0", HERDR_IME_TRACE="1")
    core_dir = Path(os.environ["LOCALAPPDATA"]) / "Programs/herdr-core"
    core = next((core_dir / name for name in ("hc.exe", "herdr-core.exe")
                 if (core_dir / name).is_file()), "hc.exe")
    started = subprocess.run([core, "server", "start"], env=env,
                             capture_output=True, timeout=30)
    (out / "core-start.log").write_bytes(started.stdout + started.stderr)
    if started.returncode:
        print((started.stdout + started.stderr).decode(errors="replace"))
        raise SystemExit("INVALID: herdr-core server did not start")
    user = ct.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = wt.HWND
    user.GetWindowThreadProcessId.argtypes = [wt.HWND, ct.POINTER(wt.DWORD)]
    user.SetForegroundWindow.argtypes = [wt.HWND]
    user.ShowWindow.argtypes = [wt.HWND, ct.c_int]
    enum_callback = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user.EnumWindows.argtypes = [enum_callback, wt.LPARAM]
    user.SendInput.argtypes = [wt.UINT, ct.POINTER(Input), ct.c_int]

    def owner(hwnd):
        pid = wt.DWORD()
        user.GetWindowThreadProcessId(hwnd, ct.byref(pid))
        return pid.value

    with (out / "herdr.stdout").open("wb") as stdout, (out / "herdr.stderr").open("wb") as stderr:
        proc = subprocess.Popen([app], env=env, stdout=stdout, stderr=stderr)
        try:
            run_trials(proc, app, env, out, user, owner, enum_callback)
        except BaseException:
            stderr.flush()
            print((out / "herdr.stderr").read_bytes()[-9000:].decode(errors="replace"))
            stdout.flush()
            print((out / "herdr.stdout").read_bytes()[-9000:].decode(errors="replace"))
            raise
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)


def run_trials(proc, app, env, out, user, owner, enum_callback):
    windows = []

    @enum_callback
    def find(hwnd, _):
        if owner(hwnd) == proc.pid:
            windows.append(hwnd)
        return True

    for _ in range(120):
        if proc.poll() is not None:
            raise SystemExit(f"INVALID: Herdr exited with {proc.returncode}")
        user.EnumWindows(find, 0)
        if windows:
            user.ShowWindow(windows[0], 9)
            user.SetForegroundWindow(windows[0])
            if owner(user.GetForegroundWindow()) == proc.pid:
                break
            windows.clear()
        time.sleep(.25)
    else:
        raise SystemExit("INVALID: runner cannot foreground the Herdr window")

    def key(vk):
        if owner(user.GetForegroundWindow()) != proc.pid:
            raise SystemExit("INVALID: Herdr lost foreground")
        events = (Input * 2)()
        for i, flags in enumerate((0, 2)):
            events[i].type = 1
            events[i].data.keyboard = Keyboard(vk, 0, flags, 0, 0)
        if user.SendInput(2, events, ct.sizeof(Input)) != 2:
            raise ct.WinError(ct.get_last_error())
        time.sleep(.045)

    results = []
    for label, mid_enter in (("control", True), ("implicit", False)):
        capture = out / f"{label}.txt"
        ready = out / f"{label}.ready"
        # The pane records text after the console line discipline receives it.
        code = (
            "import pathlib,sys;"
            "sys.stdin.reconfigure(encoding='utf-8');"
            f"pathlib.Path({str(ready)!r}).touch();"
            f"pathlib.Path({str(capture)!r}).write_text(sys.stdin.readline()"
            + ",encoding='utf-8')"
        )
        request = json.dumps({
            "name": label, "folder": str(out), "argv": [sys.executable, "-c", code]
        })
        for _ in range(20):
            if proc.poll() is not None:
                raise SystemExit(f"INVALID: Herdr exited with {proc.returncode}")
            opened = subprocess.run([app, "do", "space.new", request], env=env,
                                    capture_output=True, timeout=5)
            if opened.returncode == 0:
                break
            time.sleep(.25)
        response = opened.stdout + opened.stderr
        (out / f"{label}-space.log").write_bytes(response)
        print(label, response.decode(errors="replace"), flush=True)
        if opened.returncode:
            raise SystemExit("INVALID: could not create the scratch space")
        for _ in range(60):
            if ready.exists():
                break
            time.sleep(.25)
        else:
            raise SystemExit("INVALID: scratch terminal did not start")
        time.sleep(.5)
        key(0xF2)
        for char in "hitonokazuwo":
            key(ord(char.upper()))
        key(0x20)
        time.sleep(.17)
        if mid_enter:
            key(0x0D)
        for char in "moltuto":
            key(ord(char.upper()))
        key(0x0D)  # Commit the current IME composition.
        time.sleep(.15)
        key(0x0D)  # Submit the console line to the capture process.
        for _ in range(40):
            if capture.exists() and capture.stat().st_size:
                break
            time.sleep(.25)
        value = capture.read_text(encoding="utf-8") if capture.exists() else ""
        results.append((label, value))
        print(label, ascii(value), flush=True)

    control, implicit = ("".join(value.splitlines()) for _, value in results)
    if not control.endswith("もっと") or control == "もっと":
        raise SystemExit("INVALID: explicit commit did not establish the Japanese control")
    if implicit == "もっと":
        raise SystemExit("REPRODUCED: only the second composition reached the pane")
    if implicit != control:
        raise SystemExit("MISMATCH: inspect captured text and IME trace")
    print("PASS: explicit and implicit commits agree in this trial")


if __name__ == "__main__":
    main()
