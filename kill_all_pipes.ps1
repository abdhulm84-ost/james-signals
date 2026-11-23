# kill_all_pipes.ps1 — kill every james_pipe python + its cmd wrapper (D115 safety)
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*james_pipe*') -or
    ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*james_pipe*')
} | ForEach-Object {
    Write-Host ("killing " + $_.ProcessId + " " + $_.Name)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "done"
