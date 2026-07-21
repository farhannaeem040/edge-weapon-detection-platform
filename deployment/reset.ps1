<#
.SYNOPSIS
    DESTRUCTIVE. Stops the stack and permanently deletes the SQL Server database volume.

.DESCRIPTION
    Runs `docker compose down -v`, which removes the containers, the network, AND the
    `sqlserver-data` volume. Every Branch, Camera, Device, Activation Key, and Admin session is
    permanently destroyed. The next start.ps1 begins from an empty, freshly migrated database.

    Requires typing an explicit confirmation. The .env file is left alone unless -RemoveEnv is
    passed, because deleting it would also discard the Admin credentials you sign in with.

.PARAMETER RemoveEnv
    Also delete the .env file, so the next start.ps1 generates brand-new secrets.

.PARAMETER Force
    Skip the interactive confirmation. Intended for scripted use; be certain.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deployment/reset.ps1
#>

[CmdletBinding()]
param(
    [switch] $RemoveEnv,
    [switch] $Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repositoryRoot

try {
    Write-Host ''
    Write-Host 'WARNING - this is destructive.' -ForegroundColor Red
    Write-Host ''
    Write-Host 'This will permanently delete the SQL Server data volume (sqlserver-data),'
    Write-Host 'including every Branch, Camera, Device, and Activation Key.'
    Write-Host 'This cannot be undone.'

    if ($RemoveEnv) {
        Write-Host ''
        Write-Host 'The .env file will also be deleted, so new secrets will be generated on the' -ForegroundColor Yellow
        Write-Host 'next start (including a new Admin password).' -ForegroundColor Yellow
    }

    Write-Host ''
    Write-Host 'To stop WITHOUT deleting data, cancel and run stop.ps1 instead.'
    Write-Host ''

    if (-not $Force) {
        # A deliberately typed word, not a y/n keypress: this destroys data.
        $confirmation = Read-Host "Type 'DELETE' to confirm, or anything else to cancel"
        if ($confirmation -cne 'DELETE') {
            Write-Host ''
            Write-Host 'Cancelled. Nothing was deleted.' -ForegroundColor Green
            return
        }
    }

    Write-Host ''
    Write-Host 'Removing containers, network, and the database volume...'

    docker compose down -v
    if ($LASTEXITCODE -ne 0) {
        throw 'docker compose down -v failed. Inspect the output above.'
    }

    if ($RemoveEnv) {
        $envPath = Join-Path $repositoryRoot '.env'
        if (Test-Path $envPath) {
            Remove-Item $envPath -Force
            Write-Host 'Deleted .env.'
        }
    }

    Write-Host ''
    Write-Host 'Reset complete. The next start begins from an empty database.' -ForegroundColor Green
    Write-Host 'Start again with: powershell -ExecutionPolicy Bypass -File deployment/start.ps1'
}
finally {
    Pop-Location
}
