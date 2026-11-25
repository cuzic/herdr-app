# Diagnostic only (not part of the PR): start Herdr in a scratch home and
# report the class names of its windows, as Spy++, UI Automation and
# AutoHotkey see them.
param([Parameter(Mandatory)] [string] $App, [Parameter(Mandatory)] [string] $Label)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public static class Win {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindowExW(IntPtr parent, IntPtr after, string cls, string title);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public static readonly IntPtr HWND_MESSAGE = new IntPtr(-3);
}
'@
$home_ = Join-Path $env:RUNNER_TEMP "herdr-home-$Label"
New-Item -ItemType Directory -Force $home_ | Out-Null
$env:HERDR_APP_HOME = $home_
$env:HC_SESSION = "class-ci-$Label"
$env:HERDR_TELEMETRY = '0'
$coreDir = Join-Path $env:LOCALAPPDATA 'Programs/herdr-core'
$core = @('hc.exe', 'herdr-core.exe') | ForEach-Object { Join-Path $coreDir $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
& $core server start
if ($LASTEXITCODE) { throw 'INVALID: herdr-core server did not start' }
$proc = Start-Process -FilePath $App -PassThru
try {
  $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
  $top = $null
  foreach ($i in 1..60) {
    $top = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
    if ($top) { break }
    Start-Sleep -Milliseconds 500
  }
  if (-not $top) { throw 'INVALID: no Herdr window appeared' }
  $c = $top.Current
  "$Label uia: Name=`"$($c.Name)`" ClassName=`"$($c.ClassName)`" FrameworkId=`"$($c.FrameworkId)`""
  # Message-only windows are not in the UIA tree; list those owned by Herdr.
  $h = [IntPtr]::Zero
  $found = 0
  while ($true) {
    # PowerShell turns `$null into "" for a string argument; NullString passes a real null.
    $h = [Win]::FindWindowExW([Win]::HWND_MESSAGE, $h, [NullString]::Value, [NullString]::Value)
    if ($h -eq [IntPtr]::Zero) { break }
    [uint32]$owner = 0; [void][Win]::GetWindowThreadProcessId($h, [ref]$owner)
    if ($owner -ne $proc.Id) { continue }
    $sb = New-Object System.Text.StringBuilder 256
    [void][Win]::GetClassNameW($h, $sb, 256)
    "$Label message-only: ClassName=`"$sb`""
    $found++
  }
  if (-not $found) { throw 'INVALID: no message-only window owned by Herdr' }
} finally {
  if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
  & $core server stop
}
