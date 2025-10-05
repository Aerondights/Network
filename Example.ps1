#Requires -Version 7.0
<#
.SYNOPSIS
    Exemples d'utilisation du script Manage-VCenterVMs.ps1
    
.DESCRIPTION
    Collection de scripts exemples pour différents cas d'usage
#>

# ============================================================================
# EXEMPLE 1: Utilisation basique
# ============================================================================

function Example-Basic {
    Write-Host "`n=== EXEMPLE 1: Utilisation basique ===" -ForegroundColor Cyan
    
    $vcenter = "vcenter.example.com"
    $csvPath = "C:\scripts\vms.csv"
    
    # Demander les credentials
    $cred = Get-Credential -Message "Entrez vos identifiants vCenter"
    
    # Exécution simple
    & ".\Manage-VCenterVMs.ps1" `
        -VCenterServer $vcenter `
        -Credential $cred `
        -CsvPath $csvPath
}

# ============================================================================
# EXEMPLE 2: Performance maximale pour 2000 VMs
# ============================================================================

function Example-HighPerformance {
    Write-Host "`n=== EXEMPLE 2: Performance maximale - 2000 VMs ===" -ForegroundColor Cyan
    
    $vcenter = "vcenter.example.com"
    $csvPath = "C:\scripts\2000_vms.csv"
    $logDir = "C:\logs\vm-operations"
    
    # Créer le répertoire de logs
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $cred = Get-Credential
    
    # Configuration haute performance
    & ".\Manage-VCenterVMs.ps1" `
        -VCenterServer $vcenter `
        -Credential $cred `
        -CsvPath $csvPath `
        -MaxThreads 100 `
        -BatchSize 100 `
        -AllowSelfSigned `
        -ReportPath "$logDir\report_$timestamp.txt" `
        -LogPath "$logDir\operations_$timestamp.log" `
        -LogLevel INFO
    
    Write-Host "`nRapport sauvegardé dans: $logDir\report_$timestamp.txt" -ForegroundColor Green
}

# ============================================================================
# EXEMPLE 3: Génération dynamique du CSV depuis vCenter
# ============================================================================

function Example-DynamicCSV {
    Write-Host "`n=== EXEMPLE 3: Génération dynamique du CSV ===" -ForegroundColor Cyan
    
    $vcenter = "vcenter.example.com"
    $outputCsv = "C:\scripts\generated_vms.csv"
    $cred = Get-Credential
    
    # Connexion au vCenter
    Write-Host "Connexion au vCenter..." -ForegroundColor Yellow
    Connect-VIServer -Server $vcenter -Credential $cred
    
    # Récupérer toutes les VMs de dev à éteindre
    Write-Host "Récupération des VMs dev-*..." -ForegroundColor Yellow
    $vms = Get-VM -Name "dev-*" | Where-Object { $_.PowerState -eq "PoweredOn" }
    
    Write-Host "Trouvé $($vms.Count) VMs à traiter" -ForegroundColor Green
    
    # Créer le CSV
    $operations = $vms | ForEach-Object {
        [PSCustomObject]@{
            vm_name = $_.Name
            action = "SHUTDOWN"  # Arrêt gracieux
        }
    }
    
    $operations | Export-Csv -Path $outputCsv -NoTypeInformation -Encoding UTF8
    Write-Host "CSV généré: $outputCsv" -ForegroundColor Green
    
    Disconnect-VIServer -Confirm:$false
    
    # Exécuter l'extinction
    Write-Host "`nExécution de l'extinction..." -ForegroundColor Yellow
    & ".\Manage-VCenterVMs.ps1" `
        -VCenterServer $vcenter `
        -Credential $cred `
        -CsvPath $outputCsv `
        -MaxThreads 80 `
        -AllowSelfSigned
}

# ============================================================================
# EXEMPLE 4: Démarrage séquencé par tiers (DB -> APP -> WEB)
# ============================================================================

function Example-TieredStartup {
    Write-Host "`n=== EXEMPLE 4: Démarrage séquencé ===" -ForegroundColor Cyan
    
    $vcenter = "vcenter.example.com"
    $cred = Get-Credential
    
    # Tier 1: Bases de données et infrastructure
    Write-Host "`n[1/3] Démarrage des bases de données..." -ForegroundColor Yellow
    & ".\Manage-VCenterVMs.ps1" `
        -VCenterServer $vcenter `
        -Credential $cred `
        -CsvPath "C:\scripts\tier1_database.csv" `
        -MaxThreads 30
    
    Write-Host "Attente de 90 secondes pour l'initialisation des DB..." -ForegroundColor Cyan
    Start-Sleep -Seconds 90
    
    # Tier 2: Serveurs d'application
    Write-Host "`n[2/3] Démarrage des serveurs d'application..." -ForegroundColor Yellow
    & ".\Manage-VCenterVMs.ps1" `
        -VCenterServer $vcenter `
        -Credential $cred `
        -CsvPath "C:\scripts\tier2_application.csv" `
        -MaxThreads 50
    
    Write-Host "Attente de 60 secondes pour l'initialisation des apps..." -ForegroundColor Cyan
    Start-Sleep -Seconds 60
    
    # Tier 3: Frontaux web
    Write-Host "`n[3/3] Démarrage des frontaux web..." -ForegroundColor Yellow
    & ".\Manage-VCenterVMs.ps1" `
        -VCenterServer $vcenter `
        -Credential $cred `
        -CsvPath "C:\scripts\tier3_web.csv" `
        -MaxThreads 100
    
    Write-Host "`n✓ Démarrage séquencé terminé!" -ForegroundColor Green
}

