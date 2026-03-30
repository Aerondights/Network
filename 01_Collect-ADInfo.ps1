<#
.SYNOPSIS
    Collecte les informations Active Directory sans le module ActiveDirectory.
.DESCRIPTION
    Utilise ADSI (.NET DirectoryServices) et les classes System.DirectoryServices.ActiveDirectory.
    Collecte : Domaine & Forêt, Contrôleurs de domaine, Sites & Subnets, GPOs, PSOs, Groupes locaux.
.OUTPUTS
    Hashtable exportée en JSON dans $PSScriptRoot\output\
.NOTES
    Aucun module PowerShell requis. Nécessite d'être exécuté sur une machine jointe au domaine.
#>

[CmdletBinding()]
param()

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

# Convertit un intervalle AD (entier 64 bits négatif en 100-nanosecondes) en jours
function Convert-ADInterval {
    param([object]$Value)
    try {
        if ($null -eq $Value -or $Value -eq 0 -or $Value -eq [Int64]::MinValue) { return "Illimité" }
        $ticks = [Math]::Abs([Int64]$Value)
        return [Math]::Round($ticks / 864000000000, 1)
    } catch { return "N/A" }
}

# Convertit un FileTime AD (Int64) en date lisible
function Convert-ADFileTime {
    param([object]$Value)
    try {
        $ft = [Int64]$Value
        if ($ft -le 0 -or $ft -eq [Int64]::MaxValue) { return "Jamais" }
        return [DateTime]::FromFileTime($ft).ToString("yyyy-MM-dd HH:mm")
    } catch { return "N/A" }
}

# Requête LDAP — retourne une collection DirectorySearcher.FindAll()
function Invoke-LDAPSearch {
    param(
        [string]$Filter,
        [string[]]$Properties,
        [string]$SearchRoot  = $null,
        [System.DirectoryServices.SearchScope]$Scope = "Subtree",
        [int]$PageSize = 500
    )
    try {
        $root = if ($SearchRoot) { [ADSI]"LDAP://$SearchRoot" } else { [ADSI]"" }
        $searcher                = [System.DirectoryServices.DirectorySearcher]::new($root)
        $searcher.Filter         = $Filter
        $searcher.PageSize       = $PageSize
        $searcher.SearchScope    = $Scope
        $searcher.SizeLimit      = 0
        foreach ($p in $Properties) { [void]$searcher.PropertiesToLoad.Add($p) }
        return $searcher.FindAll()
    } catch {
        Write-Log "LDAPSearch [$Filter] : $_" "ERROR"
        return @()
    }
}

# Lit une propriété scalaire depuis un SearchResult
function Get-LDAPProp {
    param(
        [System.DirectoryServices.SearchResult]$Entry,
        [string]$Property,
        $Default = ""
    )
    try {
        $val = $Entry.Properties[$Property]
        if ($null -eq $val -or $val.Count -eq 0) { return $Default }
        return $val[0]
    } catch { return $Default }
}

Write-Log "=== COLLECTE ACTIVE DIRECTORY (ADSI) ===" "TITLE"

$ADData = [ordered]@{}

