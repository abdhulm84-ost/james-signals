# Restart the Hermes gateway with FRESH env (reads GEMINI_API_KEY from registry)
$old = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*gateway run*' }
$old | ForEach-Object { Write-Host ("killing gateway pid=" + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 2

$env:GEMINI_API_KEY = [Environment]::GetEnvironmentVariable('GEMINI_API_KEY', 'User')
$py = "C:\Users\passm\AppData\Local\hermes\tools\python-3.14.7+20260901-win32-x64\python.exe"
$main = "C:\Users\passm\AppData\Local\hermes\hermes-agent\hermes_cli\main.py"
Start-Process -FilePath $py -ArgumentList @('-m', 'hermes_cli.main', 'gateway', 'run', '--quiet') `
  -WorkingDirectory "C:\Users\passm\AppData\Local\hermes" -WindowStyle Hidden
Write-Host "gateway relaunched with fresh env (key ..." + $env:GEMINI_API_KEY.Substring($env:GEMINI_API_KEY.Length - 4) + ")"