# ============================================================================
# EXEMPLE 5: Maintenance avec snapshots automatiques
# ============================================================================

function Example-MaintenanceWithSnapshots {
    Write-Host "`n=== EXEMPLE 5: Maintenance avec snapshots ===" -ForegroundColor Cyan
    
    $vcenter = "vcenter.example.com"
    $csvPath = "C:\scripts\maintenance_vms.csv"
    $cred = Get-Credential
    $snapshotName = "Maintenance_$(Get-Date -Format 'yyyyMMdd_HHmm')"
    
    # Lire les VMs à traiter
    $vmsToProcess = Import-Csv -Path $csvPath
    
    # Connexion au vCenter
    Write-Host "Connexion au vCenter..." -ForegroundColor Yellow
    Connect-VIServer -Server $vcenter -Credential $cred
    
    # Créer les snapshots
    Write-Host "`nCréation des snapshots de sauvegarde..." -ForegroundColor Yellow
    $snapshotResults = @()
    
    foreach ($vmInfo in $vmsToProcess) {
        try {
            $vm = Get-VM -Name $vmInfo.vm_name -ErrorAction Stop
            New-Snapshot -VM $vm -Name $snapshotName -Description "Avant maintenance planifiée" -Confirm:$false | Out-Null
            Write-Host "  ✓ Snapshot créé pour $($vmInfo.vm_name)" -ForegroundColor Green
            $snapshotResults += [PSCustomObject]@{
                VM = $vmInfo.vm_name
                Success = $true
            }
        }
        catch {
            Write-Host "  ✗ Erreur snapshot pour $($vmInfo.vm_name): $_" -ForegroundColor Red
            $snapshotResults += [PSCustomObject]@{
                VM = $vmInfo.vm_name
                Success = $false
            }
        }
    }
    
    Disconnect-VIServer -Confirm:$false
    
    # Vérifier si tous les snapshots ont réussi
    $failedSnapshots = $snapshotResults | Where-Object { -not $_.Success }
    
    if ($failedSnapshots.Count -gt 0) {
        Write-Host "`n⚠️  $($failedSnapshots.Count) snapshot(s) ont échoué" -ForegroundColor Red
        $response = Read-Host "Voulez-vous continuer l'extinction malgré tout? (oui/non)"
        if ($response -notmatch '^(oui|yes|o|y)$') {
            Write-Host "Opération annulée" -ForegroundColor Yellow
            return
        }
    }
    
    # Exécuter les opérations d'extinction
    Write-Host "`nExécution des opérations de maintenance..." -ForegroundColor Yellow
    & ".\Manage-VCenterVMs.ps1" `
        -VCenterServer $vcenter `
        -Credential $cred `
        -CsvPath $csvPath `
        -MaxThreads 80 `
        -AllowSelfSigned `
        -ReportPath "C:\logs\maintenance_report_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
    
    Write-Host "`n✓ Maintenance terminée. Snapshots: $snapshotName" -ForegroundColor Green
}

