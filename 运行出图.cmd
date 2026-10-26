@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "taskfile=%~1"
if not defined taskfile if exist "task.json" set "taskfile=task.json"
if not defined taskfile if exist "projects\current\task.json" set "taskfile=projects\current\task.json"
if not defined taskfile if exist "projects\workflow-v02-acceptance\task.json" set "taskfile=projects\workflow-v02-acceptance\task.json"
if not defined taskfile if exist "projects\chengdu-mixc-5km\task.json" set "taskfile=projects\chengdu-mixc-5km\task.json"
if not defined taskfile goto missing
python -m workflow run "%taskfile%" --out output
goto done
:missing
echo 未找到任务文件。请先保存 task.json，或将已有任务文件拖到此启动器。
:done
echo.
pause
