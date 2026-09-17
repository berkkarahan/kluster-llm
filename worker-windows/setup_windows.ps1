# setup_windows.ps1 - Legacy helper; prefer: python scripts/install.py
# Keeps Vulkan + rpc-server fetch for operators who only want the worker bits.

Write-Host "=== Kluster LLM: Windows Worker Setup ===" -ForegroundColor Cyan
Write-Host "Prefer full install: python scripts\install.py" -ForegroundColor DarkCyan
Write-Host ""

# ── 1. Check GPU ─────────────────────────────────────────────────────────────
Write-Host "[1/3] Checking GPU..." -ForegroundColor Yellow

$gpu = Get-CimInstance -ClassName Win32_VideoController | Select-Object -First 3
foreach ($g in $gpu) {
    if ($g.Name) { Write-Host "  GPU: $($g.Name)" -ForegroundColor Green }
}

# ── 2. Vulkan SDK (optional for Vulkan rpc-server) ───────────────────────────
Write-Host ""
Write-Host "[2/3] Checking Vulkan SDK..." -ForegroundColor Yellow

$vulkanPath = "C:\Vulkan\SDK"
if (Test-Path $vulkanPath) {
    Write-Host "  Vulkan SDK already present at $vulkanPath" -ForegroundColor Green
} else {
    Write-Host "  Vulkan SDK not found (optional). Install from https://vulkan.lunarg.com/ if using Vulkan." -ForegroundColor Yellow
}

# ── 3. Fetch rpc-server via Python installer (correct assets) ────────────────
Write-Host ""
Write-Host "[3/3] Installing llama-server + rpc-server via scripts/install.py..." -ForegroundColor Yellow

$repoRoot = Split-Path $PSScriptRoot -Parent
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "  ERROR: python not found. Install Python 3.10+ then run: python scripts\install.py" -ForegroundColor Red
    exit 1
}

& $py.Source "$repoRoot\scripts\install.py" --skip-rust --skip-ui --skip-wireguard --prefer-prebuilt --backend vulkan
if ($LASTEXITCODE -ne 0) {
    Write-Host "  install.py failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host "Next: python scripts\install.py   # full stack incl. WireGuard + kllm CLI"
Write-Host "  or: kllm join --key <key>"