# ═════════════════════════════════════════════════════
# 1. DOMAINE & FORÊT
# ═════════════════════════════════════════════════════
Write-Log "1/6 — Domaine & Forêt..."
try {
    $domainCtx = [System.DirectoryServices.ActiveDirectory.DirectoryContext]::new(
        [System.DirectoryServices.ActiveDirectory.DirectoryContextType]::Domain
    )
    $domain = [System.DirectoryServices.ActiveDirectory.Domain]::GetDomain($domainCtx)
    $forest = $domain.Forest

    $rootDSE   = [ADSI]"LDAP://RootDSE"
    $defaultNC = $rootDSE.Properties["defaultNamingContext"][0]
    $schemaNC  = $rootDSE.Properties["schemaNamingContext"][0]
    $configNC  = $rootDSE.Properties["configurationNamingContext"][0]

    # Niveau fonctionnel domaine (msDS-Behavior-Version sur le NC racine)
    $domainObj  = [ADSI]"LDAP://$defaultNC"
    $domainMode = switch ($domainObj.Properties["msDS-Behavior-Version"][0]) {
        0 { "Windows 2000 Mixed" }
        1 { "Windows 2003 Interim" }
        2 { "Windows 2003" }
        3 { "Windows 2008" }
        4 { "Windows 2008 R2" }
        5 { "Windows 2012" }
        6 { "Windows 2012 R2" }
        7 { "Windows 2016" }
        default { "Windows 2019/2022+" }
    }

    # Niveau fonctionnel forêt (sur CN=Partitions)
    $partitions = [ADSI]"LDAP://CN=Partitions,$configNC"
    $forestMode = switch ($partitions.Properties["msDS-Behavior-Version"][0]) {
        0 { "Windows 2000" }
        1 { "Windows 2003 Interim" }
        2 { "Windows 2003" }
        3 { "Windows 2008" }
        4 { "Windows 2008 R2" }
        5 { "Windows 2012" }
        6 { "Windows 2012 R2" }
        7 { "Windows 2016" }
        default { "Windows 2019/2022+" }
    }

    # SID du domaine
    $sidBytes = $domainObj.Properties["objectSid"][0]
    $sid      = (New-Object System.Security.Principal.SecurityIdentifier($sidBytes, 0)).Value

    $ADData["Domaine"] = [ordered]@{
        NomDNS               = $domain.Name
        NomNetBIOS           = $domainObj.Properties["name"][0]
        NiveauFonctionnel    = $domainMode
        DistinguishedName    = $defaultNC
        SID                  = $sid
        Forest               = $forest.Name
        NiveauForet          = $forestMode
        ConfigNamingContext  = $configNC
        SchemaNamingContext  = $schemaNC
        PDCEmulator          = $domain.PdcRoleOwner.Name
        RIDMaster            = $domain.RidRoleOwner.Name
        InfrastructureMaster = $domain.InfrastructureRoleOwner.Name
        SchemaMaster         = $forest.SchemaRoleOwner.Name
        NamingMaster         = $forest.NamingRoleOwner.Name
    }
    Write-Log "Domaine : $($domain.Name)  |  Forêt : $($forest.Name)" "OK"
} catch {
    Write-Log "Erreur domaine/forêt : $_" "ERROR"
    $ADData["Domaine"] = @{ Erreur = $_.Exception.Message }
}

# ═════════════════════════════════════════════════════
# 2. CONTRÔLEURS DE DOMAINE
# ═════════════════════════════════════════════════════
Write-Log "2/6 — Contrôleurs de domaine..."
try {
    $defaultNC = ([ADSI]"LDAP://RootDSE").Properties["defaultNamingContext"][0]
    $configNC  = ([ADSI]"LDAP://RootDSE").Properties["configurationNamingContext"][0]

    # Tous les comptes computer avec le flag DC (userAccountControl bit 13 = SERVER_TRUST_ACCOUNT)
    $dcResults = Invoke-LDAPSearch `
        -Filter "(&(objectClass=computer)(userAccountControl:1.2.840.113556.1.4.803:=8192))" `
        -Properties @("name","dNSHostName","operatingSystem","operatingSystemVersion",
                      "whenCreated","distinguishedName","lastLogonTimestamp","userAccountControl")

    # Résolution des rôles FSMO via fSMORoleOwner sur chaque partition/container
    $fsmoMap = @{}
    $fsmoChecks = @(
        @{ Path = "LDAP://$defaultNC";                              Role = "PDC Emulator" }
        @{ Path = "LDAP://CN=RID Manager`$,CN=System,$defaultNC";  Role = "RID Master" }
        @{ Path = "LDAP://CN=Infrastructure,$defaultNC";            Role = "Infrastructure Master" }
        @{ Path = "LDAP://CN=Schema,$configNC";                     Role = "Schema Master" }
        @{ Path = "LDAP://CN=Partitions,$configNC";                 Role = "Domain Naming Master" }
    )
    foreach ($f in $fsmoChecks) {
        try {
            $obj    = [ADSI]$f.Path
            $owner  = $obj.Properties["fSMORoleOwner"][0]
            # Le fSMORoleOwner pointe vers CN=NTDS Settings,CN=<NomDC>,CN=Servers,...
            $dcName = ([regex]::Match($owner, "CN=NTDS Settings,CN=([^,]+),")).Groups[1].Value
            if ($dcName) {
                if (-not $fsmoMap.ContainsKey($dcName)) { $fsmoMap[$dcName] = @() }
                $fsmoMap[$dcName] += $f.Role
            }
        } catch {}
    }

    # Détection des GC : nTDSDSA avec options bit 0 = 1
    $gcDNs = [System.Collections.Generic.HashSet[string]]::new()
    $gcResults = Invoke-LDAPSearch `
        -Filter "(&(objectClass=nTDSDSA)(options:1.2.840.113556.1.4.803:=1))" `
        -Properties @("distinguishedName") `
        -SearchRoot "CN=Sites,$configNC"
    foreach ($gc in $gcResults) {
        $dn = Get-LDAPProp $gc "distinguishedName"
        # DN du nTDSDSA = CN=NTDS Settings,CN=<NomDC>,...  -> on remonte au CN du DC
        $dcName = ([regex]::Match($dn, "CN=NTDS Settings,CN=([^,]+),")).Groups[1].Value
        if ($dcName) { [void]$gcDNs.Add($dcName) }
    }

    $DCs = foreach ($dc in $dcResults) {
        $name    = Get-LDAPProp $dc "name"
        $dnsName = Get-LDAPProp $dc "dNSHostName"
        $dn      = Get-LDAPProp $dc "distinguishedName"
        $uac     = [int](Get-LDAPProp $dc "userAccountControl" 0)

        # RODC : UF_PARTIAL_SECRETS_ACCOUNT = 0x4000000
        $isRODC  = ($uac -band 0x4000000) -ne 0

        # Site : CN=<NomDC>,CN=Servers,CN=<Site>,CN=Sites,...
        $site = ([regex]::Match($dn, "CN=Servers,CN=([^,]+),")).Groups[1].Value

        # IP via DNS
        $ip = try {
            ([System.Net.Dns]::GetHostAddresses($dnsName) |
             Where-Object { $_.AddressFamily -eq "InterNetwork" } |
             Select-Object -First 1).IPAddressToString
        } catch { "N/A" }

        # Ping
        $ping = try { Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue } catch { $false }

        [ordered]@{
            Nom               = $name
            FQDN              = $dnsName
            IP                = $ip
            Site              = $site
            GC                = if ($gcDNs.Contains($name)) { "Oui" } else { "Non" }
            RODC              = if ($isRODC) { "Oui" } else { "Non" }
            OS                = Get-LDAPProp $dc "operatingSystem"
            VersionOS         = Get-LDAPProp $dc "operatingSystemVersion"
            FSMO              = if ($fsmoMap.ContainsKey($name)) { $fsmoMap[$name] -join " | " } else { "" }
            DernierLogon      = Convert-ADFileTime (Get-LDAPProp $dc "lastLogonTimestamp" 0)
            Ping              = if ($ping) { "OK" } else { "KO" }
        }
    }

    $ADData["ControleursDeDomaine"] = @($DCs)
    Write-Log "$(@($DCs).Count) contrôleur(s) de domaine" "OK"
} catch {
    Write-Log "Erreur DCs : $_" "ERROR"
    $ADData["ControleursDeDomaine"] = @()
}

# ═════════════════════════════════════════════════════
# 3. SITES AD & SUBNETS
# ═════════════════════════════════════════════════════
Write-Log "3/6 — Sites AD & Subnets..."
try {
    $configNC = ([ADSI]"LDAP://RootDSE").Properties["configurationNamingContext"][0]
    $sitesNC  = "CN=Sites,$configNC"

    # Sites
    $siteResults = Invoke-LDAPSearch `
        -Filter "(objectClass=site)" `
        -Properties @("name","description","distinguishedName") `
        -SearchRoot $sitesNC `
        -Scope "OneLevel"

    # Subnets
    $subnetResults = Invoke-LDAPSearch `
        -Filter "(objectClass=subnet)" `
        -Properties @("name","siteObject","description","location") `
        -SearchRoot "CN=Subnets,$sitesNC"

    # Index subnets par DN de site
    $subnetBySite = @{}
    foreach ($sn in $subnetResults) {
        $siteDN = Get-LDAPProp $sn "siteObject"
        if (-not $subnetBySite.ContainsKey($siteDN)) { $subnetBySite[$siteDN] = @() }
        $subnetBySite[$siteDN] += Get-LDAPProp $sn "name"
    }

    $Sites = foreach ($site in $siteResults) {
        $dn      = Get-LDAPProp $site "distinguishedName"
        $subnets = if ($subnetBySite.ContainsKey($dn)) { $subnetBySite[$dn] -join " | " } else { "" }

        # Serveurs AD dans ce site (CN=Servers)
        $srvSearch = Invoke-LDAPSearch `
            -Filter "(objectClass=server)" `
            -Properties @("name") `
            -SearchRoot "CN=Servers,$dn" `
            -Scope "OneLevel"
        $serverList = ($srvSearch | ForEach-Object { Get-LDAPProp $_ "name" }) -join " | "

        [ordered]@{
            Nom               = Get-LDAPProp $site "name"
            Description       = Get-LDAPProp $site "description"
            Subnets           = $subnets
            ServeursAD        = $serverList
            DistinguishedName = $dn
        }
    }

    # Site Links
    $slResults = Invoke-LDAPSearch `
        -Filter "(objectClass=siteLink)" `
        -Properties @("name","siteList","cost","replInterval","description") `
        -SearchRoot "CN=Inter-Site Transports,$sitesNC"

    $SiteLinks = foreach ($sl in $slResults) {
        $rawList  = $sl.Properties["siteList"]
        $siteNoms = @()
        if ($rawList -and $rawList.Count -gt 0) {
            foreach ($slDN in $rawList) {
                $siteNoms += ([regex]::Match([string]$slDN, "CN=([^,]+),")).Groups[1].Value
            }
        }
        [ordered]@{
            Nom         = Get-LDAPProp $sl "name"
            Sites       = $siteNoms -join " <-> "
            Cout        = Get-LDAPProp $sl "cost"
            Intervalle  = "$(Get-LDAPProp $sl 'replInterval') min"
            Description = Get-LDAPProp $sl "description"
        }
    }

    $ADData["SitesAD"]   = @($Sites)
    $ADData["SiteLinks"] = @($SiteLinks)
    Write-Log "$(@($Sites).Count) site(s)  |  $(@($SiteLinks).Count) lien(s) inter-sites" "OK"
} catch {
    Write-Log "Erreur Sites AD : $_" "ERROR"
    $ADData["SitesAD"]   = @()
    $ADData["SiteLinks"] = @()
}

# ═════════════════════════════════════════════════════
# 4. GPOs
# ═════════════════════════════════════════════════════
Write-Log "4/6 — GPOs..."
try {
    $defaultNC = ([ADSI]"LDAP://RootDSE").Properties["defaultNamingContext"][0]

    # Objets GPO stockés dans CN=Policies,CN=System
    $gpoResults = Invoke-LDAPSearch `
        -Filter "(objectClass=groupPolicyContainer)" `
        -Properties @("displayName","name","gPCFileSysPath","whenCreated","whenChanged",
                      "flags","versionNumber","description") `
        -SearchRoot "CN=Policies,CN=System,$defaultNC" `
        -Scope "OneLevel"

    # Liens GPO : attribut gPLink sur les domaines, OUs et sites
    $gpoLinkResults = Invoke-LDAPSearch `
        -Filter "(&(|(objectClass=organizationalUnit)(objectClass=domainDNS)(objectClass=site))(gPLink=*))" `
        -Properties @("distinguishedName","gPLink","name")

    # Index GUID -> liste OUs liées avec statut du lien
    $gpoLinks = @{}
    foreach ($ou in $gpoLinkResults) {
        $gpl = Get-LDAPProp $ou "gPLink"
        if (-not $gpl) { continue }
        # gPLink contient [LDAP://cn={GUID},cn=policies,...;FLAGS][...]
        $matches = [regex]::Matches($gpl, '\[LDAP://[^}]+\{([A-F0-9\-]+)\}[^;]*;(\d+)\]', "IgnoreCase")
        foreach ($m in $matches) {
            $guid      = "{$($m.Groups[1].Value.ToUpper())}"
            $linkFlags = switch ($m.Groups[2].Value) {
                "0" { "Activé" }; "1" { "Désactivé" }; "2" { "Forcé" }; default { "?" }
            }
            $ouDN = Get-LDAPProp $ou "distinguishedName"
            if (-not $gpoLinks.ContainsKey($guid)) { $gpoLinks[$guid] = @() }
            $gpoLinks[$guid] += "$ouDN [$linkFlags]"
        }
    }

    $GPOs = foreach ($gpo in $gpoResults) {
        $guid    = Get-LDAPProp $gpo "name"
        $flags   = [int](Get-LDAPProp $gpo "flags" 0)
        $statut  = switch ($flags) {
            0 { "Activée" }
            1 { "Paramètres utilisateur désactivés" }
            2 { "Paramètres ordinateur désactivés" }
            3 { "Tout désactivé" }
            default { "Inconnu ($flags)" }
        }
        # versionNumber : high-word = version user, low-word = version ordi
        $version  = [int](Get-LDAPProp $gpo "versionNumber" 0)
        $verUser  = ($version -shr 16) -band 0xFFFF
        $verComp  = $version -band 0xFFFF

        [ordered]@{
            Nom               = Get-LDAPProp $gpo "displayName"
            GUID              = $guid
            Statut            = $statut
            Cree              = (Get-LDAPProp $gpo "whenCreated")
            Modifie           = (Get-LDAPProp $gpo "whenChanged")
            VersionUser       = $verUser
            VersionOrdi       = $verComp
            SYSVOLPath        = Get-LDAPProp $gpo "gPCFileSysPath"
            Description       = Get-LDAPProp $gpo "description"
            OUsLiees          = if ($gpoLinks.ContainsKey($guid)) { $gpoLinks[$guid] -join " | " } else { "(non lié)" }
        }
    }

    $ADData["GPOs"] = @($GPOs | Sort-Object Nom)
    Write-Log "$(@($GPOs).Count) GPO(s)" "OK"
} catch {
    Write-Log "Erreur GPOs : $_" "ERROR"
    $ADData["GPOs"] = @()
}

# ═════════════════════════════════════════════════════
# 5. PASSWORD SETTINGS OBJECTS (PSO)
# ═════════════════════════════════════════════════════
Write-Log "5/6 — Password Settings Objects (PSO)..."
try {
    $defaultNC = ([ADSI]"LDAP://RootDSE").Properties["defaultNamingContext"][0]

    $psoResults = Invoke-LDAPSearch `
        -Filter "(objectClass=msDS-PasswordSettings)" `
        -Properties @(
            "name","msDS-PasswordSettingsPrecedence",
            "msDS-MinimumPasswordLength","msDS-PasswordComplexityEnabled",
            "msDS-MaximumPasswordAge","msDS-MinimumPasswordAge",
            "msDS-PasswordHistoryLength","msDS-LockoutThreshold",
            "msDS-LockoutObservationWindow","msDS-LockoutDuration",
            "msDS-PasswordReversibleEncryptionEnabled",
            "msDS-PSOAppliesTo","description"
        ) `
        -SearchRoot "CN=Password Settings Container,CN=System,$defaultNC" `
        -Scope "OneLevel"

    $PSOs = foreach ($pso in $psoResults) {
        # Résolution des cibles (utilisateurs ou groupes)
        $appliesTo = @()
        $raw = $pso.Properties["msDS-PSOAppliesTo"]
        if ($raw -and $raw.Count -gt 0) {
            foreach ($dn in $raw) {
                $appliesTo += ([regex]::Match([string]$dn, "^CN=([^,]+),")).Groups[1].Value
            }
        }

        # Fenêtre d'observation et durée verrou : valeur en 100ns négative -> minutes
        $obsWin  = [Math]::Abs([Int64](Get-LDAPProp $pso "msDS-LockoutObservationWindow" 0))
        $lockDur = [Math]::Abs([Int64](Get-LDAPProp $pso "msDS-LockoutDuration" 0))

        [ordered]@{
            Nom                     = Get-LDAPProp $pso "name"
            Priorite                = Get-LDAPProp $pso "msDS-PasswordSettingsPrecedence"
            LongueurMin             = Get-LDAPProp $pso "msDS-MinimumPasswordLength"
            Complexite              = Get-LDAPProp $pso "msDS-PasswordComplexityEnabled"
            AgeMini_jours           = Convert-ADInterval (Get-LDAPProp $pso "msDS-MinimumPasswordAge" 0)
            AgeMaxi_jours           = Convert-ADInterval (Get-LDAPProp $pso "msDS-MaximumPasswordAge" 0)
            Historique              = Get-LDAPProp $pso "msDS-PasswordHistoryLength"
            SeuilVerrouillage       = Get-LDAPProp $pso "msDS-LockoutThreshold"
            FenetreObservation_min  = [Math]::Round($obsWin  / 600000000, 0)
            DureeVerrouillage_min   = [Math]::Round($lockDur / 600000000, 0)
            ChiffrementReversible   = Get-LDAPProp $pso "msDS-PasswordReversibleEncryptionEnabled"
            Description             = Get-LDAPProp $pso "description"
            AppliqueA               = $appliesTo -join " | "
        }
    }

    $ADData["PSOs"] = @($PSOs | Sort-Object Priorite)
    if (@($PSOs).Count -eq 0) {
        Write-Log "Aucun PSO trouvé (stratégie domaine par défaut uniquement)" "WARN"
    } else {
        Write-Log "$(@($PSOs).Count) PSO(s)" "OK"
    }
} catch {
    Write-Log "Erreur PSOs : $_" "ERROR"
    $ADData["PSOs"] = @()
}

# ═════════════════════════════════════════════════════
# 6. GROUPES LOCAUX (machine courante)
# ═════════════════════════════════════════════════════
Write-Log "6/6 — Groupes locaux ($env:COMPUTERNAME)..."
try {
    # WinNT provider ADSI — aucun module requis
    $computer    = [ADSI]"WinNT://$env:COMPUTERNAME,computer"
    $LocalGroups = foreach ($group in ($computer.Children | Where-Object { $_.SchemaClassName -eq "Group" })) {
        $membres = @()
        try {
            foreach ($member in $group.Members()) {
                $path   = $member.GetType().InvokeMember("ADsPath",  "GetProperty", $null, $member, $null)
                $mName  = $member.GetType().InvokeMember("Name",     "GetProperty", $null, $member, $null)
                $mClass = $member.GetType().InvokeMember("Class",    "GetProperty", $null, $member, $null)
                $origin = if ($path -match "WinNT://$env:COMPUTERNAME/") { "Local" } else { "Domaine" }
                $membres += "$mName ($mClass) [$origin]"
            }
        } catch {}

        [ordered]@{
            NomGroupe   = $group.Name[0]
            Description = try { $group.Description[0] } catch { "" }
            NbMembres   = $membres.Count
            Membres     = $membres -join " | "
        }
    }

    $ADData["GroupesLocaux"] = @($LocalGroups | Sort-Object NomGroupe)
    Write-Log "$(@($LocalGroups).Count) groupe(s) local/locaux sur $env:COMPUTERNAME" "OK"
} catch {
    Write-Log "Erreur groupes locaux : $_" "ERROR"
    $ADData["GroupesLocaux"] = @()
}

# ═════════════════════════════════════════════════════
# EXPORT JSON
# ═════════════════════════════════════════════════════
if (-not (Test-Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
}

$jsonFile = "$OutputPath\AD_Info.json"
$ADData | ConvertTo-Json -Depth 10 | Out-File $jsonFile -Encoding UTF8
Write-Log "Export JSON : $jsonFile" "OK"
Write-Log "=== COLLECTE AD TERMINÉE ===" "TITLE"

return $ADData
