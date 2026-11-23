$p = Get-CimInstance Win32_Process -Filter "ProcessId=30212"
if ($p) { "{0} :: {1}" -f $p.Name, $p.CommandLine } else { "30212 gone" }
