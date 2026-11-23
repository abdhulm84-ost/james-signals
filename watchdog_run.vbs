' watchdog_run.vbs — hidden runner for watchdog.py (Task Scheduler entry point)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Trading\james-signals"
sh.Run "cmd /c python watchdog.py >> watchdog_stdout.log 2>&1", 0, False
