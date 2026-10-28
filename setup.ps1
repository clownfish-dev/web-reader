# Install web-reader (Scrapling fork) on this PC from this folder (fork\, register.mjs, ...).
# Run from the folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [-Headless] [-McpJson <path>] [-OldWebReader <0.9.4 folder>] [-ProfileFrom <folder>] [-NoRegister]
# Steps: venv -> Scrapling 0.5 deps + this fork (editable) -> browser into .\browsers (+ AppContainer read)
#        -> profile from an earlier install (-ProfileFrom) -> logins from web-reader 0.9.4 (-OldWebReader)
#        -> register "web-reader" for Claude Code -> self test. Existing .venv/browsers are reused.
param([switch]$Headless, [string]$McpJson = "", [string]$OldWebReader = "", [string]$ProfileFrom = "", [switch]$NoRegister)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here
function Step($m) { Write-Output "== $m" }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "python not found (3.10+ needed)" }
if (-not (Test-Path "$Here\.venv\Scripts\python.exe")) { Step "venv"; & $py -m venv "$Here\.venv" }
$vpy = "$Here\.venv\Scripts\python.exe"

Step "packages"
& $vpy -m pip install -q --disable-pip-version-check "scrapling[ai]==0.5" pypdf python-docx
& $vpy -m pip uninstall -q -y scrapling
# A leftover site-packages\scrapling folder (marker file only) would shadow the fork as a namespace package.
$sp = & $vpy -I -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
if (Test-Path "$sp\scrapling") {
  Copy-Item "$sp\scrapling\.scrapling_dependencies_installed" "$Here\fork\scrapling\" -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force "$sp\scrapling"
}
& $vpy -m pip install -q --disable-pip-version-check --no-deps -e "$Here\fork"
$where = & $vpy -I -c "import scrapling; print(scrapling.__file__)"
if ($where -notlike "$Here\fork\*") { throw "scrapling imports from $where, not the fork" }

Step "browser"
$env:PLAYWRIGHT_BROWSERS_PATH = "$Here\browsers"
if (-not (Get-ChildItem "$Here\browsers" -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue)) {
  & "$Here\.venv\Scripts\patchright.exe" install chromium
}
# Claude desktop runs MCP servers in an AppContainer: without this the browser never starts (spawn UNKNOWN).
icacls "$Here\browsers" /grant "*S-1-15-2-1:(OI)(CI)(RX)" "*S-1-15-2-2:(OI)(CI)(RX)" /T /Q | Out-Null

# This install's server and browser must be stopped while the profile is filled.
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'scrapling\.webreader\.service' -and $_.ExecutablePath -like "$Here\*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 2

if ($ProfileFrom -and (Test-Path "$ProfileFrom\profile") -and -not (Test-Path "$Here\profile")) {
  Step "profile from $ProfileFrom"
  robocopy "$ProfileFrom\profile" "$Here\profile" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
  foreach ($f in 'site-apis.json','site-notes.json','site-status.json','watches.json','sites-http-blocked.json') {
    if (Test-Path "$ProfileFrom\data\$f") { New-Item -ItemType Directory -Force "$Here\data" | Out-Null; Copy-Item "$ProfileFrom\data\$f" "$Here\data\" }
  }
}

if ($OldWebReader -and (Test-Path "$OldWebReader\server.mjs")) {
  Step "logins from web-reader 0.9.4"
  New-Item -ItemType Directory -Force "$Here\data" | Out-Null
  $env:OLD_WEB_READER = $OldWebReader
  node "$Here\export-login.mjs"   # attaches to its running browsers
  $files = @()
  if (Test-Path "$Here\data\login-state.json") { $files += "$Here\data\login-state.json" }
  else {
    node "$OldWebReader\tools\export-logins.mjs"   # browsers not running: open its profiles directly
    $files += Get-ChildItem "$OldWebReader\data\transfer\*.json" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
  }
  if ($files) {
    if ($Headless) { $env:WEB_READER_HEADLESS = "1" }
    Set-Location $env:TEMP
    & $vpy -I -m scrapling.webreader.migrate @files
    Set-Location $Here
  }
  Remove-Item "$Here\data\login-state.json" -ErrorAction SilentlyContinue   # the profile holds them now
  Remove-Item "$OldWebReader\data\transfer\*.json" -ErrorAction SilentlyContinue
}

if (-not $NoRegister) {
  Step "register"
  $a = @("$Here\register.mjs")
  if ($Headless) { $a += "--headless" }
  if ($McpJson) { $a += @("--mcp-json", $McpJson) }
  node @a
}

Step "self test"
if ($Headless) { $env:WEB_READER_HEADLESS = "1" }
Set-Location $env:TEMP
& $vpy -I -m scrapling.webreader.selftest
$code = $LASTEXITCODE
Set-Location $Here
if ($code -ne 0) { throw "self test failed" }
Write-Output "OK"
