"""Run one perf_baseline project with a memory guard (dev tool for scripts/perf_baseline.py).

    python scripts/perf_mem_guard.py <projects.json> <out dir> <start MB> <stop MB> <project> [--resume-kit]

Before start: require START_MB free.  While running: sample system free memory and the child tree's
working set every 2 s; if free memory drops below STOP_MB, terminate the child tree and report.
"""
import ctypes, json, subprocess, sys, time

class MS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("x", ctypes.c_ulonglong)]
def free_mb():
    m = MS(); m.l = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)); return m.avail >> 20
def tree_mb(pid):
    out = subprocess.run(["powershell", "-NoProfile", "-c",
        f"$p=Get-CimInstance Win32_Process; $ids=@({pid}); do {{ $n=$ids.Count; $ids=@($ids + ($p | ?{{ $ids -contains $_.ParentProcessId }} | %{{ $_.ProcessId }})) | select -Unique }} while ($ids.Count -gt $n); ($p | ?{{ $ids -contains $_.ProcessId }} | Measure-Object WorkingSetSize -Sum).Sum"],
        capture_output=True, text=True).stdout.strip()
    return int(float(out or 0)) >> 20
START_MB, STOP_MB = int(sys.argv[3]), int(sys.argv[4])
projects, out, name = sys.argv[1], sys.argv[2], sys.argv[5]
f0 = free_mb()
print(f"[guard] {name}: {f0} MB free at start (need {START_MB})", flush=True)
if f0 < START_MB:
    print(f"[guard] {name}: NOT STARTED, too little free memory", flush=True); sys.exit(3)
t = time.time()
child = subprocess.Popen([sys.executable, "scripts/perf_baseline.py", "run", projects, out, name, *sys.argv[6:]])
low, peak, last = f0, 0, 0
while child.poll() is None:
    time.sleep(2)
    f = free_mb(); low = min(low, f)
    if time.time() - last > 10:
        peak = max(peak, tree_mb(child.pid)); last = time.time()
    if f < STOP_MB:
        print(f"[guard] {name}: STOPPED, free memory {f} MB < {STOP_MB} MB after {time.time()-t:.0f} s", flush=True)
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)], capture_output=True)
        sys.exit(4)
    if int(time.time() - t) % 10 < 2:
        print(f"[guard] {name}: {time.time()-t:.0f} s, free {f} MB (lowest {low}), process tree peak {peak} MB", flush=True)
print(f"[guard] {name}: exit {child.returncode} after {time.time()-t:.0f} s; lowest free {low} MB; process tree peak {peak} MB", flush=True)
sys.exit(child.returncode)
