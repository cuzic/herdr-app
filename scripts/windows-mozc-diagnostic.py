"""Replay the observed IME sequence on a disposable Windows CI desktop."""
import ctypes as ct
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


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
    case = sys.argv[1] if len(sys.argv) > 1 else ""
    focus_change = case in ("focus-change", "focus-no-position", "focus-no-drain", "focus-prioritize-ime")
    burst_control = case == "burst-control"
    ima = case in ("ima", "ima-direct", "ima-no-drain", "ima-prioritize")
    fault = "" if focus_change or burst_control or ima else case
    if fault not in ("", "missing-result", "copy-error", "terminal-reject"):
        raise SystemExit(f"Unknown diagnostic fault: {fault}")
    out = (Path("ime-diagnostics") / (case or "baseline")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = Path("target/debug/herdr-app.exe").resolve()
    home = Path(os.environ["RUNNER_TEMP"]) / ("herdr-ime-" + (case or "baseline"))
    home.mkdir(exist_ok=True)
    (home / "shared.json").write_text(
        json.dumps({"frame.welcomed": True, "layout": "main"}), encoding="utf-8"
    )
    env = dict(os.environ, HERDR_APP_HOME=str(home), HC_SESSION=f"ime-ci-{uuid.uuid4().hex[:8]}",
               HERDR_TELEMETRY="0", GPUI_IME_COMPARE="1", HERDR_IME_TEST_FAULT=fault)
    if case == "focus-no-position":
        env["HERDR_IME_TEST_SKIP_POSITION"] = "1"
    if case == "ima-direct":
        env["GPUI_IME_DIRECT_KEY"] = "1"
    if case in ("focus-no-drain", "ima-no-drain"):
        env["HERDR_IME_TEST_SKIP_INPUT_DRAIN"] = "1"
    if case in ("focus-prioritize-ime", "ima-prioritize"):
        env["HERDR_IME_TEST_PRIORITIZE_IME"] = "1"
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
            run_trials(proc, app, env, out, user, owner, enum_callback, fault, focus_change, burst_control, ima)
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
            stopped = subprocess.run([core, "server", "stop"], env=env,
                                     capture_output=True, timeout=15)
            (out / "core-stop.log").write_bytes(stopped.stdout + stopped.stderr)


def run_trials(proc, app, env, out, user, owner, enum_callback, fault, focus_change, burst_control, ima):
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

    def send(keys):
        if owner(user.GetForegroundWindow()) != proc.pid:
            raise SystemExit("INVALID: Herdr lost foreground")
        events = (Input * len(keys))()
        for event, (vk, up) in zip(events, keys):
            event.type = 1
            event.data.keyboard = Keyboard(vk, 0, 2 if up else 0, 0, 0)
        if user.SendInput(len(events), events, ct.sizeof(Input)) != len(events):
            raise ct.WinError(ct.get_last_error())

    def tap(vk):
        send([(vk, False), (vk, True)])

    # Journal timings relative to the first output, in milliseconds.
    observed = [
        (0, "HI"), (114, "TO"), (223, "NO"), (556, "KA"),
        (785, "ZU"), (955, "WO"), (1181, 0x20), (1269, -0x20),
        (1352, "MO"), (1575, "LTU"), (1743, "TO"),
        (1981, 0x0D), (2022, -0x0D),
    ]

    transition_at = 1352
    observed_gap = 83
    suffix = "もっと"
    if ima:
        # dragonflyg4, 2026-11-24 10:39:15.409 UTC. The Space/Enter
        # release gaps are fixture timings, as only their presses were logged.
        observed = [
            (0, "I"), (249, "MA"), (498, 0x20), (560, -0x20),
            (743, "DO"), (988, "U"), (1095, "I"), (1191, "U"),
            (1490, 0x0D), (1550, -0x0D),
        ]
        transition_at = 743
        observed_gap = 183
        suffix = "どういう"

    def command(name, **args):
        result = subprocess.run([app, "do", name, json.dumps(args)], env=env,
                                capture_output=True, timeout=5)
        if result.returncode:
            raise SystemExit(f"INVALID: {name}: {result.stderr.decode(errors='replace')}")
        return json.loads(result.stdout)

    def replay(mid_enter, gap_ms, focus_roundtrip=None, burst=False):
        tap(0xF2)
        time.sleep(.15)
        start = time.perf_counter()
        for offset, events in observed:
            if offset >= transition_at:
                if burst and isinstance(events, str):
                    offset = transition_at
                offset += gap_ms - observed_gap
            time.sleep(max(0, start + offset / 1000 - time.perf_counter()))
            if isinstance(events, str):
                keys = [ord(c) for c in events]
                send([(vk, up) for up in (False, True) for vk in keys])
            else:
                send([(abs(events), events < 0)])
                if events == -0x20:
                    if mid_enter:
                        tap(0x0D)
                    if focus_roundtrip is not None:
                        focus_roundtrip()
        time.sleep(.15)
        tap(0x0D)

    results = []
    trials = [("forced", False, 83)] if fault else [("control", True, 83)] + [
        (f"implicit-{gap}-{attempt}", False, gap)
        for gap in (10, 83, 170) for attempt in range(3)
    ]
    if focus_change:
        trials = [("control", True, 83)] + [
            (f"focus-{gap}-{attempt}", False, gap)
            for gap in (10, 83, 170, 350) for attempt in range(3)
        ]
    if burst_control:
        trials = [("control", True, 83)] + [
            (f"burst-{gap}-{attempt}", False, gap)
            for gap in (10, 83, 170, 350) for attempt in range(3)
        ]
    if ima:
        trials = [("control", True, observed_gap)] + [
            (f"ima-{gap}-{attempt}", False, gap)
            for gap in (10, observed_gap, 350) for attempt in range(4)
        ]
    for label, mid_enter, gap_ms in trials:
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
        focus_roundtrip = None
        if focus_change and not mid_enter:
            terminal = json.loads(opened.stdout)["terminal"]
            other_capture = out / f"{label}-other.txt"
            other_ready = out / f"{label}-other.ready"
            reader = (
                "import msvcrt,pathlib;"
                f"pathlib.Path({str(other_ready)!r}).touch();"
                f"f=pathlib.Path({str(other_capture)!r}).open('w',encoding='utf-8');"
                "\nwhile True:\n    f.write(msvcrt.getwch())\n    f.flush()\n"
            )
            other = command("pane.split", direction="right", argv=[sys.executable, "-c", reader])
            other_terminal = other["terminal"]
            for _ in range(40):
                if other_ready.exists():
                    break
                time.sleep(.25)
            else:
                raise SystemExit("INVALID: second scratch terminal did not start")
            command("pane.focus", terminal=terminal)

            def focus_roundtrip():
                print(f"focus away {label} unix_ns={time.time_ns()}", flush=True)
                command("pane.focus", terminal=other_terminal)
                command("pane.focus", terminal=terminal)
                print(f"focus back {label} unix_ns={time.time_ns()}", flush=True)

        time.sleep(.5)
        print(f"trial {label} ns={time.time_ns()} tick_ms={ct.windll.kernel32.GetTickCount() & 0xffffffff}", flush=True)
        replay(mid_enter, gap_ms, focus_roundtrip, burst_control and not mid_enter)
        for _ in range(40):
            if capture.exists() and capture.stat().st_size:
                break
            time.sleep(.25)
        if focus_change and not mid_enter:
            count = other_capture.stat().st_size if other_capture.exists() else 0
            print(f"other pane {label} bytes={count}", flush=True)
        value = capture.read_text(encoding="utf-8") if capture.exists() else ""
        results.append((label, value))
        print(label, ascii(value), flush=True)

    if fault:
        _, received = results[0]
        trace = (out / "herdr.stderr").read_text(encoding="utf-8", errors="replace")
        if trace.count(f"[herdr-ime] injected {fault}") != 1:
            raise SystemExit(f"INVALID: {fault} did not exercise exactly one commit")
        semantic = "".join(received.splitlines()).replace("\x00", "")
        print(f"FORCED {fault}: only_motto={semantic == 'もっと'} "
              f"nul_count={received.count(chr(0))}", flush=True)
        if semantic != "もっと":
            raise SystemExit(f"Unexpected outcome for {fault}: {ascii(received)}")
        return

    control = "".join(results[0][1].splitlines())
    if not control.endswith(suffix) or control == suffix:
        raise SystemExit("INVALID: explicit commit did not establish the Japanese control")
    implicit = [(label, "".join(value.splitlines())) for label, value in results[1:]]
    missing = [label for label, value in implicit if value == suffix]
    if missing:
        raise SystemExit(f"REPRODUCED: only the second composition reached {missing}")
    different = [label for label, value in implicit if value != control]
    if different:
        raise SystemExit(f"MISMATCH: inspect captured text and IME trace for {different}")
    print(f"PASS: explicit and implicit commits agree in {len(implicit)} trials")


if __name__ == "__main__":
    main()
