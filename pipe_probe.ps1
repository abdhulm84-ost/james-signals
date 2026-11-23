Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='wscript.exe'" |
  Where-Object { $_.CommandLine -like '*james_pipe*' -or $_.CommandLine -like '*james_signals*' } |
  ForEach-Object { "{0}  {1}" -f $_.ProcessId, $_.CommandLine }
