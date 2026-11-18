<#
工程图QC 插件安装脚本 v272
用法：右键 install.bat -> 以管理员身份运行
可选参数：
  -DryRun        只检查环境，不复制/不注册
  -Dest <路径>   指定安装目录（默认 %LOCALAPPDATA%\SwQcAddin）
  -SkipRegister  只复制/写配置，不执行 regasm（自测用）
#>
param(
    [switch]$DryRun,
    [string]$Dest = "",
    [string]$Python = "",
    [switch]$SkipRegister,
    [switch]$SkipSwCheck
)
$ErrorActionPreference = "Continue"

function Info($m) { Write-Host "[信息] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK]   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[提示] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[错误] $m" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }

$src = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($Dest)) { $Dest = Join-Path $env:LOCALAPPDATA "SwQcAddin" }

Write-Host "============================================================"
Write-Host "  工程图QC 插件安装程序 (SwQcAddin v272)"
Write-Host "============================================================"
Write-Host "  安装目录: $Dest"

# 1) 管理员权限
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $DryRun -and -not $SkipRegister) {
    Info "需要管理员权限，正在请求提权..."
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList @(
            "-NoProfile","-ExecutionPolicy","Bypass","-File","`"$PSCommandPath`"",
            "-Dest","`"$Dest`""
        )
    } catch {
        Fail "提权失败，请右键 install.bat 选择“以管理员身份运行”。"
    }
    exit 0
}

# 2) SolidWorks 必须关闭
$sw = Get-Process SLDWORKS -ErrorAction SilentlyContinue
if ($sw -and -not $DryRun -and -not $SkipSwCheck) { Fail "检测到 SolidWorks 正在运行，请先完全关闭 SolidWorks。" }
Ok "SolidWorks 未运行"
$installedSw = @()
try {
    $installedSw = @(Get-ChildItem 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\SolidWorks' -ErrorAction Stop |
        Where-Object { $_.PSChildName -match '^SolidWorks (20\d\d)$' } |
        ForEach-Object { [int]([regex]::Match($_.PSChildName, '20\d\d').Value) } |
        Sort-Object -Unique)
} catch { }
if ($installedSw.Count -gt 0) {
    Info ("检测到 SolidWorks 版本: " + ($installedSw -join ', '))
    foreach ($year in $installedSw) {
        if ($year -lt 2016 -or $year -gt 2024) {
            Warn ("SolidWorks " + $year + " 超出本包核查范围 2016–2024；插件仍会尝试加载并记录版本日志。")
        }
    }
} else {
    Warn "未从系统注册表识别到 SolidWorks 版本；安装后请在目标版本中核对插件加载。"
}

# 3) Python（优先使用安装包内置运行时，无需用户安装）
$py = ""
$bundledPy = Join-Path $src "python\python.exe"
if (Test-Path $bundledPy) {
    $py = $bundledPy
    Ok "使用内置 Python 运行时（无需安装）"
} else {
    if (-not [string]::IsNullOrWhiteSpace($Python)) { $py = $Python }
    if ([string]::IsNullOrWhiteSpace($py)) {
        try { $py = (& py -3 -c "import sys;print(sys.executable)" 2>$null | Select-Object -First 1) } catch { }
    }
    if ([string]::IsNullOrWhiteSpace($py)) {
        try { $py = (& python -c "import sys;print(sys.executable)" 2>$null | Select-Object -First 1) } catch { }
    }
    if ([string]::IsNullOrWhiteSpace($py)) {
        $cands = @()
        $cands += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" -ErrorAction SilentlyContinue
        $cands += Get-ChildItem "C:\Python*\python.exe" -ErrorAction SilentlyContinue
        $cands += Get-ChildItem "C:\Program Files\Python*\python.exe" -ErrorAction SilentlyContinue
        $best = $cands | Sort-Object FullName -Descending | Select-Object -First 1
        if ($best) { $py = $best.FullName }
    }
    if ([string]::IsNullOrWhiteSpace($py) -or -not (Test-Path $py)) {
        Fail "未找到 Python：安装包内置运行时缺失，且系统未安装 Python。"
    }
    $verOk = & $py -c "import sys;print(1 if sys.version_info>=(3,8) else 0)"
    if ($verOk -ne "1") { Fail "Python 版本过低，需要 3.8+。" }
}
Ok "Python: $py"

# 核心功能依赖必须在安装前逐项校验；内置运行时没有 pip，不提示用户临时安装。
foreach ($module in @("pymupdf", "PIL", "yaml", "openpyxl", "docx")) {
    & $py -c ("import " + $module) 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail ("安装包不完整：内置 Python 缺少 " + $module) }
    Ok ("内置依赖 " + $module + " 可用")
}

# 4) 源文件校验
foreach ($f in @(
    "SwQcAddin.dll",
    "SolidWorks.Interop.sldworks.dll",
    "SolidWorks.Interop.swconst.dll",
    "SolidWorks.Interop.swpublished.dll",
    "sw-qc-ai\sw_native_qc.py",
    "sw-qc-ai\pdf_connection_test.py",
    "sw-qc-ai\pdf_whole_audit.py",
    "sw-qc-ai\pdf_audit\__init__.py",
    "sw-qc-ai\pdf_audit\providers.py",
    "sw-qc-ai\pdf_audit\render.py",
    "sw-qc-ai\pdf_audit\review.py",
    "sw-qc-ai\pdf_audit\confirmation.py"
)) {
    if (-not (Test-Path (Join-Path $src $f))) { Fail "安装包不完整，缺少: $f" }
}
Ok "安装包文件完整"

