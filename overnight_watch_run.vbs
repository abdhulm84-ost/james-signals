' overnight_watch_run.vbs — hidden runner for overnight_watch.py check mode (Task Scheduler)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Trading\james-signals"
sh.Run "cmd /c python overnight_watch.py >> watch_stdout.log 2>&1", 0, False
