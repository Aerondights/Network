#Requires -Version 7.0
<#
.SYNOPSIS
    Script professionnel pour gérer l'alimentation des VMs vCenter via PowerCLI avec RunSpaces.
    
.DESCRIPTION
    Script haute performance utilisant les RunSpaces PowerShell pour gérer jusqu'à 2000+ VMs en parallèle.
    Optimisé pour des opérations massives d'allumage/extinction de VMs.
    
.PARAMETER VCenterServer
    Serveur vCenter (ex: vcenter.example.com)
    
.PARAMETER Username
    Nom d'utilisateur vCenter
    
.PARAMETER Password
    Mot de passe vCenter (utilisez -Credential pour plus de sécurité)
    
.PARAMETER Credential
    PSCredential object pour l'authentification
    
.PARAMETER CsvPath
    Chemin du fichier CSV contenant les VMs et actions
    
.PARAMETER MaxThreads
    Nombre maximum de threads parallèles (défaut: 50, max recommandé: 100)
    
.PARAMETER BatchSize
    Taille des lots pour le traitement (défaut: 100)
    
.PARAMETER NoVerifySSL
    Désactiver la vérification SSL
    
.PARAMETER AllowSelfSigned
    Autoriser les certificats auto-signés
    
.PARAMETER NoWait
    Ne pas attendre la fin des opérations
    
.PARAMETER ReportPath
    Chemin du fichier de rapport
    
.PARAMETER LogPath
    Chemin du fichier de log
    
.PARAMETER Verbose
    Afficher les détails
    
.EXAMPLE
    .\Manage-VCenterVMs.ps1 -VCenterServer vcenter.example.com -Username admin@vsphere.local -Password 'Pass' -CsvPath vms.csv
    
.EXAMPLE
    .\Manage-VCenterVMs.ps1 -VCenterServer vcenter.example.com -Credential (Get-Credential) -CsvPath vms.csv -MaxThreads 100
    
.EXAMPLE
    $cred = Get-Credential
    .\Manage-VCenterVMs.ps1 -VCenterServer vcenter.example.com -Credential $cred -CsvPath vms.csv -AllowSelfSigned -MaxThreads 80 -ReportPath report.txt
    
.NOTES
    Auteur: Script automatisé
    Version: 2.0.0
    Nécessite: PowerShell 7.0+, VMware.PowerCLI 13.0+
    
    Installation PowerCLI:
    Install-Module -Name VMware.PowerCLI -Scope CurrentUser -Force
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$VCenterServer,
    
    [Parameter(Mandatory = $false)]
    [string]$Username,
    
    [Parameter(Mandatory = $false)]
    [string]$Password,
    
    [Parameter(Mandatory = $false)]
    [PSCredential]$Credential,
    
    [Parameter(Mandatory = $true)]
    [ValidateScript({Test-Path $_})]
    [string]$CsvPath,
    
    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 200)]
    [int]$MaxThreads = 50,
    
    [Parameter(Mandatory = $false)]
    [ValidateRange(10, 500)]
    [int]$BatchSize = 100,
    
    [Parameter(Mandatory = $false)]
    [switch]$NoVerifySSL,
    
    [Parameter(Mandatory = $false)]
    [switch]$AllowSelfSigned,
    
    [Parameter(Mandatory = $false)]
    [switch]$NoWait,
    
    [Parameter(Mandatory = $false)]
    [string]$ReportPath,
    
    [Parameter(Mandatory = $false)]
    [string]$LogPath,
    
    [Parameter(Mandatory = $false)]
    [ValidateSet('INFO', 'DEBUG', 'WARNING', 'ERROR')]
    [string]$LogLevel = 'INFO'
)

#region Classes et Enums

enum PowerAction {
    POWER_ON
    POWER_OFF
    SHUTDOWN
    RESTART
    SUSPEND
}

enum VMPowerState {
    PoweredOn
    PoweredOff
    Suspended
}