# ============================================================================
# EXEMPLE 6: Retry automatique avec exponential backoff
# ============================================================================

function Example-RetryWithBackoff {
    Write-Host "`n=== EXEMPLE 6: Retry automatique ===" -ForegroundColor Cyan
    
    $vcenter = "vcenter.example.com"
    $csvPath = "C:\scripts\vms.csv"
    $cred = Get-Credential
    
    $maxRetries = 3
    $currentRetry = 0
    $allSuccess = $false
    $waitTime = 30  # Secondes
    
    while (-not $allSuccess -and $currentRetry -lt $maxRetries) {
        $currentRetry++
        Write-Host "`n=== Tentative $currentRetry / $maxRetries ===" -ForegroundColor Cyan
        
        # Exécuter les opérations
        & ".\Manage-VCenterVMs.ps1" `
            -VCenterServer $vcenter `
            -Credential $cred `
            -CsvPath $csvPath `
            -MaxThreads 100 `
            -AllowSelfSigned `
            -ReportPath "C:\logs\report_attempt_$currentRetry.txt"
        
        # Vérifier le résultat
        if ($LASTEXITCODE -eq 0) {
            $allSuccess = $true
            Write-Host "`n✓ Toutes les opérations ont réussi!" -ForegroundColor Green
        }
        else {
            if ($currentRetry -lt $maxRetries) {
                $backoffTime = $waitTime * [Math]::Pow(2, $currentRetry - 1)
                Write-Host "`n⚠️  Certaines opérations ont échoué" -ForegroundColor Yellow
                Write-Host "Nouvelle tentative dans $backoffTime secondes..." -ForegroundColor Yellow
                Start-Sleep -Seconds $backoffTime
            }
        }
    }
    
    if (-not $allSuccess) {
        Write-Host "`n✗ Échec après $maxRetries tentatives" -ForegroundColor Red
        exit 1
    }
}

# ============================================================================
# EXEMPLE 7: Traitement par lots pour 5000+ VMs
# ============================================================================

function Example-BatchProcessing {
    Write-Host "`n=== EXEMPLE 7: Traitement par lots - 5000 VMs ===" -ForegroundColor Cyan
    
    $vcenter = "vcenter.example.com"
    $csvPath = "C:\scripts\5000_vms.csv"
    $cred = Get-Credential
    $batchSize = 500  # Traiter 500 VMs à la fois
    
    # Lire toutes les VMs
    Write-Host "Chargement du CSV..." -ForegroundColor Yellow
    $allVMs = Import-Csv -Path $csvPath
    $totalVMs = $allVMs.Count
    
    Write-Host "Total de $totalVMs VMs à traiter" -ForegroundColor Green
    Write-Host "Taille des lots: $batchSize VMs`n" -ForegroundColor Green
    
    $batchNumber = 0
    $allResults = @()
    
    for ($i = 0; $i -lt $totalVMs; $i += $batchSize) {
        $batchNumber++
        $endIndex = [Math]::Min($i + $batchSize - 1, $totalVMs - 1)
        $batch = $allVMs[$i..$endIndex]
        
        Write-Host "=== Lot $batchNumber / $([Math]::Ceiling($totalVMs / $batchSize)) ===" -ForegroundColor Cyan
        Write-Host "VMs $($i + 1) à $($endIndex + 1)" -ForegroundColor Yellow
        
        # Créer un CSV temporaire pour ce lot
        $batchFile = "C:\temp\batch_$batchNumber.csv"
        $batch | Export-Csv -Path $batchFile -NoTypeInformation
        
        # Traiter le lot
        & ".\Manage-VCenterVMs.ps1" `
            -VCenterServer $vcenter `
            -Credential $cred `
            -CsvPath $batchFile `
            -MaxThreads 100 `
            -AllowSelfSigned `
            -ReportPath "C:\logs\batch_${batchNumber}_report.txt"
        
        # Petit délai entre les lots
        if ($i + $batchSize -lt $totalVMs) {
            Write-Host "Pause de 10 secondes avant le prochain lot...`n" -ForegroundColor Yellow
            Start-Sleep -Seconds 10
        }
        
        # Nettoyer le fichier temporaire
        Remove-Item -Path $batchFile -Force -ErrorAction SilentlyContinue
    }
    
    Write-Host "`n✓ Traitement par lots terminé!" -ForegroundColor Green
    Write-Host "Total de $batchNumber lots traités" -ForegroundColor Green
}

