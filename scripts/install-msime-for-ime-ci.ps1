# Make Microsoft IME (Japanese) the only ja-JP input method on the CI desktop.
# Verification only: checks that the Herdr IME commit fix does not depend on Mozc.
$ErrorActionPreference = 'Stop'
$Tip = '0411:{03B5835F-F03C-411B-9CE2-AA23E1171E36}{A76C93D9-5523-4E90-AAFA-4DB112F9AC76}'
$Dll = Join-Path $env:WINDIR 'System32\IME\IMEJP\imjptip.dll'

Write-Host "OS: $((Get-CimInstance Win32_OperatingSystem).Caption) $((Get-CimInstance Win32_OperatingSystem).Version)"
if (-not (Test-Path $Dll)) {
  Write-Host "imjptip.dll is missing; trying Install-Language ja-JP"
  try {
    Install-Language ja-JP -ErrorAction Stop | Out-Host
  } catch {
    Write-Host "Install-Language failed: $($_.Exception.Message)"
    try {
      Get-WindowsCapability -Online -Name 'Language.Basic~~~ja-JP*' | Add-WindowsCapability -Online | Out-Host
    } catch {
      Write-Host "Add-WindowsCapability failed: $($_.Exception.Message)"
    }
  }
}
if (-not (Test-Path $Dll)) { Write-Host "##[error]Microsoft IME (imjptip.dll) is not available"; exit 1 }
Write-Host "Microsoft IME: $((Get-Item $Dll).VersionInfo.FileVersion)"

$l = New-WinUserLanguageList ja-JP
$l[0].InputMethodTips.Clear()
$l[0].InputMethodTips.Add($Tip)
Set-WinUserLanguageList $l -Force
try { Set-WinDefaultInputMethodOverride -InputTip $Tip -ErrorAction Stop }
catch { Write-Host "default input method override FAILED: $($_.Exception.Message)" }
Write-Host ((Get-WinUserLanguageList | ForEach-Object { "$($_.LanguageTag): $($_.InputMethodTips -join ',')" }) -join "`n")

Get-Process ctfmon -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
Start-Process ctfmon.exe -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5
exit 0
