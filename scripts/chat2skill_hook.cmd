@echo off
setlocal

set "ROOT=%CODEX_PLUGIN_ROOT%"
if "%ROOT%"=="" set "ROOT=%CLAUDE_PLUGIN_ROOT%"
if "%ROOT%"=="" (
  echo Chat2Skill hook requires CODEX_PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT 1>&2
  exit /b 1
)

py -3 "%ROOT%\scripts\chat2skill_hook.py" %*
if %ERRORLEVEL% EQU 0 exit /b 0

python "%ROOT%\scripts\chat2skill_hook.py" %*
exit /b %ERRORLEVEL%
