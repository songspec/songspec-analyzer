# Idle memory of the desktop app, as Task Manager's "Memory" column shows it (private working set), and the full working set.
# Starts the app with a song, waits for the analysis to finish and the app to go idle, then samples for a few seconds.
#   powershell -ExecutionPolicy Bypass -File tools\measure_idle_ram.ps1 -Exe rust\target\release\SongSpecAnalyzer.exe -Song C:\path\song.flac
param([string]$Exe = "rust\target\release\SongSpecAnalyzer.exe", [string]$Song = "", [int]$WaitSeconds = 120)
$p = if ($Song) { Start-Process -FilePath $Exe -ArgumentList "`"$Song`"" -PassThru } else { Start-Process -FilePath $Exe -PassThru }
Write-Host "started pid $($p.Id); waiting $WaitSeconds s for the analysis and idle"
$peak = 0
for ($i = 0; $i -lt $WaitSeconds; $i++) {
    Start-Sleep -Seconds 1
    $p.Refresh()
    if ($p.HasExited) { Write-Host "the app exited"; exit 1 }
    if ($p.PrivateMemorySize64 -gt $peak) { $peak = $p.PrivateMemorySize64 }
}
$samples = @()
for ($i = 0; $i -lt 5; $i++) {
    $c = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -Filter "IDProcess = $($p.Id)"
    $samples += $c.WorkingSetPrivate
    Start-Sleep -Seconds 1
}
$p.Refresh()
$priv = ($samples | Measure-Object -Average).Average / 1MB
Write-Host ("idle private working set (Task Manager 'Memory'): {0:N0} MB" -f $priv)
Write-Host ("idle working set incl. shared pages: {0:N0} MB" -f ($p.WorkingSet64 / 1MB))
Write-Host ("peak private bytes during analysis: {0:N0} MB" -f ($peak / 1MB))
Stop-Process -Id $p.Id
