@echo off
rem Double-click to start the visit bar. Optional: pass minutes, e.g.  visit_bar.bat 25
start "" pythonw "%~dp0visit_bar.py" %*