# ============================================================================
# EXEMPLE 8: Génération de rapports consolidés
# ============================================================================

function Example-ConsolidatedReport {
    Write-Host "`n=== EXEMPLE 8: Rapport consolidé ===" -ForegroundColor Cyan
    
    $reportDir = "C:\logs\vm-operations"
    $outputReport = "C:\reports\consolidated_report_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
    
    # Rechercher tous les fichiers CSV de détails
    $detailFiles = Get-ChildItem -Path $reportDir -Filter "*_details.csv" -File
    
    if ($detailFiles.Count -eq 0) {
        Write-Host "Aucun fichier de détails trouvé dans $reportDir" -ForegroundColor Yellow
        return
    }
    
    Write-Host "Consolidation de $($detailFiles.Count) rapports..." -ForegroundColor Yellow
    
    # Charger tous les résultats
    $allResults = @()
    foreach ($file in $detailFiles) {
        $results = Import-Csv -Path $file.FullName
        $allResults += $results
    }
    
    # Générer les statistiques
    $totalOps = $allResults.Count
    $successOps = ($allResults | Where-Object { $_.Success -eq $true }).Count
    $failedOps = $totalOps - $successOps
    
    # Statistiques par action
    $actionStats = $allResults | Group-Object -Property Action | Select-Object Name, Count
    
    # Top 10 des échecs
    $failures = $allResults | Where-Object { $_.Success -eq $false } | 
        Select-Object VMName, Action, Message | 
        Sort-Object VMName |
        Select-Object -First 10
    
    # Créer le rapport
    $report = @"
================================================================================
RAPPORT CONSOLIDÉ - GESTION VMS VCENTER
================================================================================
Généré le: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Fichiers analysés: $($detailFiles.Count)

STATISTIQUES GLOBALES:
--------------------------------------------------------------------------------
Total d'opérations: $totalOps
Succès: $successOps ($([Math]::Round(($successOps / $totalOps) * 100, 2))%)
Échecs: $failedOps ($([Math]::Round(($failedOps / $totalOps) * 100, 2))%)

RÉPARTITION PAR ACTION:
--------------------------------------------------------------------------------
$($actionStats | ForEach-Object { "$($_.Name): $($_.Count) opérations" } | Out-String)

TOP 10 DES ÉCHECS:
--------------------------------------------------------------------------------
$($failures | Format-Table -AutoSize | Out-String)

FICHIERS SOURCES:
--------------------------------------------------------------------------------
$($detailFiles | ForEach-Object { $_.Name } | Out-String)

================================================================================
"@
    
    # Afficher et sauvegarder
    Write-Host $report -ForegroundColor Cyan
    $report | Out-File -FilePath $outputReport -Encoding UTF8
    
    Write-Host "`nRapport consolidé sauvegardé: $outputReport" -ForegroundColor Green
}

# ============================================================================
# EXEMPLE 9: Intégration avec notifications email
# ============================================================================

