<#
.SYNOPSIS
    Deploy the Weapon Detection Jetson Agent from Windows to the Jetson (IP-02 T-41, section 13).

.DESCRIPTION
    Packages only the Agent source and the Jetson deployment assets (excluding .git, .venv,
    databases, logs, caches, and .env), copies them to a unique temporary directory on the Jetson
    over SSH (key auth), and runs the installer through interactive sudo. The temporary remote
    directory is removed afterwards.

    This script never copies an Activation Key (it lives only on the Jetson, out-of-band) and never
    handles, embeds, or prints any secret or credential. Privileged install steps run via
    'sudo' on the Jetson, which prompts for the sudo password interactively in your terminal.

.PARAMETER JetsonHost
    Jetson hostname or Tailscale IP. Default: 100.98.226.80

.PARAMETER User
    SSH user on the Jetson. Default: farhan

.PARAMETER IdentityFile
    SSH private key. Default: <user profile>\.ssh\id_ed25519

.PARAMETER StageOnly
    Copy the files but do not run the installer (inspect on the Jetson first).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deployment/jetson/deploy.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deployment/jetson/deploy.ps1 -JetsonHost 100.98.226.80 -User farhan
#>

[CmdletBinding()]
param(
    [string]$JetsonHost = "100.98.226.80",
    [string]$User = "farhan",
    [string]$IdentityFile = (Join-Path $env:USERPROFILE ".ssh\id_ed25519"),
    [switch]$StageOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$m) Write-Host "[deploy] $m" }
function Die { param([string]$m) Write-Error "[deploy] $m"; exit 1 }

# --- Resolve the repository root from this script's location (works from any CWD) ----------------
$scriptDir = $PSScriptRoot                                   # deployment/jetson
$repoRoot  = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
$agentDir  = Join-Path $repoRoot "agent"
if (-not (Test-Path (Join-Path $agentDir "pyproject.toml"))) {
    Die "Agent source not found at $agentDir (expected pyproject.toml). Run from the repository."
}

# --- Verify required commands --------------------------------------------------------------------
foreach ($cmd in @("ssh", "scp", "tar")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Die "'$cmd' not found on PATH. OpenSSH client and bsdtar ship with Windows 10/11 - enable the OpenSSH Client optional feature."
    }
}
if (-not (Test-Path $IdentityFile)) { Die "SSH identity file not found: $IdentityFile" }

$target = "$User@$JetsonHost"
$sshOpts = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-i", $IdentityFile)

# --- Verify SSH connectivity ---------------------------------------------------------------------
Write-Step "verifying SSH connectivity to $target"
$who = & ssh @sshOpts $target "whoami" 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($who)) {
    Die "cannot reach $target over SSH with key auth. Ensure the key is authorized (ssh-copy-id)."
}
Write-Step "connected as '$($who.Trim())' on $JetsonHost"

# --- Package the payload locally (exclude junk and anything secret) ------------------------------
$stamp     = Get-Date -Format "yyyyMMddHHmmss"
$archName  = "wda-agent-$stamp.tar.gz"
$localArch = Join-Path ([System.IO.Path]::GetTempPath()) $archName
$excludes  = @(
    "--exclude=.git", "--exclude=.venv", "--exclude=__pycache__", "--exclude=*.pyc",
    "--exclude=.pytest_cache", "--exclude=.mypy_cache", "--exclude=.ruff_cache",
    "--exclude=*.db", "--exclude=*.log", "--exclude=.env", "--exclude=node_modules"
)
Write-Step "packaging agent/ and deployment/jetson/ (excluding .git/.venv/db/logs/caches/.env)"
# tar resolves paths relative to -C; agent + deployment/jetson only - nothing else in the repo.
& tar -czf $localArch -C $repoRoot @excludes "agent" "deployment/jetson"
if ($LASTEXITCODE -ne 0) { Die "tar packaging failed" }

try {
    # --- Unique temporary remote directory -------------------------------------------------------
    $remoteDir = (& ssh @sshOpts $target "mktemp -d /tmp/wda-deploy-XXXXXX").Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($remoteDir)) { Die "failed to create a remote temp dir" }
    Write-Step "staging to ${target}:${remoteDir}"

    try {
        # --- Copy + extract ----------------------------------------------------------------------
        & scp @sshOpts $localArch "${target}:${remoteDir}/${archName}"
        if ($LASTEXITCODE -ne 0) { Die "scp of the payload failed" }
        & ssh @sshOpts $target "tar -xzf '${remoteDir}/${archName}' -C '${remoteDir}' && rm -f '${remoteDir}/${archName}'"
        if ($LASTEXITCODE -ne 0) { Die "remote extraction failed" }

        if ($StageOnly) {
            Write-Step "staged only (StageOnly). To install on the Jetson, run:"
            Write-Host  "    ssh -t $target 'cd $remoteDir/deployment/jetson && sudo bash install.sh'"
            Write-Step "remember to remove $remoteDir afterwards."
            return
        }

        # --- Run the installer through interactive sudo (prompts for the sudo password) ----------
        Write-Step "running the installer on the Jetson (sudo will prompt for your password)"
        # -t allocates a TTY so the interactive sudo password prompt works.
        & ssh -t @sshOpts $target "cd '$remoteDir/deployment/jetson' && sudo bash install.sh"
        if ($LASTEXITCODE -ne 0) { Die "remote installation failed (exit $LASTEXITCODE). See the output above." }
        Write-Step "installation finished. Next: provision the Activation Key on the Jetson, then start the service."
        Write-Host  "    ssh -t $target 'sudo /opt/weapon-detection/agent/deployment/jetson/set-activation-key.sh'"
        Write-Host  "    ssh -t $target 'sudo systemctl start weapon-detection-agent'"
        Write-Host  "    ssh -t $target 'sudo /opt/weapon-detection/agent/deployment/jetson/verify.sh'"
    }
    finally {
        # --- Always clean up the remote temp directory -------------------------------------------
        Write-Step "cleaning up $remoteDir"
        & ssh @sshOpts $target "rm -rf '$remoteDir'" 2>$null | Out-Null
    }
}
finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $localArch
}
