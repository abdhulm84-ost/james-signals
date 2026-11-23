Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe' or Name='wscript.exe' or Name='cscript.exe'" |
  ForEach-Object { "{0}  {1}" -f $_.ProcessId, $_.CommandLine }
