<#
.SYNOPSIS
    Génère une topologie réseau à partir de fichiers CSV VLAN

.DESCRIPTION
    Ce script analyse les fichiers CSV contenant les informations VLAN et génère
    une topologie réseau complète avec statistiques et rapports détaillés.

.PARAMETER VlanListPath
    Chemin vers le fichier CSV contenant la liste des VLANs

.PARAMETER VlanDetailsFolder
    Dossier contenant les fichiers CSV de détails par VLAN

.PARAMETER OutputPath
    Chemin de sortie pour les rapports générés

.EXAMPLE
    .\Generate-NetworkTopology.ps1 -VlanListPath "C:\Data\vlans.csv" -VlanDetailsFolder "C:\Data\VlanDetails" -OutputPath "C:\Output"

.NOTES
    Auteur: Expert PowerShell
    Version: 1.0
    Date: 2025-11-13
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({Test-Path $_ -PathType Leaf})]
    [string]$VlanListPath,

    [Parameter(Mandatory = $true)]
    [ValidateScript({Test-Path $_ -PathType Container})]
    [string]$VlanDetailsFolder,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath = ".\NetworkTopology_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
)

#region Fonctions

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet('Info', 'Warning', 'Error', 'Success')]
        [string]$Level = 'Info'
    )
    
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $color = switch ($Level) {
        'Info'    { 'Cyan' }
        'Warning' { 'Yellow' }
        'Error'   { 'Red' }
        'Success' { 'Green' }
    }
    
    Write-Host "[$timestamp] [$Level] $Message" -ForegroundColor $color
}

function Import-VlanList {
    param([string]$Path)
    
    try {
        Write-Log "Importation de la liste des VLANs depuis: $Path"
        $vlans = Import-Csv -Path $Path -Encoding UTF8
        Write-Log "✓ $($vlans.Count) VLANs importés avec succès" -Level Success
        return $vlans
    }
    catch {
        Write-Log "Erreur lors de l'importation de la liste VLAN: $_" -Level Error
        throw
    }
}

function Import-VlanDetails {
    param(
        [string]$FolderPath,
        [array]$VlanList
    )
    
    $allDetails = @{}
    $totalHosts = 0
    
    foreach ($vlan in $VlanList) {
        $vlanId = $vlan.VLAN_ID
        $vlanFile = Get-ChildItem -Path $FolderPath -Filter "*$vlanId*.csv" | Select-Object -First 1
        
        if ($vlanFile) {
            try {
                $details = Import-Csv -Path $vlanFile.FullName -Encoding UTF8
                $allDetails[$vlanId] = $details
                $totalHosts += $details.Count
                Write-Log "  ├─ VLAN $vlanId : $($details.Count) hôtes"
            }
            catch {
                Write-Log "  ├─ Erreur VLAN $vlanId : $_" -Level Warning
                $allDetails[$vlanId] = @()
            }
        }
        else {
            Write-Log "  ├─ Fichier non trouvé pour VLAN $vlanId" -Level Warning
            $allDetails[$vlanId] = @()
        }
    }
    
    Write-Log "✓ Total: $totalHosts hôtes chargés" -Level Success
    return $allDetails
}

function Build-NetworkTopology {
    param(
        [array]$VlanList,
        [hashtable]$VlanDetails
    )
    
    Write-Log "Construction de la topologie réseau..."
    
    $topology = @{
        VLANs = @()
        Datacenters = @{}
        Statistics = @{
            TotalVLANs = $VlanList.Count
            TotalHosts = 0
            TotalDatacenters = 0
            HostsByVLAN = @{}
            HostsByDatacenter = @{}
        }
    }
    
    foreach ($vlan in $VlanList) {
        $vlanId = $vlan.VLAN_ID
        $hosts = $VlanDetails[$vlanId]
        
        $vlanObj = [PSCustomObject]@{
            VLAN_ID = $vlanId
            Name = $vlan.Name
            Description = $vlan.Description
            Network = $vlan.Network
            HostCount = $hosts.Count
            Hosts = $hosts
            Datacenters = ($hosts | Select-Object -ExpandProperty Datacenter -Unique)
        }
        
        $topology.VLANs += $vlanObj
        $topology.Statistics.TotalHosts += $hosts.Count
        $topology.Statistics.HostsByVLAN[$vlanId] = $hosts.Count
        
        # Agrégation par datacenter
        foreach ($host in $hosts) {
            $dc = $host.Datacenter
            if (-not $topology.Datacenters.ContainsKey($dc)) {
                $topology.Datacenters[$dc] = @{
                    Name = $dc
                    VLANs = @()
                    HostCount = 0
                    Hosts = @()
                }
            }
            
            if ($topology.Datacenters[$dc].VLANs -notcontains $vlanId) {
                $topology.Datacenters[$dc].VLANs += $vlanId
            }
            
            $topology.Datacenters[$dc].HostCount++
            $topology.Datacenters[$dc].Hosts += $host
            
            if (-not $topology.Statistics.HostsByDatacenter.ContainsKey($dc)) {
                $topology.Statistics.HostsByDatacenter[$dc] = 0
            }
            $topology.Statistics.HostsByDatacenter[$dc]++
        }
    }
    
    $topology.Statistics.TotalDatacenters = $topology.Datacenters.Count
    
    Write-Log "✓ Topologie construite avec succès" -Level Success
    return $topology
}

