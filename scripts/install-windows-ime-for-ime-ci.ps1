# Make one Windows input method the only one on the CI desktop.
# Verification only: checks that the Herdr IME commit fix does not depend on Mozc.
#   Microsoft IME (ja):  -Lang ja-JP -Tip '0411:{03B5835F-F03C-411B-9CE2-AA23E1171E36}{A76C93D9-5523-4E90-AAFA-4DB112F9AC76}'
#   Microsoft Pinyin:    -Lang zh-CN -Tip '0804:{81D4E9C9-1D3B-41BC-9E6C-4B40BF79E35E}{FA550B04-5AD7-411F-A5AC-CA038EC515D7}'
#   Microsoft Korean:    -Lang ko-KR -Tip '0412:{A028AE76-01B1-46C2-99C4-ACD9858AE02F}{B5FE1F02-D5F2-4445-9C03-C568F23C99A1}'
#   Google 日本語入力:   -Lang ja-JP -Tip '0411:{D5A86FD5-5308-47EA-AD16-9C4EB160EC3C}{773EB24E-CA1D-4B1B-B420-FA985BB0B80D}' -Gji
param(
  [Parameter(Mandatory)] [string]$Lang,
  [Parameter(Mandatory)] [string]$Tip,
  [switch]$Gji
)
$ErrorActionPreference = 'Continue'
Write-Host "OS: $((Get-CimInstance Win32_OperatingSystem).Caption) $((Get-CimInstance Win32_OperatingSystem).Version)"
$clsid = $Tip.Substring(5, 38)
$tipKey = "HKLM:\SOFTWARE\Microsoft\CTF\TIP\$clsid"

if ($Gji) {
  # Same retry as awase tools/ci/install-gji.ps1: choco sometimes stalls and exits -1.
  $ok = $false
  for ($i = 1; $i -le 3 -and -not $ok; $i++) {
    Write-Host "GJI install attempt $i/3"
    $job = Start-Job { choco install googlejapaneseinput -y --no-progress --force 2>&1 | Select-Object -Last 5; "EXIT=$LASTEXITCODE" }
    if (Wait-Job $job -Timeout 240) {
      $out = Receive-Job $job; $out | ForEach-Object { Write-Host $_ }
      $ok = $out -contains 'EXIT=0'
    } else {
      Write-Host "GJI install timed out"; Stop-Job $job
      Get-Process choco, msiexec -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Remove-Job $job -Force
    if (-not $ok) { Start-Sleep -Seconds 10 }
  }
  if (-not $ok) { Write-Host "##[error]GJI install failed"; exit 1 }
  # Mozc config field 22 = 0: no first-run dialog.
  $dir = Join-Path $env:USERPROFILE 'AppData\LocalLow\Google\Google Japanese Input'
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  [IO.File]::WriteAllBytes((Join-Path $dir 'config1.db'), [byte[]](0xB0,0x01,0x00))
}

$l = New-WinUserLanguageList $Lang
$l[0].InputMethodTips.Clear()
$l[0].InputMethodTips.Add($Tip)
Set-WinUserLanguageList -LanguageList $l -Force
$sw = [Diagnostics.Stopwatch]::StartNew()
for ($i = 0; $i -lt 36 -and -not (Test-Path $tipKey); $i++) { Start-Sleep -Seconds 5 }
Write-Host "TIP_REGISTERED=$(Test-Path $tipKey) after $([int]$sw.Elapsed.TotalSeconds)s"
if (-not (Test-Path $tipKey)) {
  Get-WindowsCapability -Online -Name "Language.Basic~~~$Lang~*" | Add-WindowsCapability -Online | Out-Host
  Set-WinUserLanguageList -LanguageList $l -Force
  for ($i = 0; $i -lt 24 -and -not (Test-Path $tipKey); $i++) { Start-Sleep -Seconds 5 }
  Write-Host "TIP_REGISTERED(after capability)=$(Test-Path $tipKey) after $([int]$sw.Elapsed.TotalSeconds)s"
}
if (-not (Test-Path $tipKey)) { Write-Host "##[error]$Tip is not registered"; exit 1 }
try { Set-WinDefaultInputMethodOverride -InputTip $Tip -ErrorAction Stop }
catch { Write-Host "default input method override FAILED: $($_.Exception.Message)" }
Write-Host ((Get-WinUserLanguageList | ForEach-Object { "$($_.LanguageTag): $($_.InputMethodTips -join ',')" }) -join "`n")

Get-Process | Where-Object { $_.Name -match 'googleime' } | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process ctfmon -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
Start-Process ctfmon.exe -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5
exit 0
