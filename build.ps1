param(
    [string]$InteropDir = "",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceDir = Join-Path $Root "src\SwQcAddin"
$EngineDir = Join-Path $Root "src\sw-qc-ai"

if (-not $OutDir) {
    $OutDir = Join-Path $Root "build"
}
if (-not $InteropDir) {
    $InteropDir = Join-Path $Root "vendor\solidworks"
}

$CompilerCandidates = @(
    "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$Compiler = $CompilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Compiler) {
    throw ".NET Framework 4.x csc.exe was not found."
}

$InteropNames = @(
    "SolidWorks.Interop.sldworks.dll",
    "SolidWorks.Interop.swconst.dll",
    "SolidWorks.Interop.swpublished.dll"
)
foreach ($Name in $InteropNames) {
    $Path = Join-Path $InteropDir $Name
    if (-not (Test-Path $Path)) {
        throw "Missing $Path. Copy the Interop files from a licensed SOLIDWORKS api\redist directory."
    }
}

$Sources = @(
    "QcModel.cs",
    "QcDocument.cs",
    "QcDocumentStore.cs",
    "QcRunner.cs",
    "QcDetailView.cs",
    "QcResultForm.cs",
    "QcAiSettingsForm.cs",
    "SwDocReader.cs",
    "SwQcAddin.cs",
    "QcGtol.cs",
    "QcCalc.cs",
    "QcGtolLinkForm.cs",
    "QcDatumForm.cs",
    "QcManualRowForm.cs",
    "PdfAuditModels.cs",
    "SwPdfExporter.cs",
    "PdfAuditRunner.cs",
    "PdfAuditForm.cs",
    "PdfAuditSettingsForm.cs",
    "PdfAuditConfirmForm.cs"
) | ForEach-Object {
    $Path = Join-Path $SourceDir $_
    if (-not (Test-Path $Path)) { throw "Missing source file: $Path" }
    $Path
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$OutputDll = Join-Path $OutDir "SwQcAddin.dll"

$FrameworkReferences = @(
    "/r:System.dll",
    "/r:System.Core.dll",
    "/r:Microsoft.CSharp.dll",
    "/r:System.Drawing.dll",
    "/r:System.Windows.Forms.dll",
    "/r:System.Web.Extensions.dll"
)
$SolidWorksReferences = $InteropNames | ForEach-Object {
    "/r:" + (Join-Path $InteropDir $_)
}

$Arguments = @(
    "/nologo",
    "/target:library",
    "/platform:x64",
    "/optimize+",
    "/out:$OutputDll"
) + $FrameworkReferences + $SolidWorksReferences + $Sources

Write-Host "Compiling the C# add-in..."
& $Compiler $Arguments
if ($LASTEXITCODE -ne 0) {
    throw "The C# add-in build failed."
}

$EngineOutput = Join-Path $OutDir "sw-qc-ai"
if (Test-Path $EngineOutput) {
    Remove-Item -LiteralPath $EngineOutput -Recurse -Force
}
Copy-Item -LiteralPath $EngineDir -Destination $EngineOutput -Recurse
Copy-Item -LiteralPath (Join-Path $SourceDir "icons_large.bmp") -Destination $OutDir
Copy-Item -LiteralPath (Join-Path $SourceDir "icons_small.bmp") -Destination $OutDir

Write-Host "Build complete: $OutputDll" -ForegroundColor Green
Write-Host "This is build output, not a registered one-click installer." -ForegroundColor Yellow