function Export-TopologyReports {
    param(
        [hashtable]$Topology,
        [string]$OutputPath
    )
    
    Write-Log "Génération des rapports..."
    
    # Création du dossier de sortie
    if (-not (Test-Path $OutputPath)) {
        New-Item -Path $OutputPath -ItemType Directory -Force | Out-Null
    }
    
    # Rapport principal
    $mainReport = Join-Path $OutputPath "TopologieReseau_Principale.html"
    Export-MainReport -Topology $Topology -Path $mainReport
    
    # Rapport statistiques
    $statsReport = Join-Path $OutputPath "TopologieReseau_Statistiques.csv"
    Export-StatisticsReport -Topology $Topology -Path $statsReport
    
    # Rapport par VLAN
    $vlanReport = Join-Path $OutputPath "TopologieReseau_ParVLAN.csv"
    Export-VlanReport -Topology $Topology -Path $vlanReport
    
    # Rapport par Datacenter
    $dcReport = Join-Path $OutputPath "TopologieReseau_ParDatacenter.csv"
    Export-DatacenterReport -Topology $Topology -Path $dcReport
    
    # Export JSON complet
    $jsonExport = Join-Path $OutputPath "TopologieReseau_Complete.json"
    $Topology | ConvertTo-Json -Depth 10 | Out-File -FilePath $jsonExport -Encoding UTF8
    
    Write-Log "✓ Rapports générés dans: $OutputPath" -Level Success
}

