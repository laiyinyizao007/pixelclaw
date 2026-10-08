# 每日自动打招呼 — ai产品经理，最多 120 条
# 由 Windows Task Scheduler 每天 20:00 调用

$ErrorActionPreference = 'Stop'
$ProjectDir = 'C:\Dev\projects\pixelclaw'
$LogDir     = "$ProjectDir\logs\boss"
$LogFile    = "$LogDir\scheduler_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force $LogDir | Out-Null }

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 定时任务启动" | Tee-Object -FilePath $LogFile

Set-Location $ProjectDir
$env:PYTHONUTF8 = '1'

python -m scenarios.boss.scripts.smart_match_greet `
    --keyword 'ai产品经理' `
    --max-greet 120 `
    2>&1 | Tee-Object -Append -FilePath $LogFile

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 定时任务结束，退出码 $LASTEXITCODE" |
    Tee-Object -Append -FilePath $LogFile
