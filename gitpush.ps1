# Auto-navegar a la raíz de PostulacionBotVPS
Set-Location $PSScriptRoot

# Script de Empuje Tanque v1.1 - PostulacionBot
Write-Host "Iniciando proceso de envio para PostulacionBot..." -ForegroundColor Cyan

# 1. Preparar commits
git add .
$msg = "Katty Update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
git commit -m "$msg" 2>$null

# 2. Sincronizacion Segura
Write-Host "Sincronizando repositorio local con GitHub..." -ForegroundColor Gray
git pull origin main --rebase

if ($LASTEXITCODE -ne 0) {
    Write-Host "Conflicto detectado durante la sincronizacion. Reparando..." -ForegroundColor Yellow
    git rebase --abort 2>$null
    git pull origin main --no-rebase -X ours
    git add .
    git commit -m "$msg (Manual Conflict Resolve)" 2>$null
}

# 3. Empuje final
git push origin main
Write-Host "Proyecto en GitHub. El Deploy automatico comenzara ahora." -ForegroundColor Green
