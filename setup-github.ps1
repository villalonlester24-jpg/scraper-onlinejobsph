# Push this folder to a GitHub repo you already created.
#
#   .\setup-github.ps1 -RepoUrl "https://github.com/YOURNAME/vajobscraper.git"
#
# Create the repo first at https://github.com/new, PRIVATE, with no README or
# .gitignore (this folder already has one). See CLOUD.md Part 2.

param(
    [Parameter(Mandatory = $true)]
    [string]$RepoUrl
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "git is not installed." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Run this, then CLOSE this window, open a new one, and try again:"
    Write-Host "    winget install --id Git.Git -e"
    exit 1
}

if (-not (Test-Path '.git')) {
    Write-Host "initialising repository..."
    git init -b main
    if ($LASTEXITCODE -ne 0) { git init; git symbolic-ref HEAD refs/heads/main }
}

# Stage everything, then refuse to go further if anything personal got caught.
git add -A

$staged = @(git diff --cached --name-only)
$personal = @($staged | Where-Object { $_ -match '\.(pdf|docx|xls)$' })
if ($personal.Count -gt 0) {
    Write-Host ""
    Write-Host "REFUSING TO COMMIT - these look personal:" -ForegroundColor Red
    $personal | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Add them to .gitignore, then run:  git reset"
    exit 1
}

Write-Host ""
Write-Host "About to commit $($staged.Count) file(s):" -ForegroundColor Cyan
$staged | ForEach-Object { Write-Host "    $_" }
Write-Host ""

if ($staged.Count -eq 0 -and (Test-Path '.git')) {
    Write-Host "nothing new to commit"
} else {
    git commit -m "OnlineJobs.ph scraper + GitHub Actions automation"
}

$remotes = @(git remote)
if ($remotes -contains 'origin') {
    git remote set-url origin $RepoUrl
} else {
    git remote add origin $RepoUrl
}

Write-Host ""
Write-Host "pushing to $RepoUrl ..." -ForegroundColor Cyan
git push -u origin main

Write-Host ""
Write-Host "Done. Next: add the secrets and run the workflow once." -ForegroundColor Green
Write-Host "See CLOUD.md Part 3."
