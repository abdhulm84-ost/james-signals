$k = [Environment]::GetEnvironmentVariable('GEMINI_API_KEY', 'User')
if ($k) { "set: {0}...{1} (len {2})" -f $k.Substring(0, 8), $k.Substring($k.Length - 4), $k.Length } else { "not set" }
