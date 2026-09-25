# run_worker.ps1 - Initialize and launch the RPC worker on Windows
# Usage: .\run_worker.ps1 -IP "192.168.10.2" -Port 50052
# Prefer: kllm join --key <join-key> (no GGUF on workers; tensors arrive via RPC).

param(
    [string]$IP = "0.0.0.0",
    [int]$Port = 50052,
    [int]$Context = 4096,
    [int]$Ngpu = 0,
    [int]$Threads = 0,
    [switch]$Help
)

# ── Help ──────────────────────────────────────────────────────────────────────
if ($Help) {
    Write-Host "Usage: .\run_worker.ps1 [-IP <ip>] [-Port <port>] [-Context <tokens>]"
    Write-Host ""
    Write-Host "  Prefer: kllm join --key <join-key> (no GGUF on workers; tensors arrive via RPC)."
    Write-Host "  -IP       Static IP for the dedicated network link (default: 0.0.0.0)"
    Write-Host "  -Port     RPC port to listen on (default: 50052)"
    Write-Host "  -Context  Context size in tokens (default: 4096)"
    Write-Host "  -Ngpu     Number of GPU layers (0 = auto-detect)"
    Write-Host "  -Threads  CPU threads for non-GPU layers (0 = auto-detect)"
    exit 0
}

Write-Host "=== Kluster LLM: Windows Worker ===" -ForegroundColor Cyan
Write-Host ""

# ── Detect GPU layers ────────────────────────────────────────────────────────
if ($Ngpu -eq 0) {
    $gpuInfo = Get-WmiObject -Class Win32_PnPEntity | Where-Object { $_.Name -match "AMD|RADEON|GPU" } | Select-Object -First 1
    if ($gpuInfo) {
        $Ngpu = 999  # Offload all layers to GPU by default
        Write-Host "[INFO] Auto-detected AMD GPU, setting -ngpu 999 (offload all layers)" -ForegroundColor Green
    } else {
        $Ngpu = 0
        Write-Host "[WARN] No GPU detected, using CPU fallback (-ngpu 0)" -ForegroundColor Yellow
    }
}

# ── Detect CPU threads ───────────────────────────────────────────────────────
if ($Threads -eq 0) {
    $Threads = (Get-WmiObject Win32_Processor).NumberOfLogicalProcessors
    Write-Host "[INFO] CPU threads: $Threads" -ForegroundColor Green
}

# ── Open firewall port ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "[INFO] Configuring Windows Firewall..." -ForegroundColor Yellow

$firewallRuleName = "kllm-RPC"
$existingRule = Get-NetFirewallRule -Name $firewallRuleName -ErrorAction SilentlyContinue

if ($existingRule) {
    Write-Host "  Firewall rule '$firewallRuleName' already exists." -ForegroundColor Green
} else {
    try {
        New-NetFirewallRule -Name $firewallRuleName `
            -DisplayName "Kluster LLM RPC Worker" `
            -Description "Kluster LLM RPC worker" `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort $Port `
            -Profile Any `
            -Enabled True `
            -ErrorAction Stop
        Write-Host "  Opened TCP port $Port in Windows Firewall." -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Could not create firewall rule (needs Administrator)." -ForegroundColor Yellow
        Write-Host "  Open elevated PowerShell and run:" -ForegroundColor Yellow
        Write-Host "  New-NetFirewallRule -Name '$firewallRuleName' -DisplayName 'Kluster LLM RPC Worker' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Any" -ForegroundColor Cyan
    }
}

# ── Bind to static IP ────────────────────────────────────────────────────────
if ($IP -ne "0.0.0.0") {
    Write-Host ""
    Write-Host "[INFO] Binding worker to $IP:$Port" -ForegroundColor Yellow
}

# ── Launch rpc-server.exe ────────────────────────────────────────────────────
$rpcServer = "$PSScriptRoot\rpc-server.exe"

if (-not (Test-Path $rpcServer)) {
    Write-Host "ERROR: rpc-server.exe not found at $rpcServer" -ForegroundColor Red
    Write-Host "Run .\setup_windows.ps1 first to download it." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[INFO] Launching RPC worker..." -ForegroundColor Green
Write-Host "  Binding to: $IP:$Port"
Write-Host "  GPU layers: $Ngpu"
Write-Host "  CPU threads: $Threads"
Write-Host "  Context: $Context tokens"
Write-Host ""

# No --model: GGUF stays on master; rpc-server receives tensors over RPC (-c cache).
$args = @("--host", $IP, "--port", "$Port", "-c", "--ngpu", "$Ngpu", "--threads", "$Threads", "--ctx-size", "$Context")

# Launch with environment variables for Vulkan/ROCm
$env:VK_ICD_FILE_NAMES = "C:\Vulkan\SDK\icd.ics"
$env:HIP_PLATFORM = "amd"

$dllDir = Join-Path $PSScriptRoot "src"
if (Test-Path $dllDir) {
    $env:PATH = "$dllDir;$env:PATH"
}

& "$rpcServer" $args
