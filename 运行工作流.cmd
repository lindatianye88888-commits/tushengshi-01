@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "requestfile=%~1"
if defined requestfile goto request
if exist "request.json" set "requestfile=request.json"
if defined requestfile goto request
if exist "task.json" goto task
if exist "projects\current\request.json" set "requestfile=projects\current\request.json"
if defined requestfile goto request
if exist "projects\workflow-v02-acceptance\request.json" set "requestfile=projects\workflow-v02-acceptance\request.json"
if defined requestfile goto request
echo 未找到请求文件。请先按 examples\request.example.json 保存请求，再将文件拖到此启动器。
goto done
:request
python -m workflow start "%requestfile%"
goto done
:task
python -m workflow run "task.json" --out output
:done
echo.
pause
