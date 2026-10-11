param(
    [ValidateRange(1, 65535)][int]$Port = 8766,
    [string]$ProjectRoot = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
$resolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
$dashboardScript = Join-Path $PSScriptRoot 'dashboard_revision.py'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    & $pythonCommand.Source $dashboardScript --root $resolvedProject --port $Port
    exit $LASTEXITCODE
}
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & $pyLauncher.Source -3 $dashboardScript --root $resolvedProject --port $Port
    exit $LASTEXITCODE
}
throw 'No Python interpreter found. Install Python or run dashboard_revision.py with your existing interpreter. No dependencies are installed by this script.'