class VMOperation {
    [string]$VMName
    [PowerAction]$Action
    [bool]$Success
    [string]$Message
    [double]$Duration
    [datetime]$Timestamp
    
    VMOperation([string]$vmName, [PowerAction]$action) {
        $this.VMName = $vmName
        $this.Action = $action
        $this.Success = $false
        $this.Message = ""
        $this.Duration = 0
        $this.Timestamp = Get-Date
    }
}

class PerformanceMetrics {
    [int]$TotalOperations
    [int]$SuccessCount
    [int]$FailureCount
    [double]$TotalDuration
    [double]$AverageDuration
    [double]$OperationsPerSecond
    [int]$MaxConcurrency
    [datetime]$StartTime
    [datetime]$EndTime
    
    PerformanceMetrics() {
        $this.StartTime = Get-Date
    }
    
    [void] Finalize() {
        $this.EndTime = Get-Date
        $this.TotalDuration = ($this.EndTime - $this.StartTime).TotalSeconds
        if ($this.TotalOperations -gt 0) {
            $this.AverageDuration = $this.TotalDuration / $this.TotalOperations
            $this.OperationsPerSecond = $this.TotalOperations / $this.TotalDuration
        }
    }
}

#endregion

#region Logging Functions

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        
        [Parameter(Mandatory = $false)]
        [ValidateSet('INFO', 'DEBUG', 'WARNING', 'ERROR', 'SUCCESS')]
        [string]$Level = 'INFO'
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] [$Level] $Message"
    
    # Couleurs selon le niveau
    $color = switch ($Level) {
        'INFO'    { 'White' }
        'DEBUG'   { 'Gray' }
        'WARNING' { 'Yellow' }
        'ERROR'   { 'Red' }
        'SUCCESS' { 'Green' }
        default   { 'White' }
    }
    
    # Filtrage selon LogLevel
    $levelPriority = @{
        'DEBUG' = 0
        'INFO' = 1
        'WARNING' = 2
        'ERROR' = 3
    }
    
    if ($levelPriority[$Level] -ge $levelPriority[$script:LogLevel]) {
        Write-Host $logMessage -ForegroundColor $color
        
        # Écrire dans le fichier de log si spécifié
        if ($script:LogPath) {
            Add-Content -Path $script:LogPath -Value $logMessage -ErrorAction SilentlyContinue
        }
    }
}

#endregion

#region SSL Configuration

function Set-VCenterSSLConfiguration {
    param(
        [bool]$NoVerifySSL,
        [bool]$AllowSelfSigned
    )
    
    if ($NoVerifySSL) {
        Write-Log "⚠️  Vérification SSL désactivée - connexion non sécurisée!" -Level WARNING
        Set-PowerCLIConfiguration -InvalidCertificateAction Ignore -Confirm:$false -Scope Session | Out-Null
        
        # Confirmation utilisateur pour la sécurité
        $response = Read-Host "Voulez-vous continuer malgré ce risque de sécurité? (oui/non)"
        if ($response -notmatch '^(oui|yes|o|y)$') {
            Write-Log "Opération annulée par l'utilisateur" -Level INFO
            exit 0
        }
    }
    elseif ($AllowSelfSigned) {
        Write-Log "Mode certificat auto-signé activé" -Level INFO
        Set-PowerCLIConfiguration -InvalidCertificateAction Ignore -Confirm:$false -Scope Session | Out-Null
    }
    else {
        Write-Log "Vérification SSL complète activée" -Level INFO
        Set-PowerCLIConfiguration -InvalidCertificateAction Fail -Confirm:$false -Scope Session | Out-Null
    }
    
    # Désactiver la participation au programme d'amélioration
    Set-PowerCLIConfiguration -ParticipateInCEIP $false -Confirm:$false -Scope Session | Out-Null
}

#endregion

#region VM Operations with RunSpaces