function Example-EmailNotifications {
    Write-Host "`n=== EXEMPLE 9: Notifications email ===" -ForegroundColor Cyan
    
    $vcenter = "vcenter.example.com"
    $csvPath = "C:\scripts\vms.csv"
    $cred = Get-Credential
    
    # Configuration email
    $smtpServer = "smtp.example.com"
    $smtpPort = 587
    $emailFrom = "vcenter-automation@example.com"
    $emailTo = "admin@example.com"
    $emailCred = Get-Credential -Message "Credentials SMTP"
    
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $reportPath = "C:\logs\report_$timestamp.txt"
    
    # Exécuter les opérations
    Write-Host "Exécution des opérations..." -ForegroundColor Yellow
    & ".\Manage-VCenterVMs.ps1" `
        -VCenterServer $vcenter `
        -Credential $cred `
        -CsvPath $csvPath `
        -MaxThreads 100 `
        -AllowSelfSigned `
        -ReportPath $reportPath
    
    # Lire le rapport
    $reportContent = Get-Content -Path $reportPath -Raw
    
    # Déterminer le statut
    if ($LASTEXITCODE -eq 0) {
        $subject = "✓ Gestion VMs - Succès"
        $body = "Toutes les opérations ont réussi.`n`n$reportContent"
    }
    else {
        $subject = "⚠️  Gestion VMs - Échecs détectés"
        $body = "Certaines opérations ont échoué. Voir le rapport ci-dessous.`n`n$reportContent"
    }
    
    # Envoyer l'email
    try {
        Send-MailMessage `
            -SmtpServer $smtpServer `
            -Port $smtpPort `
            -UseSsl `
            -Credential $emailCred `
            -From $emailFrom `
            -To $emailTo `
            -Subject $subject `
            -Body $body `
            -Attachments $reportPath
        
        Write-Host "`n✓ Email de notification envoyé à $emailTo" -ForegroundColor Green
    }
    catch {
        Write-Host "`n✗ Erreur lors de l'envoi de l'email: $_" -ForegroundColor Red
    }
}

# ============================================================================
# MENU PRINCIPAL
# ============================================================================

function Show-Menu {
    Clear-Host
    Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║       Exemples d'utilisation - Manage-VCenterVMs.ps1          ║" -ForegroundColor Cyan
    Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "1. Utilisation basique" -ForegroundColor White
    Write-Host "2. Performance maximale (2000 VMs)" -ForegroundColor White
    Write-Host "3. Génération dynamique du CSV" -ForegroundColor White
    Write-Host "4. Démarrage séquencé (Tiers)" -ForegroundColor White
    Write-Host "5. Maintenance avec snapshots" -ForegroundColor White
    Write-Host "6. Retry automatique" -ForegroundColor White
    Write-Host "7. Traitement par lots (5000+ VMs)" -ForegroundColor White
    Write-Host "8. Rapport consolidé" -ForegroundColor White
    Write-Host "9. Notifications email" -ForegroundColor White
    Write-Host "Q. Quitter" -ForegroundColor Yellow
    Write-Host ""
}

# Boucle principale du menu
do {
    Show-Menu
    $choice = Read-Host "Choisissez un exemple (1-9 ou Q)"
    
    switch ($choice) {
        '1' { Example-Basic }
        '2' { Example-HighPerformance }
        '3' { Example-DynamicCSV }
        '4' { Example-TieredStartup }
        '5' { Example-MaintenanceWithSnapshots }
        '6' { Example-RetryWithBackoff }
        '7' { Example-BatchProcessing }
        '8' { Example-ConsolidatedReport }
        '9' { Example-EmailNotifications }
        'Q' { Write-Host "`nAu revoir!" -ForegroundColor Green; exit }
        default { Write-Host "`nChoix invalide!" -ForegroundColor Red; Start-Sleep -Seconds 2 }
    }
    
    if ($choice -ne 'Q') {
        Write-Host "`nAppuyez sur une touche pour revenir au menu..." -ForegroundColor Yellow
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
} while ($choice -ne 'Q')