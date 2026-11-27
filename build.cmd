@echo off
setlocal
call "%~dp0build\build_all.bat" %*
exit /b %ERRORLEVEL%