if ($DryRun) {
    Info "DryRun 检查通过（未复制、未注册）。"
    exit 0
}

# 5) 复制
try {
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    Copy-Item (Join-Path $src "SwQcAddin.dll") $Dest -Force
    Copy-Item (Join-Path $src "SolidWorks.Interop.*.dll") $Dest -Force
    $engineDest = Join-Path $Dest "sw-qc-ai"
    if (Test-Path $engineDest) { Remove-Item $engineDest -Recurse -Force }
    Copy-Item (Join-Path $src "sw-qc-ai") $Dest -Recurse -Force
    if (-not (Test-Path (Join-Path $engineDest "sw_native_qc.py")) -or
        -not (Test-Path (Join-Path $engineDest "pdf_audit\providers.py"))) {
        Fail "引擎复制失败或 PDF 审核模块不完整"
    }
} catch { Fail "复制文件失败：$($_.Exception.Message)" }
if (Test-Path (Join-Path $src "python\python.exe")) {
    if (Test-Path (Join-Path $Dest "python")) { Remove-Item (Join-Path $Dest "python") -Recurse -Force }
    Copy-Item (Join-Path $src "python") $Dest -Recurse -Force
    $py = Join-Path $Dest "python\python.exe"
}
Set-Content -Path (Join-Path $Dest "version.txt") -Value "SwQcAddin_v272.dll" -Encoding ASCII
Ok "插件与引擎已复制"

# 6) 生成/升级配置。升级时用户配置是主数据，严禁清空 API 密钥或 AI/PDF 设置。
$native = Join-Path $engineDest "sw_native_qc.py"
$configPath = Join-Path $Dest "qc_addin_config.ini"
if (Test-Path $configPath) {
    $existing = @(Get-Content -LiteralPath $configPath -Encoding UTF8)
    function Set-ConfigPath([string[]]$lines, [string]$key, [string]$value) {
        $result = New-Object System.Collections.Generic.List[string]
        $found = $false
        foreach ($line in $lines) {
            if ($line -match ("^\s*" + [regex]::Escape($key) + "\s*=")) {
                if (-not $found) { $result.Add($key + "=" + $value); $found = $true }
            } else { $result.Add($line) }
        }
        if (-not $found) { $result.Insert(0, $key + "=" + $value) }
        return $result.ToArray()
    }
    $existing = Set-ConfigPath $existing "python" $py
    $existing = Set-ConfigPath $existing "native" $native
    [System.IO.File]::WriteAllText($configPath, ($existing -join "`r`n") + "`r`n",
        (New-Object System.Text.UTF8Encoding($false)))
    Ok "已保留现有 API 密钥和全部 AI/PDF 设置，仅更新运行路径"
} else {
    $ini = @"
# 工程图 QC 插件配置
python=$py
parser=
native=$native
rules=gb_baseline
timeout=180
# AI 语义检查（可选，可在插件「AI 设置」里填写）
ai_provider=deepseek
ai_key=
ai_key_deepseek=
ai_base=https://api.deepseek.com/v1
ai_model=deepseek-chat
ai_proxy=
standard=GB
"@
    [System.IO.File]::WriteAllText($configPath, $ini, (New-Object System.Text.UTF8Encoding($false)))
    Ok "首次安装配置已生成"
}

# 7) 注册
if (-not $SkipRegister) {
    $regasm = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\regasm.exe"
    if (-not (Test-Path $regasm)) { Fail "找不到 regasm.exe，请确认已安装 .NET Framework 4.x。" }
    Info "正在注册插件..."
    $out = & $regasm /codebase (Join-Path $Dest "SwQcAddin.dll") 2>&1
    if ($LASTEXITCODE -ne 0) { Fail "插件注册失败：$out" }
    Ok "插件注册成功"
} else {
    Warn "已跳过注册（SkipRegister）"
}

# 8) 生成卸载脚本
$uninstall = @"
@echo off
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)
tasklist /FI "IMAGENAME eq SLDWORKS.exe" | find /I "SLDWORKS.exe" >nul
if not errorlevel 1 (
    echo 请先完全关闭 SolidWorks 再卸载。
    pause
    exit /b 1
)
"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\regasm.exe" /u "$Dest\SwQcAddin.dll" >nul 2>&1
rmdir /S /Q "$Dest"
echo 已卸载工程图QC插件。
pause
"@
Set-Content -Path (Join-Path $Dest "卸载_工程图QC.bat") -Value $uninstall -Encoding UTF8
Ok "卸载脚本已生成"

Write-Host ""
Write-Host "============================================================"
Write-Host "  安装完成" -ForegroundColor Green
Write-Host "============================================================"
Write-Host "  1. 打开 SolidWorks"
Write-Host "  2. 菜单：工具 -> 工程图QC"
Write-Host "  3. 首次使用可在插件「AI 设置」填写 AI Key（可选）"
Write-Host ""
Write-Host "  安装目录：$Dest"
Write-Host "  卸载方式：运行 $Dest\卸载_工程图QC.bat"
Write-Host ""
Read-Host "按回车退出"
