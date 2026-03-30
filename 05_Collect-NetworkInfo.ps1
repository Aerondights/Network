<#
.SYNOPSIS
    Collecte les informations réseau, DNS, DHCP, NTP, Proxy, Certificats et Firewall.
.DESCRIPTION
    Aucun module PowerShell requis.
    DNS  : requêtes CIM sur root\MicrosoftDNS (serveurs DNS distants via CIM session DCOM/WinRM)
    DHCP : requêtes CIM sur root\Microsoft\Windows\DHCP (serveurs DHCP distants)
    Tout le reste : cmdlets NetTCPIP/NetSecurity natives (incluses dans Windows 8.1+/2012R2+)
.PARAMETER DNSServers
    FQDN ou IPs des serveurs DNS à interroger.
    Si non renseigné : détecté depuis la config DNS de la NIC active locale.
.PARAMETER DHCPServers
    FQDN ou IPs des serveurs DHCP à interroger.
    Si non renseigné : détecté depuis ADSI (CN=NetServices dans la configuration AD).
.EXAMPLE
    .\05_Collect-NetworkInfo.ps1
.EXAMPLE
    .\05_Collect-NetworkInfo.ps1 -DNSServers "dc01.corp.local","dc02.corp.local" -DHCPServers "dhcp01.corp.local"
.NOTES
    Les blocs DNS et DHCP nécessitent un accès réseau aux serveurs concernés (ports WMI/WinRM).
    En cas d'échec, ces blocs sont simplement ignorés — le reste de la collecte continue.
#>

[CmdletBinding()]
param(
    [string[]]$DNSServers  = @(),
    [string[]]$DHCPServers = @()
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

# Ouvre une CIM session DCOM puis WinRM en fallback — retourne $null si les deux échouent
function New-CimSessionSafe {
    param([string]$ComputerName, [System.Management.Automation.PSCredential]$Credential = $null)
    $params = @{ ComputerName = $ComputerName }
    if ($Credential) { $params["Credential"] = $Credential }

    foreach ($proto in @("Dcom", "Wsman")) {
        try {
            $opt = if ($proto -eq "Dcom") {
                [Microsoft.Management.Infrastructure.Options.DComSessionOptions]::new()
            } else {
                [Microsoft.Management.Infrastructure.Options.WSManSessionOptions]::new()
            }
            $sess = New-CimSession @params -SessionOption $opt -ErrorAction Stop
            return $sess
        } catch {}
    }
    return $null
}

Write-Log "=== COLLECTE RÉSEAU ===" "TITLE"

$NetworkData = [ordered]@{}

# ═════════════════════════════════════════════════════
# AUTO-DÉTECTION DNS
# ═════════════════════════════════════════════════════
if ($DNSServers.Count -eq 0) {
    Write-Log "Auto-détection des serveurs DNS depuis la configuration NIC locale..."
    try {
        # On lit les serveurs DNS configurés sur les interfaces actives (pas de module AD)
        $detected = Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.ServerAddresses.Count -gt 0 -and $_.InterfaceAlias -notmatch "Loopback|vEthernet" } |
            Select-Object -ExpandProperty ServerAddresses |
            Where-Object { $_ -ne "127.0.0.1" -and $_ -ne "::1" } |
            Sort-Object -Unique
        if ($detected.Count -gt 0) {
            $DNSServers = @($detected)
            Write-Log "DNS détectés : $($DNSServers -join ', ')" "OK"
        } else {
            Write-Log "Aucun serveur DNS détecté depuis les NICs" "WARN"
        }
    } catch {
        Write-Log "Erreur détection DNS : $_" "WARN"
    }
}

# ═════════════════════════════════════════════════════
# AUTO-DÉTECTION DHCP via ADSI (CN=NetServices dans AD)
# ═════════════════════════════════════════════════════
if ($DHCPServers.Count -eq 0) {
    Write-Log "Auto-détection des serveurs DHCP depuis AD (ADSI)..."
    try {
        $rootDSE   = [ADSI]"LDAP://RootDSE"
        $configNC  = $rootDSE.Properties["configurationNamingContext"][0]
        $dhcpRoot  = [ADSI]"LDAP://CN=NetServices,CN=Services,$configNC"
        $detected  = @()
        foreach ($child in $dhcpRoot.Children) {
            # Chaque objet dhcpServer a son FQDN dans le CN
            $fqdn = $child.Properties["dhcpServers"]
            if ($fqdn) {
                # dhcpServers : "i192.168.1.10$rdc01.corp.local" -> on extrait le FQDN après '$'
                $detected += $fqdn | ForEach-Object {
                    if ($_ -match '\$(.+)$') { $Matches[1] } else { $_ }
                }
            }
        }
        if ($detected.Count -gt 0) {
            $DHCPServers = @($detected | Sort-Object -Unique)
            Write-Log "DHCP détectés : $($DHCPServers -join ', ')" "OK"
        } else {
            Write-Log "Aucun serveur DHCP trouvé dans AD" "WARN"
        }
    } catch {
        Write-Log "Erreur détection DHCP ADSI : $_" "WARN"
    }
}