function Invoke-VMOperationParallel {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Operations,
        
        [Parameter(Mandatory = $true)]
        [int]$MaxThreads,
        
        [Parameter(Mandatory = $true)]
        [string]$VCenterServer,
        
        [Parameter(Mandatory = $true)]
        [PSCredential]$Credential,
        
        [Parameter(Mandatory = $false)]
        [bool]$WaitForCompletion = $true,
        
        [Parameter(Mandatory = $false)]
        [bool]$NoVerifySSL = $false
    )
    
    Write-Log "Initialisation du pool de RunSpaces avec $MaxThreads threads..." -Level INFO
    
    # Créer le RunSpace Pool (plus performant que les Jobs)
    $runspacePool = [runspacefactory]::CreateRunspacePool(1, $MaxThreads)
    $runspacePool.ApartmentState = "MTA"
    $runspacePool.ThreadOptions = "ReuseThread"
    $runspacePool.Open()
    
    # ScriptBlock pour l'exécution dans chaque RunSpace
    $scriptBlock = {
        param(
            $VMName,
            $Action,
            $VCenterServer,
            $Username,
            $Password,
            $WaitForCompletion,
            $NoVerifySSL
        )
        
        $result = [PSCustomObject]@{
            VMName = $VMName
            Action = $Action
            Success = $false
            Message = ""
            Duration = 0
            Timestamp = Get-Date
        }
        
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        
        try {
            # Importer le module PowerCLI dans ce RunSpace
            Import-Module VMware.VimAutomation.Core -ErrorAction Stop | Out-Null
            
            # Configuration SSL
            if ($NoVerifySSL) {
                Set-PowerCLIConfiguration -InvalidCertificateAction Ignore -Confirm:$false -Scope Session -WarningAction SilentlyContinue | Out-Null
            }
            Set-PowerCLIConfiguration -ParticipateInCEIP $false -Confirm:$false -Scope Session -WarningAction SilentlyContinue | Out-Null
            
            # Créer les credentials
            $secPassword = ConvertTo-SecureString $Password -AsPlainText -Force
            $cred = New-Object System.Management.Automation.PSCredential($Username, $secPassword)
            
            # Connexion au vCenter (réutilise la connexion si possible)
            $viServer = Connect-VIServer -Server $VCenterServer -Credential $cred -ErrorAction Stop -WarningAction SilentlyContinue
            
            # Récupérer la VM
            $vm = Get-VM -Name $VMName -Server $viServer -ErrorAction Stop
            
            if (-not $vm) {
                $result.Message = "VM non trouvée"
                return $result
            }
            
            # Vérifier l'état actuel
            $currentState = $vm.PowerState
            
            # Exécuter l'action appropriée
            switch ($Action) {
                "POWER_ON" {
                    if ($currentState -eq "PoweredOn") {
                        $result.Success = $true
                        $result.Message = "VM déjà allumée"
                    }
                    else {
                        $task = Start-VM -VM $vm -Confirm:$false -RunAsync:(!$WaitForCompletion) -ErrorAction Stop
                        if ($WaitForCompletion) {
                            Wait-Task -Task $task -ErrorAction Stop | Out-Null
                        }
                        $result.Success = $true
                        $result.Message = "VM allumée avec succès"
                    }
                }
                "POWER_OFF" {
                    if ($currentState -eq "PoweredOff") {
                        $result.Success = $true
                        $result.Message = "VM déjà éteinte"
                    }
                    else {
                        $task = Stop-VM -VM $vm -Confirm:$false -RunAsync:(!$WaitForCompletion) -ErrorAction Stop
                        if ($WaitForCompletion) {
                            Wait-Task -Task $task -ErrorAction Stop | Out-Null
                        }
                        $result.Success = $true
                        $result.Message = "VM éteinte avec succès"
                    }
                }
                "SHUTDOWN" {
                    if ($currentState -eq "PoweredOff") {
                        $result.Success = $true
                        $result.Message = "VM déjà éteinte"
                    }
                    else {
                        # Vérifier si VMware Tools est en cours d'exécution
                        if ($vm.Guest.State -eq "Running") {
                            $task = Stop-VMGuest -VM $vm -Confirm:$false -ErrorAction Stop
                            $result.Success = $true
                            $result.Message = "Arrêt gracieux initié"
                        }
                        else {
                            $result.Message = "VMware Tools non disponible, utilisez POWER_OFF"
                        }
                    }
                }
                "RESTART" {
                    if ($currentState -eq "PoweredOff") {
                        $result.Message = "Impossible de redémarrer une VM éteinte"
                    }
                    else {
                        $task = Restart-VM -VM $vm -Confirm:$false -RunAsync:(!$WaitForCompletion) -ErrorAction Stop
                        if ($WaitForCompletion) {
                            Wait-Task -Task $task -ErrorAction Stop | Out-Null
                        }
                        $result.Success = $true
                        $result.Message = "VM redémarrée avec succès"
                    }
                }
                "SUSPEND" {
                    if ($currentState -eq "Suspended") {
                        $result.Success = $true
                        $result.Message = "VM déjà suspendue"
                    }
                    elseif ($currentState -eq "PoweredOff") {
                        $result.Message = "Impossible de suspendre une VM éteinte"
                    }
                    else {
                        $task = Suspend-VM -VM $vm -Confirm:$false -RunAsync:(!$WaitForCompletion) -ErrorAction Stop
                        if ($WaitForCompletion) {
                            Wait-Task -Task $task -ErrorAction Stop | Out-Null
                        }
                        $result.Success = $true
                        $result.Message = "VM suspendue avec succès"
                    }
                }
                default {
                    $result.Message = "Action non supportée: $Action"
                }
            }
        }
        catch {
            $result.Message = "Erreur: $($_.Exception.Message)"
        }
        finally {
            # Déconnexion
            try {
                if ($viServer) {
                    Disconnect-VIServer -Server $viServer -Confirm:$false -ErrorAction SilentlyContinue
                }
            }
            catch {}
            
            $stopwatch.Stop()
            $result.Duration = $stopwatch.Elapsed.TotalSeconds
        }
        
        return $result
    }
    
    # Créer les RunSpaces
    $runspaces = @()
    $totalOps = $Operations.Count
    $currentOp = 0
    
    Write-Log "Lancement de $totalOps opérations..." -Level INFO
    
    foreach ($op in $Operations) {
        $currentOp++
        
        $powershell = [powershell]::Create()
        $powershell.RunspacePool = $runspacePool
        
        # Ajouter le script et les paramètres
        [void]$powershell.AddScript($scriptBlock)
        [void]$powershell.AddArgument($op.VMName)
        [void]$powershell.AddArgument($op.Action)
        [void]$powershell.AddArgument($VCenterServer)
        [void]$powershell.AddArgument($Credential.UserName)
        [void]$powershell.AddArgument($Credential.GetNetworkCredential().Password)
        [void]$powershell.AddArgument($WaitForCompletion)
        [void]$powershell.AddArgument($NoVerifySSL)
        
        # Démarrer l'exécution asynchrone
        $runspaces += [PSCustomObject]@{
            Pipe = $powershell
            Status = $powershell.BeginInvoke()
            VMName = $op.VMName
            Action = $op.Action
        }
        
        # Afficher la progression
        if ($currentOp % 50 -eq 0 -or $currentOp -eq $totalOps) {
            Write-Progress -Activity "Démarrage des opérations" -Status "$currentOp/$totalOps" -PercentComplete (($currentOp / $totalOps) * 100)
        }
    }
    
    Write-Progress -Activity "Démarrage des opérations" -Completed
    Write-Log "Toutes les opérations ont été lancées, attente des résultats..." -Level INFO
    
    # Collecter les résultats
    $results = @()
    $completed = 0
    $startTime = Get-Date
    
    while ($runspaces.Count -gt 0) {
        $runspacesToRemove = @()
        
        foreach ($runspace in $runspaces) {
            if ($runspace.Status.IsCompleted) {
                try {
                    $result = $runspace.Pipe.EndInvoke($runspace.Status)
                    $results += $result
                    
                    $completed++
                    $elapsed = (Get-Date) - $startTime
                    $opsPerSec = if ($elapsed.TotalSeconds -gt 0) { [math]::Round($completed / $elapsed.TotalSeconds, 2) } else { 0 }
                    
                    # Log du résultat
                    $status = if ($result.Success) { "✓" } else { "✗" }
                    $level = if ($result.Success) { "SUCCESS" } else { "ERROR" }
                    Write-Log "$status [$completed/$totalOps] $($result.VMName): $($result.Message) ($([math]::Round($result.Duration, 2))s) [$opsPerSec ops/s]" -Level $level
                    
                    # Afficher la progression
                    Write-Progress -Activity "Traitement des VMs" -Status "$completed/$totalOps complétées ($opsPerSec ops/s)" -PercentComplete (($completed / $totalOps) * 100)
                }
                catch {
                    Write-Log "Erreur lors de la récupération du résultat pour $($runspace.VMName): $_" -Level ERROR
                }
                finally {
                    $runspace.Pipe.Dispose()
                    $runspacesToRemove += $runspace
                }
            }
        }
        
        # Retirer les runspaces terminés
        foreach ($rs in $runspacesToRemove) {
            $runspaces = $runspaces | Where-Object { $_ -ne $rs }
        }
        
        # Attendre un peu avant de vérifier à nouveau
        if ($runspaces.Count -gt 0) {
            Start-Sleep -Milliseconds 100
        }
    }
    
    Write-Progress -Activity "Traitement des VMs" -Completed
    
    # Nettoyer le pool
    $runspacePool.Close()
    $runspacePool.Dispose()
    
    Write-Log "Toutes les opérations sont terminées!" -Level SUCCESS
    
    return $results
}

