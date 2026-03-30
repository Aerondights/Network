<#
.SYNOPSIS
    Collecte les informations MECM via WMI/CIM distant sur le SMS Provider.
.DESCRIPTION
    Aucun module, aucune console MECM requise.
    Toutes les requêtes passent par CIM (root\SMS\site_<CODE>) depuis n'importe quelle
    machine du domaine ayant les droits "Read-Only Analyst" sur le site MECM.
.PARAMETER SMSProvider
    FQDN ou IP du SMS Provider (= Site Server par défaut, ou serveur dédié si Remote Provider).
    Si non renseigné, tentative d'auto-détection via DNS/AD.
.PARAMETER SiteCode
    Code du site MECM (ex: P01). Auto-détecté depuis le SMS Provider si non renseigné.
.PARAMETER Credential
    PSCredential si l'utilisateur courant n'a pas les droits WMI nécessaires.
    Par défaut : compte courant (pass-through Kerberos).
.EXAMPLE
    .\02_Collect-MECMInfo.ps1 -SMSProvider "mecm-srv01.corp.local"
.EXAMPLE
    .\02_Collect-MECMInfo.ps1 -SMSProvider "mecm-srv01.corp.local" -SiteCode "P01"
.EXAMPLE
    $cred = Get-Credential
    .\02_Collect-MECMInfo.ps1 -SMSProvider "mecm-srv01.corp.local" -Credential $cred
.NOTES
    Droits requis : rôle MECM "Read-Only Analyst" (scope All) sur le compte utilisé.
    Ports réseau  : 135/TCP (RPC Endpoint Mapper) + ports RPC dynamiques (49152-65535)
                    OU 5985/TCP si WinRM est activé sur le SMS Provider.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$SMSProvider,

    [string]$SiteCode,

    [System.Management.Automation.PSCredential]$Credential
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$OutputPath = "$PSScriptRoot\output"

# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $color = switch ($Level) {
        "ERROR" { "Red" }
        "WARN"  { "Yellow" }
        "OK"    { "Green" }
        "TITLE" { "Magenta" }
        default { "Cyan" }
    }
    Write-Host "[$ts][$Level] $Message" -ForegroundColor $color
}

# Requête CIM avec gestion d'erreur propre
function Invoke-SMSQuery {
    param(
        [string]$ClassName,
        [string]$Filter    = $null,
        [string]$Query     = $null,   # WQL complet si Filter ne suffit pas
        [string[]]$Select  = $null,   # propriétés à récupérer (perf)
        [string]$Namespace = $script:SMSNamespace
    )
    try {
        $params = @{
            CimSession = $script:CimSession
            Namespace  = $Namespace
        }
        if ($Query) {
            $params["Query"] = $Query
        } else {
            $params["ClassName"] = $ClassName
            if ($Filter) { $params["Filter"] = $Filter }
            if ($Select) { $params["Property"] = $Select }
        }
        return Get-CimInstance @params -ErrorAction Stop
    } catch {
        Write-Log "CIM [$ClassName] : $($_.Exception.Message)" "ERROR"
        return @()
    }
}

# Convertit une date WMI (string YYYYMMDDHHMMSS.000000+000) en date lisible
function Convert-WMIDate {
    param([string]$WMIDate)
    if (-not $WMIDate -or $WMIDate -eq "00000000000000.000000+000") { return "" }
    try { return [Management.ManagementDateTimeConverter]::ToDateTime($WMIDate).ToString("yyyy-MM-dd HH:mm") }
    catch { return $WMIDate }
}

# Convertit un nombre de secondes en "Xh Ymin"
function Format-Duration {
    param([int]$Seconds)
    if ($Seconds -le 0) { return "0 min" }
    $h   = [Math]::Floor($Seconds / 3600)
    $min = [Math]::Floor(($Seconds % 3600) / 60)
    if ($h -gt 0) { return "${h}h ${min}min" } else { return "${min}min" }
}

