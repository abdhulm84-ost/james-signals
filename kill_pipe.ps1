Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*james_pipe*' } |
  ForEach-Object { Write-Host ("killing " + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }
