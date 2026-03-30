<#
.SYNOPSIS
    Orchestrateur principal du VDI OpsKit.
    Lance les modules de collecte AD, MECM et Réseau puis génère le fichier Excel.
.DESCRIPTION
    Chaque module est appelé en dot-sourcing et retourne une hashtable.
    Les données sont consolidées dans un JSON global puis passées au générateur Excel (Python).
.PARAMETER SMSProvider
    FQDN ou IP du SMS Provider MECM (obligatoire si le module MECM est activé).
    Exemple : "mecm-srv01.corp.local"
.PARAMETER SiteCode
    Code site MECM (ex: "P01"). Auto-détecté depuis le SMS Provider si omis.
.PARAMETER DNSServers
    Liste de serveurs DNS à interroger pour le module réseau.
    Si omis : auto-détecté depuis la configuration NIC locale.
.PARAMETER DHCPServers
    Liste de serveurs DHCP à interroger pour le module réseau.
    Si omis : auto-détecté depuis AD (ADSI).
.PARAMETER MECMCredential
    PSCredential pour la connexion CIM au SMS Provider.
    Si omis : compte Windows courant (pass-through Kerberos).
.PARAMETER SkipModules
    Modules à ignorer. Valeurs possibles : "AD", "MECM", "Network"
    Exemple : -SkipModules "MECM" pour ne lancer que AD et Network.
.PARAMETER NoExcel
    Ne génère pas le fichier Excel (JSON uniquement).
.EXAMPLE
    # Collecte complète
    .\00_Invoke-VDI-Inventory.ps1 -SMSProvider "mecm-srv01.corp.local"

.EXAMPLE
    # Sans MECM
    .\00_Invoke-VDI-Inventory.ps1 -SkipModules "MECM"

.EXAMPLE
    # Avec credentials MECM explicites et code site
    $cred = Get-Credential
    .\00_Invoke-VDI-Inventory.ps1 -SMSProvider "mecm-srv01.corp.local" -SiteCode "P01" -MECMCredential $cred

.EXAMPLE
    # JSON uniquement, pas d'Excel
    .\00_Invoke-VDI-Inventory.ps1 -SMSProvider "mecm-srv01.corp.local" -NoExcel

.NOTES
    Prérequis : Python 3 + openpyxl installés pour la génération Excel.
                pip install openpyxl
    Droits minimum :
      - AD      : utilisateur du domaine (lecture LDAP anonyme ou authentifiée)
      - MECM    : rôle "Read-Only Analyst" sur le site MECM
      - Réseau  : accès WMI/WinRM aux serveurs DNS et DHCP (optionnel)
#>

