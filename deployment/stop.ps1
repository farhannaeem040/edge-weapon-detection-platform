<#
.SYNOPSIS
    Stops the central platform stack, keeping all data.

.DESCRIPTION
    Runs `docker compose down`, which removes the containers and the network but leaves the
    `sqlserver-data` volume intact. Branches, devices, and Activation Keys all survive; the next
    `start.ps1` brings the stack back with the same data.

    To delete the database as well, use reset.ps1 instead.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deployment/stop.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repositoryRoot

try {
    Write-Host 'Stopping the central platform (database volume is preserved)...'
    Write-Host ''

    # No -v: the named volume must survive.
    docker compose down
    if ($LASTEXITCODE -ne 0) {
        throw 'docker compose down failed. Inspect the output above.'
    }

    Write-Host ''
    Write-Host 'Stopped. Your data is preserved in the sqlserver-data volume.' -ForegroundColor Green
    Write-Host 'Start again with: powershell -ExecutionPolicy Bypass -File deployment/start.ps1'
}
finally {
    Pop-Location
}
