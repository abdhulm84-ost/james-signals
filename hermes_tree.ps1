$gw = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*gateway run*' }
if ($gw) {
  "gateway pid=$($gw.ProcessId) parent=$($gw.ParentProcessId)"
  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($gw.ParentProcessId)"
  if ($p) { "parent: $($p.Name) :: $($p.CommandLine)" }
} else { "no gateway running" }
Get-CimInstance Win32_Process -Filter "Name='Hermes.exe'" |
  ForEach-Object { "app: pid=$($_.ProcessId) $($_.ExecutablePath)" }
