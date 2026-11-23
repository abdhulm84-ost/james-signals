@echo off
cd /d C:\Trading\james-signals
echo ===== PRE-OPEN: AUDIT ===== >> system_audit_run.log
C:\Users\passm\miniconda3\python.exe system_audit.py --quiet >> system_audit_run.log 2>&1
echo ===== PRE-OPEN: SELF-HEAL ===== >> system_audit_run.log
C:\Users\passm\miniconda3\python.exe selfheal.py >> system_audit_run.log 2>&1
echo ===== PRE-OPEN: RE-AUDIT (verify heals) ===== >> system_audit_run.log
C:\Users\passm\miniconda3\python.exe system_audit.py --quiet >> system_audit_run.log 2>&1
echo ===== PRE-OPEN: REGRESSION (queue+exit guards) ===== >> system_audit_run.log
C:\Users\passm\miniconda3\python.exe test_durable_queue.py >> system_audit_run.log 2>&1
C:\Users\passm\miniconda3\python.exe test_deadline.py >> system_audit_run.log 2>&1
C:\Users\passm\miniconda3\python.exe test_supersede.py >> system_audit_run.log 2>&1
C:\Users\passm\miniconda3\python.exe test_serial_queue.py >> system_audit_run.log 2>&1
C:\Users\passm\miniconda3\python.exe test_stale_replay.py >> system_audit_run.log 2>&1
C:\Users\passm\miniconda3\python.exe test_exit_scope_d67.py >> system_audit_run.log 2>&1
echo ===== PRE-OPEN COMPLETE ===== >> system_audit_run.log