#endregion

#region CSV Processing

function Import-VMOperations {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CsvPath
    )
    
    Write-Log "Lecture du fichier CSV: $CsvPath" -Level INFO
    
    try {
        $csvData = Import-Csv -Path $CsvPath -Encoding UTF8
        
        # Valider les colonnes
        if (-not ($csvData[0].PSObject.Properties.Name -contains 'vm_name' -and 
                  $csvData[0].PSObject.Properties.Name -contains 'action')) {
            throw "Le fichier CSV doit contenir les colonnes 'vm_name' et 'action'"
        }
        
        $operations = @()
        $lineNumber = 1
        
        foreach ($row in $csvData) {
            $lineNumber++
            
            $vmName = $row.vm_name.Trim()
            $actionStr = $row.action.Trim().ToUpper()
            
            if ([string]::IsNullOrWhiteSpace($vmName)) {
                Write-Log "Ligne $lineNumber : nom de VM vide, ignoré" -Level WARNING
                continue
            }
            
            # Valider l'action
            try {
                $action = [PowerAction]::$actionStr
            }
            catch {
                Write-Log "Ligne $lineNumber : action '$actionStr' invalide pour VM '$vmName', ignoré" -Level WARNING
                continue
            }
            
            $operations += [PSCustomObject]@{
                VMName = $vmName
                Action = $action
            }
        }
        
        Write-Log "$($operations.Count) opérations chargées depuis le CSV" -Level SUCCESS
        return $operations
    }
    catch {
        Write-Log "Erreur lors de la lecture du CSV: $_" -Level ERROR
        throw
    }
}