function Export-MainReport {
    param(
        [hashtable]$Topology,
        [string]$Path
    )
    
    $html = @"
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Topologie Réseau - Rapport Principal</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1400px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
        h2 { color: #34495e; margin-top: 30px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin: 20px 0; }
        .stat-card { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; text-align: center; }
        .stat-card h3 { margin: 0; font-size: 2.5em; }
        .stat-card p { margin: 5px 0 0 0; opacity: 0.9; }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th { background: #34495e; color: white; padding: 12px; text-align: left; font-weight: 600; }
        td { padding: 10px; border-bottom: 1px solid #ddd; }
        tr:hover { background: #f8f9fa; }
        .vlan-badge { display: inline-block; padding: 4px 12px; background: #3498db; color: white; border-radius: 12px; font-size: 0.9em; margin: 2px; }
        .dc-badge { display: inline-block; padding: 4px 12px; background: #27ae60; color: white; border-radius: 12px; font-size: 0.9em; margin: 2px; }
        .footer { margin-top: 30px; text-align: center; color: #7f8c8d; font-size: 0.9em; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Topologie Réseau - Rapport d'Analyse</h1>
        <p><strong>Date de génération:</strong> $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')</p>
        
        <div class="stats">
            <div class="stat-card">
                <h3>$($Topology.Statistics.TotalVLANs)</h3>
                <p>VLANs</p>
            </div>
            <div class="stat-card" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);">
                <h3>$($Topology.Statistics.TotalHosts)</h3>
                <p>Hôtes Total</p>
            </div>
            <div class="stat-card" style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);">
                <h3>$($Topology.Statistics.TotalDatacenters)</h3>
                <p>Datacenters</p>
            </div>
        </div>
        
        <h2>📡 VLANs Configurés</h2>
        <table>
            <thead>
                <tr>
                    <th>VLAN ID</th>
                    <th>Nom</th>
                    <th>Réseau</th>
                    <th>Hôtes</th>
                    <th>Datacenters</th>
                </tr>
            </thead>
            <tbody>
"@
    
    foreach ($vlan in $Topology.VLANs | Sort-Object VLAN_ID) {
        $dcBadges = ($vlan.Datacenters | ForEach-Object { "<span class='dc-badge'>$_</span>" }) -join " "
        $html += @"
                <tr>
                    <td><span class="vlan-badge">$($vlan.VLAN_ID)</span></td>
                    <td><strong>$($vlan.Name)</strong></td>
                    <td>$($vlan.Network)</td>
                    <td>$($vlan.HostCount)</td>
                    <td>$dcBadges</td>
                </tr>
"@
    }
    
    $html += @"
            </tbody>
        </table>
        
        <h2>🏢 Datacenters</h2>
        <table>
            <thead>
                <tr>
                    <th>Datacenter</th>
                    <th>Nombre de VLANs</th>
                    <th>Nombre d'Hôtes</th>
                </tr>
            </thead>
            <tbody>
"@
    
    foreach ($dc in $Topology.Datacenters.GetEnumerator() | Sort-Object Name) {
        $html += @"
                <tr>
                    <td><strong>$($dc.Value.Name)</strong></td>
                    <td>$($dc.Value.VLANs.Count)</td>
                    <td>$($dc.Value.HostCount)</td>
                </tr>
"@
    }
    
    $html += @"
            </tbody>
        </table>
        
        <div class="footer">
            <p>Généré par le script PowerShell de Topologie Réseau</p>
        </div>
    </div>
</body>
</html>
"@
    
    $html | Out-File -FilePath $Path -Encoding UTF8
}

function Export-StatisticsReport {
    param(
        [hashtable]$Topology,
        [string]$Path
    )
    
    $stats = @()
    $stats += [PSCustomObject]@{
        Métrique = "Total VLANs"
        Valeur = $Topology.Statistics.TotalVLANs
    }
    $stats += [PSCustomObject]@{
        Métrique = "Total Hôtes"
        Valeur = $Topology.Statistics.TotalHosts
    }
    $stats += [PSCustomObject]@{
        Métrique = "Total Datacenters"
        Valeur = $Topology.Statistics.TotalDatacenters
    }
    $stats += [PSCustomObject]@{
        Métrique = "Moyenne Hôtes/VLAN"
        Valeur = [math]::Round($Topology.Statistics.TotalHosts / $Topology.Statistics.TotalVLANs, 2)
    }
    
    $stats | Export-Csv -Path $Path -NoTypeInformation -Encoding UTF8
}

function Export-VlanReport {
    param(
        [hashtable]$Topology,
        [string]$Path
    )
    
    $vlanReport = foreach ($vlan in $Topology.VLANs) {
        [PSCustomObject]@{
            VLAN_ID = $vlan.VLAN_ID
            Nom = $vlan.Name
            Description = $vlan.Description
            Réseau = $vlan.Network
            NombreHôtes = $vlan.HostCount
            Datacenters = ($vlan.Datacenters -join "; ")
        }
    }
    
    $vlanReport | Export-Csv -Path $Path -NoTypeInformation -Encoding UTF8
}

function Export-DatacenterReport {
    param(
        [hashtable]$Topology,
        [string]$Path
    )
    
    $dcReport = foreach ($dc in $Topology.Datacenters.GetEnumerator()) {
        [PSCustomObject]@{
            Datacenter = $dc.Value.Name
            NombreVLANs = $dc.Value.VLANs.Count
            NombreHôtes = $dc.Value.HostCount
            VLANs = ($dc.Value.VLANs -join "; ")
        }
    }
    
    $dcReport | Export-Csv -Path $Path -NoTypeInformation -Encoding UTF8
}

#endregion

#region Script Principal

try {
    Write-Log "=" * 80
    Write-Log "GÉNÉRATION DE LA TOPOLOGIE RÉSEAU" -Level Success
    Write-Log "=" * 80
    
    # Import des données
    $vlanList = Import-VlanList -Path $VlanListPath
    $vlanDetails = Import-VlanDetails -FolderPath $VlanDetailsFolder -VlanList $vlanList
    
    # Construction de la topologie
    $topology = Build-NetworkTopology -VlanList $vlanList -VlanDetails $vlanDetails
    
    # Export des rapports
    Export-TopologyReports -Topology $topology -OutputPath $OutputPath
    
    Write-Log "=" * 80
    Write-Log "✓ TRAITEMENT TERMINÉ AVEC SUCCÈS" -Level Success
    Write-Log "=" * 80
    Write-Log "Rapports disponibles dans: $OutputPath"
    
    # Ouverture du rapport principal
    $mainReport = Join-Path $OutputPath "TopologieReseau_Principale.html"
    if (Test-Path $mainReport) {
        Start-Process $mainReport
    }
}
catch {
    Write-Log "❌ ERREUR CRITIQUE: $_" -Level Error
    Write-Log $_.ScriptStackTrace -Level Error
    exit 1
}

#endregion