# ─────────────────────────────────────────
# CONNEXION CIM
# ─────────────────────────────────────────
Write-Log "=== COLLECTE MECM VIA CIM/WMI ===" "TITLE"
Write-Log "SMS Provider cible : $SMSProvider"

# Options de session CIM — on tente WSMAN (WinRM) puis DCOM (RPC classique)
$cimOpts = [Microsoft.Management.Infrastructure.Options.DComSessionOptions]::new()

$sessionParams = @{ ComputerName = $SMSProvider; SessionOption = $cimOpts }
if ($Credential) { $sessionParams["Credential"] = $Credential }

try {
    $script:CimSession = New-CimSession @sessionParams -ErrorAction Stop
    Write-Log "Session CIM établie sur $SMSProvider (DCOM)" "OK"
} catch {
    Write-Log "DCOM échoué, tentative WinRM... ($_)" "WARN"
    try {
        $wsmanOpts = [Microsoft.Management.Infrastructure.Options.WSManSessionOptions]::new()
        $sessionParams["SessionOption"] = $wsmanOpts
        $script:CimSession = New-CimSession @sessionParams -ErrorAction Stop
        Write-Log "Session CIM établie sur $SMSProvider (WinRM)" "OK"
    } catch {
        Write-Log "Impossible d'établir une session CIM sur $SMSProvider : $_" "ERROR"
        Write-Log "Vérifiez : résolution DNS, ports 135+RPC ou 5985, droits WMI" "WARN"
        return
    }
}