#endregion

#region Reporting

function New-OperationReport {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Results,
        
        [Parameter(Mandatory = $true)]
        [PerformanceMetrics]$Metrics,
        
        [Parameter(Mandatory = $false)]
        [string]$OutputPath
    )
    
    $report = @()
    $report += "=" * 100
    $report += "RAPPORT D'EXÉCUTION - Gestion VMs vCenter"
    $report += "=" * 100
    $report += ""
    $report += "STATISTIQUES GÉNÉRALES:"
    $report += "-" * 100
    $report += "Total d'opérations       : $($Metrics.TotalOperations)"
    $report += "Succès                   : $($Metrics.SuccessCount) ($([math]::Round(($Metrics.SuccessCount / $Metrics.TotalOperations) * 100, 2))%)"
    $report += "Échecs                   : $($Metrics.FailureCount) ($([math]::Round(($Metrics.FailureCount / $Metrics.TotalOperations) * 100, 2))%)"
    $report += ""
    $report += "PERFORMANCES:"
    $report += "-" * 100
    $report += "Heure de début           : $($Metrics.StartTime.ToString('yyyy-MM-dd HH:mm:ss'))"
    $report += "Heure de fin             : $($Metrics.EndTime.ToString('yyyy-MM-dd HH:mm:ss'))"
    $report += "Durée totale             : $([math]::Round($Metrics.TotalDuration, 2))s"
    $report += "Durée moyenne par VM     : $([math]::Round($Metrics.AverageDuration, 2))s"
    $report += "Opérations par seconde   : $([math]::Round($Metrics.OperationsPerSecond, 2)) ops/s"
    $report += "Concurrence maximale     : $($Metrics.MaxConcurrency) threads"
    $report += ""
    $report += "DÉTAILS DES OPÉRATIONS:"
    $report += "=" * 100
    $report += "{0,-40} {1,-15} {2,-10} {3,-10} {4}" -f "VM", "Action", "Statut", "Durée(s)", "Message"
    $report += "-" * 100
    
    foreach ($result in $Results | Sort-Object -Property Success, VMName) {
        $status = if ($result.Success) { "SUCCÈS" } else { "ÉCHEC" }
        $report += "{0,-40} {1,-15} {2,-10} {3,-10} {4}" -f `
            $result.VMName.Substring(0, [Math]::Min(40, $result.VMName.Length)),
            $result.Action,
            $status,
            [math]::Round($result.Duration, 2),
            $result.Message.Substring(0, [Math]::Min(50, $result.Message.Length))
    }
    
    $report += "=" * 100
    $report += ""
    
    $reportText = $report -join "`n"
    
    # Afficher le rapport
    Write-Host "`n$reportText" -ForegroundColor Cyan
    
    # Sauvegarder le rapport si un chemin est spécifié
    if ($OutputPath) {
        try {
            $reportText | Out-File -FilePath $OutputPath -Encoding UTF8 -Force
            Write-Log "Rapport sauvegardé dans: $OutputPath" -Level SUCCESS
        }
        catch {
            Write-Log "Erreur lors de la sauvegarde du rapport: $_" -Level ERROR
        }
    }
}