# ═════════════════════════════════════════════════════
# 1. INTERFACES RÉSEAU LOCALES
# ═════════════════════════════════════════════════════
Write-Log "1/8 — Interfaces réseau locales..."
try {
    $NICs = @(Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | ForEach-Object {
        $nic = $_

        $ipObj = @(Get-NetIPAddress -InterfaceIndex $nic.InterfaceIndex `
                    -AddressFamily IPv4 -ErrorAction SilentlyContinue)
        $ipAddr  = if ($ipObj.Count -gt 0) { $ipObj[0].IPAddress }    else { "" }
        $prefix  = if ($ipObj.Count -gt 0) { $ipObj[0].PrefixLength } else { "" }

        $gwObj = @(Get-NetRoute -InterfaceIndex $nic.InterfaceIndex `
                    -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue)
        $gw = if ($gwObj.Count -gt 0) { $gwObj[0].NextHop } else { "" }

        $dnsAddrs = @((Get-DnsClientServerAddress -InterfaceIndex $nic.InterfaceIndex `
                        -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses)

        # LinkSpeed en bits/s -> conversion propre
        $speedBps = [long]$nic.LinkSpeed
        $speedStr = if ($speedBps -ge 1000000000) {
            "$([math]::Round($speedBps / 1000000000, 0)) Gbps"
        } elseif ($speedBps -ge 1000000) {
            "$([math]::Round($speedBps / 1000000, 0)) Mbps"
        } else {
            "$speedBps bps"
        }

        $vlan = (Get-NetAdapterAdvancedProperty -Name $nic.Name `
                    -DisplayName "VLAN ID" -ErrorAction SilentlyContinue).DisplayValue

        [ordered]@{
            Interface   = $nic.Name
            Description = $nic.InterfaceDescription
            MAC         = $nic.MacAddress
            Vitesse     = $speedStr
            IP          = $ipAddr
            Masque      = $prefix
            Passerelle  = $gw
            DNS         = $dnsAddrs -join " | "
            MTU         = $nic.MtuSize
            VLAN        = if ($vlan) { $vlan } else { "" }
            Driver      = $nic.DriverVersion
        }
    })

    $NetworkData["NICsLocaux"] = $NICs
    Write-Log "$($NICs.Count) interface(s) réseau active(s)" "OK"
} catch {
    Write-Log "Erreur NICs : $_" "ERROR"
    $NetworkData["NICsLocaux"] = @()
}

# ═════════════════════════════════════════════════════
# 2. ZONES DNS  (via CIM root\MicrosoftDNS)
# ═════════════════════════════════════════════════════
Write-Log "2/8 — Zones DNS..."
$AllZones = @()
foreach ($dnsServer in $DNSServers) {
    Write-Log "  → $dnsServer"
    $cimDNS = New-CimSessionSafe -ComputerName $dnsServer
    if (-not $cimDNS) {
        Write-Log "  Impossible d'ouvrir une session CIM vers $dnsServer" "WARN"
        continue
    }
    try {
        $zones = @(Get-CimInstance -CimSession $cimDNS `
            -Namespace "root\MicrosoftDNS" `
            -ClassName "MicrosoftDNS_Zone" `
            -ErrorAction Stop)

        foreach ($z in $zones) {
            # Compte les enregistrements A de la zone
            $nbRecords = 0
            try {
                $recs = @(Get-CimInstance -CimSession $cimDNS `
                    -Namespace "root\MicrosoftDNS" `
                    -ClassName "MicrosoftDNS_AType" `
                    -Filter "ContainerName='$($z.Name)'" `
                    -ErrorAction SilentlyContinue)
                $nbRecords = $recs.Count
            } catch {}

            $AllZones += [ordered]@{
                Serveur           = $dnsServer
                Zone              = $z.Name
                Type              = switch ($z.ZoneType) {
                    0 { "Cache" }; 1 { "Primaire" }; 2 { "Secondaire" }
                    3 { "Stub" }; 4 { "Forwarder" }; default { $z.ZoneType }
                }
                IntegrAD          = if ($z.DsIntegrated) { "Oui" } else { "Non" }
                ZoneInverse       = if ($z.Reverse) { "Oui" } else { "Non" }
                MiseAJourDyn      = switch ($z.AllowUpdate) {
                    0 { "Non" }; 1 { "Non sécurisée" }; 2 { "Sécurisée uniquement" }
                    default { $z.AllowUpdate }
                }
                NbRecordsA        = $nbRecords
                Fichier           = $z.DataFile
            }
        }
        Write-Log "  $($zones.Count) zone(s) sur $dnsServer" "OK"
    } catch {
        Write-Log "  Erreur zones DNS $dnsServer : $_" "WARN"
    } finally {
        Remove-CimSession $cimDNS -ErrorAction SilentlyContinue
    }
    # On collecte depuis le premier DNS qui répond pour éviter les doublons
    if ($AllZones.Count -gt 0) { break }
}

$NetworkData["ZonesDNS"] = $AllZones
if ($AllZones.Count -eq 0) { Write-Log "Aucune zone DNS collectée" "WARN" }

# ═════════════════════════════════════════════════════
# 3. SCOPES DHCP  (via CIM root\Microsoft\Windows\DHCP)
# ═════════════════════════════════════════════════════
Write-Log "3/8 — Scopes DHCP..."
$AllScopes = @()
foreach ($dhcpServer in $DHCPServers) {
    Write-Log "  → $dhcpServer"
    $cimDHCP = New-CimSessionSafe -ComputerName $dhcpServer
    if (-not $cimDHCP) {
        Write-Log "  Impossible d'ouvrir une session CIM vers $dhcpServer" "WARN"
        continue
    }
    try {
        $scopes = @(Get-CimInstance -CimSession $cimDHCP `
            -Namespace "root\Microsoft\Windows\DHCP" `
            -ClassName "PS_DhcpServerv4Scope" `
            -ErrorAction Stop)

        foreach ($s in $scopes) {
            # Options scope : passerelle (3) et DNS (6)
            $optGW  = ""
            $optDNS = ""
            try {
                $opts = @(Get-CimInstance -CimSession $cimDHCP `
                    -Namespace "root\Microsoft\Windows\DHCP" `
                    -ClassName "PS_DhcpServerv4OptionValue" `
                    -Filter "ScopeId='$($s.ScopeId)'" `
                    -ErrorAction SilentlyContinue)
                $optGW  = ($opts | Where-Object { $_.OptionId -eq 3  } | Select-Object -ExpandProperty Value) -join " | "
                $optDNS = ($opts | Where-Object { $_.OptionId -eq 6  } | Select-Object -ExpandProperty Value) -join " | "
            } catch {}

            # Statistiques baux
            $nbLeases = 0
            $nbResa   = 0
            try {
                $stats = Get-CimInstance -CimSession $cimDHCP `
                    -Namespace "root\Microsoft\Windows\DHCP" `
                    -ClassName "PS_DhcpServerv4ScopeStatistics" `
                    -Filter "ScopeId='$($s.ScopeId)'" `
                    -ErrorAction SilentlyContinue
                if ($stats) { $nbLeases = $stats.InUse }

                $resas = @(Get-CimInstance -CimSession $cimDHCP `
                    -Namespace "root\Microsoft\Windows\DHCP" `
                    -ClassName "PS_DhcpServerv4Reservation" `
                    -Filter "ScopeId='$($s.ScopeId)'" `
                    -ErrorAction SilentlyContinue)
                $nbResa = $resas.Count
            } catch {}

            $AllScopes += [ordered]@{
                Serveur       = $dhcpServer
                ScopeID       = $s.ScopeId
                Nom           = $s.Name
                Description   = $s.Description
                MasqueSous    = $s.SubnetMask
                PlageDebut    = $s.StartRange
                PlageFin      = $s.EndRange
                Passerelle    = $optGW
                DNS           = $optDNS
                Etat          = $s.State
                BailDuree     = "$($s.LeaseDuration.TotalHours)h"
                NbBaux        = $nbLeases
                NbReservations= $nbResa
            }
        }
        Write-Log "  $($scopes.Count) scope(s) sur $dhcpServer" "OK"
    } catch {
        Write-Log "  Erreur scopes DHCP $dhcpServer : $_" "WARN"
    } finally {
        Remove-CimSession $cimDHCP -ErrorAction SilentlyContinue
    }
}

$NetworkData["ScopesDHCP"] = $AllScopes
if ($AllScopes.Count -eq 0) { Write-Log "Aucun scope DHCP collecté" "WARN" }

# ═════════════════════════════════════════════════════
# 4. CERTIFICATS (magasin local machine)
# ═════════════════════════════════════════════════════
Write-Log "4/8 — Certificats (LocalMachine\My)..."
try {
    $today = Get-Date
    $Certs = @(Get-ChildItem -Path "Cert:\LocalMachine\My" -ErrorAction Stop |
        Where-Object { $_.NotAfter -gt $today } |
        ForEach-Object {
            $cert = $_
            $joursRestants = [int]($cert.NotAfter - $today).TotalDays

            # SAN : extension Subject Alternative Name
            $sanExt = $cert.Extensions | Where-Object { $_.Oid.FriendlyName -eq "Subject Alternative Name" }
            $san    = if ($sanExt) { $sanExt.Format($false) } else { "" }

            # Usages EKU
            $eku = $cert.Extensions | Where-Object { $_.Oid.Value -eq "2.5.29.37" }
            $ekuStr = if ($eku) { $eku.Format($false) } else { "" }

            [ordered]@{
                Sujet          = $cert.Subject
                Emetteur       = $cert.Issuer
                Expire         = $cert.NotAfter.ToString("yyyy-MM-dd")
                JoursRestants  = $joursRestants
                Alerte         = if ($joursRestants -lt 30)  { "CRITIQUE < 30j"  }
                                 elseif ($joursRestants -lt 60) { "ATTENTION < 60j" }
                                 else                           { "OK" }
                Thumbprint     = $cert.Thumbprint
                SerialNumber   = $cert.SerialNumber
                SAN            = $san
                Usages         = $ekuStr
                HasPrivateKey  = $cert.HasPrivateKey
            }
        } | Sort-Object JoursRestants)

    $NetworkData["Certificats"] = $Certs
    $critiques = @($Certs | Where-Object { $_.Alerte -ne "OK" }).Count
    Write-Log "$($Certs.Count) certificat(s) valide(s) — $critiques nécessitent attention" $(if ($critiques -gt 0) { "WARN" } else { "OK" })
} catch {
    Write-Log "Erreur certificats : $_" "ERROR"
    $NetworkData["Certificats"] = @()
}

# ═════════════════════════════════════════════════════
# 5. NTP
# ═════════════════════════════════════════════════════
Write-Log "5/8 — Configuration NTP (w32tm)..."
try {
    # w32tm retourne des tableaux de strings — on filtre les lignes utiles
    $ntpCfg    = @(w32tm /query /configuration 2>&1)
    $ntpStatus = @(w32tm /query /status 2>&1)
    $ntpPeers  = @(w32tm /query /peers 2>&1)

    # Extraction des valeurs clés
    $extractVal = { param($lines, $key)
        $line = $lines | Where-Object { $_ -match "^\s*$key\s*:" } | Select-Object -First 1
        if ($line) { ($line -split ":", 2)[1].Trim() } else { "" }
    }

    $NetworkData["NTP"] = [ordered]@{
        Type            = & $extractVal $ntpCfg "Type"
        NTPServer       = & $extractVal $ntpCfg "NtpServer"
        Enabled         = & $extractVal $ntpCfg "Enabled"
        Source          = & $extractVal $ntpStatus "Source"
        Offset          = & $extractVal $ntpStatus "Last Successful Sync Time"
        Stratum         = & $extractVal $ntpStatus "Stratum"
        NbPairs         = @($ntpPeers | Where-Object { $_ -match "^\s*Pair\s*:" }).Count
        DetailPeers     = ($ntpPeers | Where-Object { $_ -match "Pair|Etat|Mode|Decalage" }) -join " | "
    }
    Write-Log "NTP collecté — Source : $($NetworkData['NTP'].Source)" "OK"
} catch {
    Write-Log "Erreur NTP : $_" "ERROR"
    $NetworkData["NTP"] = @{ Erreur = $_.Exception.Message }
}

# ═════════════════════════════════════════════════════
# 6. PROXY
# ═════════════════════════════════════════════════════
Write-Log "6/8 — Configuration Proxy..."
try {
    $regMachine = Get-ItemProperty `
        -Path "HKLM:\Software\Policies\Microsoft\Windows\CurrentVersion\Internet Settings" `
        -ErrorAction SilentlyContinue
    $regUser = Get-ItemProperty `
        -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" `
        -ErrorAction SilentlyContinue

    # WinHTTP proxy (utilisé par les services système, différent de WinInet)
    $winhttp = @(netsh winhttp show proxy 2>&1) -join " "

    $NetworkData["Proxy"] = [ordered]@{
        ProxyGPO_Machine  = if ($regMachine) { $regMachine.ProxyServer }    else { "" }
        ProxyUtilisateur  = if ($regUser)    { $regUser.ProxyServer }       else { "" }
        ProxyActif        = if ($regUser)    { [bool]$regUser.ProxyEnable } else { $false }
        ListeExclusion    = if ($regUser)    { $regUser.ProxyOverride }     else { "" }
        AutoConfig_PAC    = if ($regUser)    { $regUser.AutoConfigURL }     else { "" }
        WinHTTP           = $winhttp
    }
    Write-Log "Proxy collecté" "OK"
} catch {
    Write-Log "Erreur Proxy : $_" "ERROR"
    $NetworkData["Proxy"] = @{ Erreur = $_.Exception.Message }
}

# ═════════════════════════════════════════════════════
# 7. PROFILS FIREWALL
# ═════════════════════════════════════════════════════
Write-Log "7/8 — Firewall Windows (profils)..."
try {
    $FWProfiles = @(Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object {
        [ordered]@{
            Profil              = $_.Name
            Actif               = $_.Enabled.ToString()
            ActionEntranteDefaut= $_.DefaultInboundAction.ToString()
            ActionSortanteDefaut= $_.DefaultOutboundAction.ToString()
            JournalisationAutorisees = $_.LogAllowed.ToString()
            JournalisationBloquees   = $_.LogBlocked.ToString()
            CheminJournal       = $_.LogFileName
        }
    })
    $NetworkData["FirewallProfils"] = $FWProfiles
    Write-Log "$($FWProfiles.Count) profil(s) firewall collecté(s)" "OK"
} catch {
    Write-Log "Erreur profils firewall : $_" "ERROR"
    $NetworkData["FirewallProfils"] = @()
}

# ═════════════════════════════════════════════════════
# 8. RÈGLES FIREWALL (filtrées sur les flux VDI/MECM)
# ═════════════════════════════════════════════════════
Write-Log "8/8 — Règles Firewall (flux VDI/infra)..."
try {
    $pattern = "Horizon|VMware|VDI|RDS|PCoIP|Blast|MECM|SCCM|WinRM|WMI|ConfigMgr"

    # On récupère d'abord les règles filtrées (Enabled est un enum, pas un bool)
    $FWRules = @(Get-NetFirewallRule -ErrorAction Stop |
        Where-Object {
            $_.DisplayName -match $pattern -and
            $_.Enabled -eq [Microsoft.Management.Infrastructure.CimInstance]::new("MSFT_NetFirewallRule").psobject.Properties -or
            $_.Enabled.ToString() -eq "True"
        } | ForEach-Object {
            $rule     = $_
            $portFilt = $rule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
            $appFilt  = $rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
            [ordered]@{
                Nom          = $rule.DisplayName
                Direction    = $rule.Direction.ToString()
                Action       = $rule.Action.ToString()
                Protocole    = if ($portFilt) { $portFilt.Protocol }    else { "" }
                PortLocal    = if ($portFilt) { $portFilt.LocalPort }   else { "" }
                PortDistant  = if ($portFilt) { $portFilt.RemotePort }  else { "" }
                Profil       = $rule.Profile.ToString()
                Programme    = if ($appFilt)  { $appFilt.Program }      else { "" }
                Description  = $rule.Description
                Groupe       = $rule.Group
            }
        })

    # Fallback plus simple si la première approche retourne 0 règles
    if ($FWRules.Count -eq 0) {
        $FWRules = @(Get-NetFirewallRule -ErrorAction Stop |
            Where-Object { $_.DisplayName -match $pattern } |
            ForEach-Object {
                $rule     = $_
                $portFilt = $rule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
                $appFilt  = $rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
                [ordered]@{
                    Nom         = $rule.DisplayName
                    Activee     = $rule.Enabled.ToString()
                    Direction   = $rule.Direction.ToString()
                    Action      = $rule.Action.ToString()
                    Protocole   = if ($portFilt) { $portFilt.Protocol }   else { "" }
                    PortLocal   = if ($portFilt) { $portFilt.LocalPort }  else { "" }
                    PortDistant = if ($portFilt) { $portFilt.RemotePort } else { "" }
                    Profil      = $rule.Profile.ToString()
                    Programme   = if ($appFilt)  { $appFilt.Program }     else { "" }
                    Description = $rule.Description
                }
            })
    }

    $NetworkData["FirewallRegles"] = $FWRules
    Write-Log "$($FWRules.Count) règle(s) firewall correspondante(s)" "OK"
} catch {
    Write-Log "Erreur règles firewall : $_" "ERROR"
    $NetworkData["FirewallRegles"] = @()
}

# ═════════════════════════════════════════════════════
# EXPORT JSON
# ═════════════════════════════════════════════════════
if (-not (Test-Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
}

$jsonFile = "$OutputPath\Network_Info.json"
$NetworkData | ConvertTo-Json -Depth 10 | Out-File $jsonFile -Encoding UTF8
Write-Log "Export JSON : $jsonFile" "OK"
Write-Log "=== COLLECTE RÉSEAU TERMINÉE ===" "TITLE"

return $NetworkData