# Auto-détection du Site Code
if (-not $SiteCode) {
    try {
        $siteObj = Get-CimInstance -CimSession $script:CimSession `
            -Namespace "root\SMS" -ClassName "SMS_ProviderLocation" -ErrorAction Stop |
            Select-Object -First 1
        $SiteCode = $siteObj.SiteCode
        Write-Log "Site Code détecté automatiquement : $SiteCode" "OK"
    } catch {
        Write-Log "Impossible de détecter le Site Code. Spécifiez -SiteCode." "ERROR"
        Remove-CimSession $script:CimSession -ErrorAction SilentlyContinue
        return
    }
}

$script:SMSNamespace = "root\SMS\site_$SiteCode"
Write-Log "Namespace WMI : $($script:SMSNamespace)"

$MECMData = [ordered]@{}

# ═════════════════════════════════════════════════════
# 1. INFORMATIONS SITE
# ═════════════════════════════════════════════════════
Write-Log "1/9 — Informations site..."
try {
    $site = Invoke-SMSQuery -ClassName "SMS_Site" -Filter "SiteCode='$SiteCode'" | Select-Object -First 1

    # Version complète depuis SMS_SCI_SiteDefinition
    $siteDef = Invoke-SMSQuery -Query "SELECT * FROM SMS_SCI_SiteDefinition WHERE SiteCode='$SiteCode'" |
        Select-Object -First 1

    $MECMData["Site"] = [ordered]@{
        SiteCode      = $site.SiteCode
        NomSite       = $site.SiteName
        Version       = $site.Version
        SMSProvider   = $SMSProvider
        TypeSite      = switch ($site.Type) {
            2 { "Primaire" }; 1 { "Administration Centrale (CAS)" }
            4 { "Secondaire" }; default { "Inconnu ($($site.Type))" }
        }
        SiteParent    = $site.ReportingSiteCode
        SQLServer     = $siteDef.SQLDatabaseName
        InstallDir    = $site.InstallDir
        DateInstall   = Convert-WMIDate $site.InstallDate
    }
    Write-Log "Site : $($site.SiteName)  |  Version : $($site.Version)" "OK"
} catch {
    Write-Log "Erreur site : $_" "ERROR"
    $MECMData["Site"] = @{ Erreur = $_.Exception.Message }
}

# ═════════════════════════════════════════════════════
# 2. RÔLES SYSTÈME DE SITE
# ═════════════════════════════════════════════════════
Write-Log "2/9 — Rôles système de site..."
try {
    # SMS_SiteSystemSummarizer : un enregistrement par serveur/rôle
    $roleSummary = Invoke-SMSQuery -ClassName "SMS_SiteSystemSummarizer" `
        -Select @("SiteSystem","Role","SiteCode","Status","AvailabilityState","DownSince","ObjectCount")

    # On regroupe les rôles par serveur
    $rolesBySrv = @{}
    foreach ($r in $roleSummary) {
        $srv = $r.SiteSystem -replace "\\\\|\\", "" -replace '^\[.*?\]', '' # nettoie le chemin UNC
        $srv = $srv.Trim('\')
        if (-not $rolesBySrv.ContainsKey($srv)) {
            $rolesBySrv[$srv] = @{ Roles = @(); Status = @(); DownSince = @() }
        }
        $rolesBySrv[$srv].Roles    += $r.Role
        $rolesBySrv[$srv].Status   += switch ($r.Status) { 0 {"OK"} 1 {"Warning"} 2 {"Critical"} default {$r.Status} }
        if ($r.DownSince) { $rolesBySrv[$srv].DownSince += Convert-WMIDate $r.DownSince }
    }

    $Roles = foreach ($srv in $rolesBySrv.Keys | Sort-Object) {
        $info = $rolesBySrv[$srv]
        # Statut global = pire des statuts
        $worstStatus = if ($info.Status -contains "Critical") { "Critical" } `
                       elseif ($info.Status -contains "Warning") { "Warning" } `
                       else { "OK" }
        [ordered]@{
            Serveur   = $srv
            Roles     = ($info.Roles | Sort-Object -Unique) -join " | "
            Statut    = $worstStatus
            DownSince = ($info.DownSince | Where-Object { $_ } | Select-Object -First 1)
        }
    }

    $MECMData["RolesSite"] = @($Roles)
    Write-Log "$(@($Roles).Count) serveur(s) de site" "OK"
} catch {
    Write-Log "Erreur rôles site : $_" "ERROR"
    $MECMData["RolesSite"] = @()
}

# ═════════════════════════════════════════════════════
# 3. DISTRIBUTION POINTS
# ═════════════════════════════════════════════════════
Write-Log "3/9 — Distribution Points..."
try {
    $DPs = Invoke-SMSQuery -ClassName "SMS_DistributionPointInfo" `
        -Select @("ServerName","SiteCode","IsPXE","IsMulticast","PreStagingAllowed",
                  "TransferRate","DPType","IsProtected","GroupCount","PackageCount",
                  "LastUpdateTime")

    # Groupes DP
    $dpGroups = Invoke-SMSQuery -ClassName "SMS_DPGroupInfo" `
        -Select @("Name","Description","MemberCount","PackageCount","GroupID")

    $MECMData["DistributionPoints"] = @($DPs | ForEach-Object {
        [ordered]@{
            Serveur        = $_.ServerName
            SiteCode       = $_.SiteCode
            PXE            = if ($_.IsPXE) { "Oui" } else { "Non" }
            Multicast      = if ($_.IsMulticast) { "Oui" } else { "Non" }
            PreStaging     = if ($_.PreStagingAllowed) { "Oui" } else { "Non" }
            NbPackages     = $_.PackageCount
            NbGroupes      = $_.GroupCount
            DernierMAJ     = Convert-WMIDate $_.LastUpdateTime
        }
    })

    $MECMData["GroupesDP"] = @($dpGroups | ForEach-Object {
        [ordered]@{
            Nom         = $_.Name
            Description = $_.Description
            NbDPs       = $_.MemberCount
            NbPackages  = $_.PackageCount
            ID          = $_.GroupID
        }
    })

    Write-Log "$($DPs.Count) DP(s)  |  $($dpGroups.Count) groupe(s) DP" "OK"
} catch {
    Write-Log "Erreur DPs : $_" "ERROR"
    $MECMData["DistributionPoints"] = @()
}

# ═════════════════════════════════════════════════════
# 4. BOUNDARY GROUPS & BOUNDARIES
# ═════════════════════════════════════════════════════
Write-Log "4/9 — Boundary Groups & Boundaries..."
try {
    $bgResults = Invoke-SMSQuery -ClassName "SMS_BoundaryGroup" `
        -Select @("GroupID","Name","Description","MemberCount","DefaultSiteCode","CreatedBy")

    $bResults = Invoke-SMSQuery -ClassName "SMS_Boundary" `
        -Select @("BoundaryID","DisplayName","BoundaryType","Value","GroupCount","SiteSystems")

    $MECMData["BoundaryGroups"] = @($bgResults | ForEach-Object {
        [ordered]@{
            Nom         = $_.Name
            Description = $_.Description
            SiteDefaut  = $_.DefaultSiteCode
            NbBoundaries= $_.MemberCount
            CreeePar    = $_.CreatedBy
            ID          = $_.GroupID
        }
    })

    $MECMData["Boundaries"] = @($bResults | ForEach-Object {
        $type = switch ($_.BoundaryType) {
            0 { "Sous-réseau IP" }
            1 { "Site AD" }
            2 { "Plage IPv6" }
            3 { "Plage IPv4" }
            4 { "VPN" }
            default { "Inconnu ($($_.BoundaryType))" }
        }
        [ordered]@{
            Nom        = $_.DisplayName
            Type       = $type
            Valeur     = $_.Value
            NbGroupes  = $_.GroupCount
            SiteSystems= ($_.SiteSystems -join " | ")
        }
    })

    Write-Log "$($bgResults.Count) groupe(s)  |  $($bResults.Count) boundary/ies" "OK"
} catch {
    Write-Log "Erreur Boundaries : $_" "ERROR"
    $MECMData["BoundaryGroups"] = @()
    $MECMData["Boundaries"]     = @()
}

# ═════════════════════════════════════════════════════
# 5. COLLECTIONS (Device)
# ═════════════════════════════════════════════════════
Write-Log "5/9 — Collections Device..."
try {
    # On récupère toutes les Device Collections
    $collections = Invoke-SMSQuery -Query "
        SELECT CollectionID, Name, Comment, MemberCount, LimitToCollectionName,
               RefreshType, LastChangeTime, LastMemberChangeTime,
               CollectionRules, IsBuiltIn
        FROM SMS_Collection
        WHERE CollectionType = 2
    "

    $MECMData["Collections"] = @($collections | Sort-Object Name | ForEach-Object {
        $refreshType = switch ($_.RefreshType) {
            1 { "Manuel" }
            2 { "Périodique" }
            4 { "Incrémental" }
            6 { "Périodique + Incrémental" }
            default { $_.RefreshType }
        }
        # Règles de collection : on extrait le type de chaque règle
        $regles = @()
        if ($_.CollectionRules) {
            foreach ($rule in $_.CollectionRules) {
                $ruleType = $rule.CimClass.CimClassName
                $ruleType = $ruleType -replace "SMS_CollectionRule", ""
                $regles += "$ruleType : $($rule.RuleName)"
            }
        }
        [ordered]@{
            Nom             = $_.Name
            ID              = $_.CollectionID
            Commentaire     = $_.Comment
            NbMembres       = $_.MemberCount
            LimiteePar      = $_.LimitToCollectionName
            Actualisation   = $refreshType
            DernierRefresh  = Convert-WMIDate $_.LastMemberChangeTime
            DernierChange   = Convert-WMIDate $_.LastChangeTime
            Builtin         = if ($_.IsBuiltIn) { "Oui" } else { "Non" }
            Regles          = $regles -join " | "
        }
    })

    Write-Log "$($collections.Count) collection(s) Device" "OK"
} catch {
    Write-Log "Erreur Collections : $_" "ERROR"
    $MECMData["Collections"] = @()
}

# ═════════════════════════════════════════════════════
# 6. TASK SEQUENCES
# ═════════════════════════════════════════════════════
Write-Log "6/9 — Task Sequences..."
try {
    $TSList = Invoke-SMSQuery -ClassName "SMS_TaskSequencePackage" `
        -Select @("PackageID","Name","Description","Version","BootImageID",
                  "LastRefreshTime","SourceDate","ProgramFlags","ImageOSVersion",
                  "SourceSite")

    # Déploiements de TS
    $tsDeploys = Invoke-SMSQuery -Query "
        SELECT PackageID, CollectionName, CollectionID, DeploymentTime,
               DeploymentIntent, AdvertFlags
        FROM SMS_Advertisement
        WHERE PackageID LIKE 'TST%' OR AdvertFlags = 8388608
    "
    # Index PackageID -> collections déployées
    $tsDeployMap = @{}
    foreach ($d in $tsDeploys) {
        if (-not $tsDeployMap.ContainsKey($d.PackageID)) { $tsDeployMap[$d.PackageID] = @() }
        $intent = switch ($d.DeploymentIntent) { 1 {"Requis"} 2 {"Disponible"} default {$d.DeploymentIntent} }
        $tsDeployMap[$d.PackageID] += "$($d.CollectionName) [$intent]"
    }

    $MECMData["TaskSequences"] = @($TSList | Sort-Object Name | ForEach-Object {
        [ordered]@{
            Nom            = $_.Name
            PackageID      = $_.PackageID
            Description    = $_.Description
            Version        = $_.Version
            BootImageID    = $_.BootImageID
            VersionOS      = $_.ImageOSVersion
            DernierRefresh = Convert-WMIDate $_.LastRefreshTime
            DateSource     = Convert-WMIDate $_.SourceDate
            SiteSource     = $_.SourceSite
            Deploiements   = if ($tsDeployMap.ContainsKey($_.PackageID)) {
                                 $tsDeployMap[$_.PackageID] -join " | "
                             } else { "Aucun" }
        }
    })

    Write-Log "$($TSList.Count) Task Sequence(s)" "OK"
} catch {
    Write-Log "Erreur Task Sequences : $_" "ERROR"
    $MECMData["TaskSequences"] = @()
}

# ═════════════════════════════════════════════════════
# 7. BOOT IMAGES
# ═════════════════════════════════════════════════════
Write-Log "7/9 — Boot Images (WinPE)..."
try {
    $bootImages = Invoke-SMSQuery -ClassName "SMS_BootImagePackage" `
        -Select @("PackageID","Name","Version","Description","ImageSize",
                  "SourceDate","LastRefreshTime","Architecture","PkgFlags")

    $MECMData["BootImages"] = @($bootImages | Sort-Object Name | ForEach-Object {
        [ordered]@{
            Nom            = $_.Name
            PackageID      = $_.PackageID
            Version        = $_.Version
            Description    = $_.Description
            Architecture   = switch ($_.Architecture) { 0 {"x86"} 9 {"x64"} default {$_.Architecture} }
            TailleMo       = [Math]::Round($_.ImageSize / 1MB, 1)
            DernierRefresh = Convert-WMIDate $_.LastRefreshTime
            DateSource     = Convert-WMIDate $_.SourceDate
        }
    })

    Write-Log "$($bootImages.Count) Boot Image(s)" "OK"
} catch {
    Write-Log "Erreur Boot Images : $_" "ERROR"
    $MECMData["BootImages"] = @()
}

# ═════════════════════════════════════════════════════
# 8. SOFTWARE UPDATE GROUPS (SUG)
# ═════════════════════════════════════════════════════
Write-Log "8/9 — Software Update Groups..."
try {
    $SUGs = Invoke-SMSQuery -ClassName "SMS_AuthorizationList" `
        -Select @("CI_ID","LocalizedDisplayName","NumberOfExpiredUpdates",
                  "NumberOfUpdates","DateCreated","DateLastModified",
                  "IsExpired","LocalizedDescription","SourceSite")

    $MECMData["SoftwareUpdateGroups"] = @($SUGs | Sort-Object LocalizedDisplayName | ForEach-Object {
        [ordered]@{
            Nom                 = $_.LocalizedDisplayName
            Description         = $_.LocalizedDescription
            NbMisesAJour        = $_.NumberOfUpdates
            NbExpirees          = $_.NumberOfExpiredUpdates
            Expire              = if ($_.IsExpired) { "Oui" } else { "Non" }
            DateCreation        = Convert-WMIDate $_.DateCreated
            DernierModification = Convert-WMIDate $_.DateLastModified
            SiteSource          = $_.SourceSite
            CI_ID               = $_.CI_ID
        }
    })

    Write-Log "$($SUGs.Count) Software Update Group(s)" "OK"
} catch {
    Write-Log "Erreur SUGs : $_" "ERROR"
    $MECMData["SoftwareUpdateGroups"] = @()
}

# ═════════════════════════════════════════════════════
# 9. MAINTENANCE WINDOWS
# ═════════════════════════════════════════════════════
Write-Log "9/9 — Fenêtres de maintenance..."
try {
    # SMS_ServiceWindow : liées aux collections via CollectionID
    $MWs = Invoke-SMSQuery -ClassName "SMS_ServiceWindow" `
        -Select @("SWD","ServiceWindowID","Name","IsEnabled",
                  "Duration","ServiceWindowType","CollectionID",
                  "RecurrenceType","StartTime","IsGMT")

    # Résolution des noms de collections
    $colNames = @{}
    foreach ($col in (Invoke-SMSQuery -ClassName "SMS_Collection" `
        -Select @("CollectionID","Name") `
        -Query "SELECT CollectionID, Name FROM SMS_Collection WHERE CollectionType = 2")) {
        $colNames[$col.CollectionID] = $col.Name
    }

    $MECMData["MaintenanceWindows"] = @($MWs | Sort-Object Name | ForEach-Object {
        $colName = if ($colNames.ContainsKey($_.CollectionID)) { $colNames[$_.CollectionID] } else { $_.CollectionID }
        $swType  = switch ($_.ServiceWindowType) {
            1 { "Général" }
            4 { "Software Updates" }
            5 { "OSD" }
            default { "Type $($_.ServiceWindowType)" }
        }
        $recType = switch ($_.RecurrenceType) {
            1 { "Aucune" }
            2 { "Quotidienne" }
            3 { "Hebdomadaire" }
            4 { "Mensuelle (jour)" }
            5 { "Mensuelle (semaine)" }
            default { "Type $($_.RecurrenceType)" }
        }
        [ordered]@{
            Nom          = $_.Name
            Collection   = $colName
            Activee      = if ($_.IsEnabled) { "Oui" } else { "Non" }
            Type         = $swType
            Duree        = Format-Duration $_.Duration
            Recurrence   = $recType
            Debut        = Convert-WMIDate $_.StartTime
            GMT          = if ($_.IsGMT) { "UTC" } else { "Heure locale" }
        }
    })

    Write-Log "$($MWs.Count) Maintenance Window(s)" "OK"
} catch {
    Write-Log "Erreur Maintenance Windows : $_" "ERROR"
    $MECMData["MaintenanceWindows"] = @()
}

# ─────────────────────────────────────────
# FERMETURE SESSION CIM
# ─────────────────────────────────────────
Remove-CimSession $script:CimSession -ErrorAction SilentlyContinue
Write-Log "Session CIM fermée" "OK"

# ─────────────────────────────────────────
# EXPORT JSON
# ─────────────────────────────────────────
if (-not (Test-Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
}

$jsonFile = "$OutputPath\MECM_Info.json"
$MECMData | ConvertTo-Json -Depth 10 | Out-File $jsonFile -Encoding UTF8
Write-Log "Export JSON : $jsonFile" "OK"
Write-Log "=== COLLECTE MECM TERMINÉE ===" "TITLE"

return $MECMData