[CmdletBinding()]
param(
    [string]   $SMSProvider,
    [string]   $SiteCode,
    [string[]] $DNSServers     = @(),
    [string[]] $DHCPServers    = @(),
    [System.Management.Automation.PSCredential] $MECMCredential,
    [ValidateSet("AD", "MECM", "Network")]
    [string[]] $SkipModules    = @(),
    [switch]   $NoExcel
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$ScriptsPath = $PSScriptRoot
$OutputPath  = "$PSScriptRoot\output"
$StartTime   = Get-Date

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

function Write-Banner {
    param([string]$Text)
    $line = "─" * 56
    Write-Host ""
    Write-Host "  $line" -ForegroundColor Magenta
    Write-Host "  $Text" -ForegroundColor Magenta
    Write-Host "  $line" -ForegroundColor Magenta
    Write-Host ""
}

function Invoke-Module {
    param(
        [string]$Name,
        [string]$ScriptFile,
        [hashtable]$Params = @{}
    )
    Write-Log "Démarrage module : $Name" "TITLE"
    $t0 = Get-Date
    try {
        $result = & "$ScriptsPath\$ScriptFile" @Params
        $elapsed = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
        Write-Log "Module $Name terminé en ${elapsed}s" "OK"
        return $result
    } catch {
        Write-Log "Module $Name ÉCHEC : $_" "ERROR"
        return @{ Erreur = $_.Exception.Message }
    }
}

# ─────────────────────────────────────────
# VÉRIFICATIONS PRÉALABLES
# ─────────────────────────────────────────
Write-Banner "VDI OPS KIT — Collecte Infrastructure"
Write-Log "Machine         : $env:COMPUTERNAME"
Write-Log "Utilisateur     : $env:USERDOMAIN\$env:USERNAME"
Write-Log "Dossier scripts : $ScriptsPath"
Write-Log "Dossier sortie  : $OutputPath"
Write-Log "Modules actifs  : $(("AD","MECM","Network" | Where-Object { $_ -notin $SkipModules }) -join ', ')"
Write-Host ""

# Vérifie la présence des scripts enfants
$requiredScripts = @{
    "AD"      = "01_Collect-ADInfo.ps1"
    "MECM"    = "02_Collect-MECMInfo.ps1"
    "Network" = "05_Collect-NetworkInfo.ps1"
}
$missing = @()
foreach ($mod in $requiredScripts.Keys) {
    if ($mod -in $SkipModules) { continue }
    $path = "$ScriptsPath\$($requiredScripts[$mod])"
    if (-not (Test-Path $path)) {
        Write-Log "Script manquant : $path" "ERROR"
        $missing += $path
    }
}
if ($missing.Count -gt 0) {
    Write-Log "Arrêt : $($missing.Count) script(s) manquant(s)." "ERROR"
    return
}

# Vérifie que SMSProvider est fourni si MECM est actif
if ("MECM" -notin $SkipModules -and -not $SMSProvider) {
    Write-Log "MECM activé mais -SMSProvider non renseigné." "ERROR"
    Write-Log "Utilisez -SMSProvider 'mecm-srv01.corp.local' ou ajoutez -SkipModules 'MECM'" "WARN"
    return
}

# Crée le dossier output
if (-not (Test-Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
}

$AllData = [ordered]@{
    Meta = [ordered]@{
        DateCollecte  = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        Machine       = $env:COMPUTERNAME
        Utilisateur   = "$env:USERDOMAIN\$env:USERNAME"
        ModulesLances = @("AD","MECM","Network" | Where-Object { $_ -notin $SkipModules })
    }
}

# ═════════════════════════════════════════════════════
# MODULE 1 — ACTIVE DIRECTORY
# ═════════════════════════════════════════════════════
if ("AD" -notin $SkipModules) {
    $AllData["AD"] = Invoke-Module -Name "Active Directory" -ScriptFile "01_Collect-ADInfo.ps1"
}

# ═════════════════════════════════════════════════════
# MODULE 2 — MECM
# ═════════════════════════════════════════════════════
if ("MECM" -notin $SkipModules) {
    $mecmParams = @{ SMSProvider = $SMSProvider }
    if ($SiteCode)        { $mecmParams["SiteCode"]    = $SiteCode }
    if ($MECMCredential)  { $mecmParams["Credential"]  = $MECMCredential }

    $AllData["MECM"] = Invoke-Module -Name "MECM" -ScriptFile "02_Collect-MECMInfo.ps1" -Params $mecmParams
}

# ═════════════════════════════════════════════════════
# MODULE 3 — RÉSEAU
# ═════════════════════════════════════════════════════
if ("Network" -notin $SkipModules) {
    $netParams = @{}
    if ($DNSServers.Count  -gt 0) { $netParams["DNSServers"]  = $DNSServers }
    if ($DHCPServers.Count -gt 0) { $netParams["DHCPServers"] = $DHCPServers }

    $AllData["Network"] = Invoke-Module -Name "Réseau" -ScriptFile "05_Collect-NetworkInfo.ps1" -Params $netParams
}

# ═════════════════════════════════════════════════════
# EXPORT JSON GLOBAL
# ═════════════════════════════════════════════════════
Write-Banner "Export JSON"
$timestamp = Get-Date -Format "yyyyMMdd_HHmm"
$jsonFile  = "$OutputPath\VDI_Inventory_$timestamp.json"

try {
    $AllData | ConvertTo-Json -Depth 15 | Out-File $jsonFile -Encoding UTF8
    Write-Log "JSON global : $jsonFile" "OK"
} catch {
    Write-Log "Erreur export JSON : $_" "ERROR"
}

# ═════════════════════════════════════════════════════
# GÉNÉRATION EXCEL
# ═════════════════════════════════════════════════════
if (-not $NoExcel) {
    Write-Banner "Génération Excel"
    $xlsxFile    = "$OutputPath\VDI_OpsKit_$timestamp.xlsx"
    $pyScript    = "$ScriptsPath\06_Generate-Excel.py"

    # Détection Python
    $pythonExe = $null
    foreach ($candidate in @("python", "python3", "py")) {
        try {
            $ver = & $candidate --version 2>&1
            if ($ver -match "Python 3") {
                $pythonExe = $candidate
                Write-Log "Python détecté : $ver ($candidate)" "OK"
                break
            }
        } catch {}
    }

    if (-not $pythonExe) {
        Write-Log "Python 3 introuvable dans le PATH. Excel non généré." "WARN"
        Write-Log "Installez Python 3 et relancez, ou ouvrez le JSON manuellement." "WARN"
    } elseif (-not (Test-Path $pyScript)) {
        Write-Log "Script Python manquant : $pyScript" "WARN"
    } else {
        # Vérifie openpyxl
        $hasOpenpyxl = & $pythonExe -c "import openpyxl; print('ok')" 2>&1
        if ($hasOpenpyxl -ne "ok") {
            Write-Log "openpyxl manquant. Installation..." "WARN"
            & $pythonExe -m pip install openpyxl --quiet 2>&1 | Out-Null
        }

        try {
            & $pythonExe $pyScript --json $jsonFile --output $xlsxFile 2>&1 |
                ForEach-Object { Write-Log "  [Python] $_" }
            if (Test-Path $xlsxFile) {
                Write-Log "Excel généré : $xlsxFile" "OK"
            } else {
                Write-Log "Excel non trouvé après génération — vérifiez les logs Python ci-dessus" "WARN"
            }
        } catch {
            Write-Log "Erreur génération Excel : $_" "ERROR"
        }
    }
}

# ═════════════════════════════════════════════════════
# RÉSUMÉ FINAL
# ═════════════════════════════════════════════════════
$duration = (Get-Date) - $StartTime
Write-Banner "Collecte terminée"
Write-Log "Durée totale    : $([math]::Round($duration.TotalMinutes, 1)) min"
Write-Log "JSON            : $jsonFile" "OK"
if (-not $NoExcel -and (Test-Path "$OutputPath\VDI_OpsKit_$timestamp.xlsx")) {
    Write-Log "Excel           : $OutputPath\VDI_OpsKit_$timestamp.xlsx" "OK"
}
Write-Log "Dossier output  : $OutputPath"
Write-Host ""
