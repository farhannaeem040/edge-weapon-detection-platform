<#
.SYNOPSIS
    Follows logs from the central platform stack.

.PARAMETER Service
    Optional service to follow: sqlserver, migrations, backend, or frontend.
    Omit to follow all of them.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deployment/logs.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deployment/logs.ps1 -Service backend
#>

[CmdletBinding()]
param(
    [ValidateSet('sqlserver', 'migrations', 'backend', 'frontend')]
    [string] $Service
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repositoryRoot

try {
    if ($Service) {
        Write-Host "Following logs for '$Service' (Ctrl+C to stop)..."
        docker compose logs -f $Service
    }
    else {
        Write-Host 'Following logs for all services (Ctrl+C to stop)...'
        docker compose logs -f
    }
}
finally {
    Pop-Location
}