#endregion

#region Main Execution

function Main {
    $ErrorActionPreference = "Stop"
    
    # Banner
    Write-Host "`n" -NoNewline
    Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║    Gestion VMs vCenter - PowerShell High Performance Script    ║" -ForegroundColor Cyan
    Write-Host "║                    Version 2.0.0 - RunSpaces                   ║" -ForegroundColor Cyan
    Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    
    # Initialiser les métriques
    $metrics = [PerformanceMetrics]::new()
    $metrics.MaxConcurrency = $MaxThreads
    
    try {
        # Vérifier PowerCLI
        Write-Log "Vérification de VMware PowerCLI..." -Level INFO
        if (-not (Get-Module -ListAvailable -Name VMware.PowerCLI)) {
            Write-Log "VMware PowerCLI n'est pas installé!" -Level ERROR
            Write-Log "Installation: Install-Module -Name VMware.PowerCLI -Scope CurrentUser -Force" -Level INFO
            exit 1
        }
        
        Import-Module VMware.VimAutomation.Core -ErrorAction Stop
        Write-Log "VMware PowerCLI chargé avec succès" -Level SUCCESS
        
        # Configuration SSL
        Set-VCenterSSLConfiguration -NoVerifySSL $NoVerifySSL.IsPresent -AllowSelfSigned $AllowSelfSigned.IsPresent
        
        # Préparer les credentials
        if ($Credential) {
            $cred = $Credential
        }
        elseif ($Username -and $Password) {
            $secPassword = ConvertTo-SecureString $Password -AsPlainText -Force
            $cred = New-Object System.Management.Automation.PSCredential($Username, $secPassword)
        }
        else {
            Write-Log "Identifiants manquants. Utilisez -Username/-Password ou -Credential" -Level ERROR
            exit 1
        }
        
        # Test de connexion au vCenter
        Write-Log "Connexion au vCenter: $VCenterServer" -Level INFO
        try {
            $viServer = Connect-VIServer -Server $VCenterServer -Credential $cred -ErrorAction Stop
            Write-Log "✓ Connexion réussie à $VCenterServer" -Level SUCCESS
            Write-Log "  Version: $($viServer.Version)" -Level INFO
            Write-Log "  Build: $($viServer.Build)" -Level INFO
        }
        catch {
            Write-Log "✗ Échec de connexion à $VCenterServer" -Level ERROR
            Write-Log "Erreur: $($_.Exception.Message)" -Level ERROR
            exit 1
        }
        
        # Charger les opérations depuis le CSV
        $operations = Import-VMOperations -CsvPath $CsvPath
        
        if ($operations.Count -eq 0) {
            Write-Log "Aucune opération à effectuer" -Level WARNING
            Disconnect-VIServer -Server $viServer -Confirm:$false
            exit 0
        }
        
        $metrics.TotalOperations = $operations.Count
        
        # Afficher les statistiques des opérations
        Write-Host "`nRÉSUMÉ DES OPÉRATIONS:" -ForegroundColor Yellow
        Write-Host ("-" * 60) -ForegroundColor Yellow
        $operations | Group-Object -Property Action | ForEach-Object {
            Write-Host "  $($_.Name): $($_.Count) VMs" -ForegroundColor White
        }
        Write-Host ("-" * 60) -ForegroundColor Yellow
        Write-Host ""
        
        # Déconnecter la connexion de test
        Disconnect-VIServer -Server $viServer -Confirm:$false
        
        # Traiter les opérations en parallèle avec RunSpaces
        Write-Log "Démarrage du traitement parallèle avec $MaxThreads threads..." -Level INFO
        Write-Log "Taille des lots: $BatchSize VMs" -Level INFO
        
        $results = Invoke-VMOperationParallel `
            -Operations $operations `
            -MaxThreads $MaxThreads `
            -VCenterServer $VCenterServer `
            -Credential $cred `
            -WaitForCompletion (!$NoWait.IsPresent) `
            -NoVerifySSL $NoVerifySSL.IsPresent
        
        # Calculer les métriques finales
        $metrics.SuccessCount = ($results | Where-Object { $_.Success }).Count
        $metrics.FailureCount = $metrics.TotalOperations - $metrics.SuccessCount
        $metrics.Finalize()
        
        # Générer le rapport
        New-OperationReport -Results $results -Metrics $metrics -OutputPath $ReportPath
        
        # Exporter les résultats en CSV si demandé
        if ($ReportPath) {
            $csvReportPath = $ReportPath -replace '\.[^.]+, '_details.csv'
            $results | Export-Csv -Path $csvReportPath -NoTypeInformation -Encoding UTF8
            Write-Log "Résultats détaillés exportés dans: $csvReportPath" -Level SUCCESS
        }
        
        # Code de sortie basé sur les résultats
        if ($metrics.FailureCount -eq 0) {
            Write-Log "`n✓ Toutes les opérations ont réussi!" -Level SUCCESS
            exit 0
        }
        else {
            Write-Log "`n⚠️  $($metrics.FailureCount) opération(s) ont échoué" -Level WARNING
            exit 1
        }
    }
    catch {
        Write-Log "Erreur fatale: $_" -Level ERROR
        Write-Log $_.ScriptStackTrace -Level ERROR
        exit 1
    }
    finally {
        # Nettoyage des connexions
        try {
            Get-VIServer -ErrorAction SilentlyContinue | Disconnect-VIServer -Confirm:$false -ErrorAction SilentlyContinue
        }
        catch {}
    }
}

#endregion

# Point d'entrée
Main