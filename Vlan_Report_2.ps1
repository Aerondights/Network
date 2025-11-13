<#
.SYNOPSIS
    Génère une topologie réseau à partir de fichiers CSV VLAN.

.DESCRIPTION
    Lit un fichier listant les VLANs et un dossier contenant un fichier CSV par VLAN (hôtes).
    Produit GraphML (par défaut) et/ou DOT, JSON, CSV résumé.
    Optionnel : génère images via Graphviz (dot) si présent.

.PARAMETER VlanListPath
    Chemin vers le fichier CSV listant les VLANs (ex: VLANId,VLANName,Description).

.PARAMETER VlanFolder
    Dossier contenant les CSV de chaque VLAN. Chaque fichier doit contenir au minimum les colonnes Name,Address,Datacenter.

.PARAMETER OutputFolder
    Dossier de sortie pour exports et logs.

.PARAMETER GraphFormat
    Formats d'export de la topologie: GraphML, DOT (par défaut GraphML).

.PARAMETER GenerateImages
    Si présent, essaye d'appeler 'dot' pour produire PNG/SVG à partir du DOT.

.PARAMETER LogPath
    Chemin du fichier de log.

.EXAMPLE
    .\Build-NetworkTopology.ps1 -VlanListPath .\vlans.csv -VlanFolder .\vlans -OutputFolder .\out -GraphFormat GraphML,DOT -GenerateImages -LogPath .\out\topology.log

#>

param(
    [Parameter(Mandatory=$true)][string] $VlanListPath,
    [Parameter(Mandatory=$true)][string] $VlanFolder,
    [Parameter(Mandatory=$true)][string] $OutputFolder,
    [string[]] $GraphFormat = @("GraphML"),
    [switch] $GenerateImages,
    [string] $LogPath = ""
)

Set-StrictMode -Version Latest

function Write-Log {
    param([string]$Message, [string]$Level="INFO")
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[$timestamp] [$Level] $Message"
    Write-Verbose $line
    if ($LogPath) {
        try {
            Add-Content -Path $LogPath -Value $line -ErrorAction Stop
        } catch {
            Write-Warning "Impossible d'écrire dans le log $LogPath : $_"
        }
    }
}

function Validate-FileExists {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path $Path)) {
        throw "Fichier/dossier introuvable: $Name ($Path)"
    }
}

function Import-CSVChecked {
    param([string]$Path, [string[]]$RequiredHeaders)
    try {
        $rows = Import-Csv -Path $Path -ErrorAction Stop
    } catch {
        throw "Échec import CSV '$Path' : $_"
    }
    # Header validation
    $headers = ($rows | Get-Member -MemberType NoteProperty | Select-Object -ExpandProperty Name)
    foreach ($h in $RequiredHeaders) {
        if (-not ($headers -contains $h)) {
            throw "Le fichier '$Path' ne contient pas la colonne requise '$h'. Colonnes présentes: $($headers -join ', ')"
        }
    }
    return $rows
}

function Build-Topology {
    param(
        [array]$VlanList,          # objets VLAN {VLANId, VLANName, ...}
        [string]$VlanFolder
    )

    $devices = @{}   # clé = device unique (Name|Address), valeur = objet device
    $vlans = @{}     # clé = VLANId (ou VLANName), valeur = objet vlan
    $edges = @()     # liste d'arêtes @{From=$id1; To=$id2; Type='vlan-member'}

    foreach ($v in $VlanList) {
        $vlanId = if ($v.VLANId) { $v.VLANId } else { $v.VLAN }  # support variantes
        $vlanKey = "$vlanId"
        $vlans[$vlanKey] = [PSCustomObject]@{
            Id = $vlanKey
            Name = if ($v.VLANName) { $v.VLANName } elseif ($v.Name) { $v.Name } else { "VLAN_$vlanKey" }
            Description = ($v.Description -as [string])
            SourceFile = $v.SourceFile
        }

        # Cherche un CSV correspondant au VLAN (nom ou id)
        $candidates = @()
        $patterns = @("$vlanId*.csv", "*$vlanId*.csv", "$($vlan.Name)*.csv")
        foreach ($p in $patterns) {
            $candidates += Get-ChildItem -Path $VlanFolder -Filter $p -File -ErrorAction SilentlyContinue
        }
        $candidates = $candidates | Select-Object -Unique

        if ($candidates.Count -eq 0) {
            Write-Log "Aucun fichier CSV hôtes trouvé pour VLAN '$vlanKey' (recherché par $($patterns -join ', '))." "WARN"
            continue
        }

        foreach ($file in $candidates) {
            Write-Log "Import du fichier '$($file.FullName)' pour VLAN $vlanKey."
            try {
                $rows = Import-CSVChecked -Path $file.FullName -RequiredHeaders @("Name","Address","Datacenter")
            } catch {
                Write-Log $_ "ERROR"
                continue
            }
            foreach ($r in $rows) {
                $deviceKey = "{0}|{1}" -f $r.Name.Trim(), $r.Address.Trim()
                if (-not $devices.ContainsKey($deviceKey)) {
                    $devices[$deviceKey] = [PSCustomObject]@{
                        Id = [System.Guid]::NewGuid().ToString()
                        Name = $r.Name.Trim()
                        Address = $r.Address.Trim()
                        Datacenter = $r.Datacenter.Trim()
                        Tags = @()
                        SourceFile = $file.Name
                    }
                } else {
                    # Fusionner informations manquantes
                    $dev = $devices[$deviceKey]
                    if (-not $dev.Datacenter -and $r.Datacenter) { $dev.Datacenter = $r.Datacenter.Trim() }
                    if (-not $dev.Address -and $r.Address) { $dev.Address = $r.Address.Trim() }
                }
                # créer arête VLAN -> device
                $edges += [PSCustomObject]@{
                    From = $vlans[$vlanKey].Id
                    To = $devices[$deviceKey].Id
                    Type = "vlan-member"
                    Vlan = $vlanKey
                }
            }
        }
    }

    return [PSCustomObject]@{
        Vlans = $vlans
        Devices = $devices
        Edges = $edges
    }
}

function Export-GraphML {
    param([psobject]$Topology, [string]$OutPath)
    # Crée un document GraphML de base
    $xml = New-Object System.Xml.XmlDocument
    $xmlDeclaration = $xml.CreateXmlDeclaration("1.0","UTF-8",$null)
    $xml.AppendChild($xmlDeclaration) | Out-Null

    $graphml = $xml.CreateElement("graphml", "http://graphml.graphdrawing.org/xmlns")
    $xml.AppendChild($graphml) | Out-Null

    # Définir keys (attributs)
    $keys = @(
        @{id='k_name'; for='node'; attr='name'; type='string'},
        @{id='k_type'; for='node'; attr='type'; type='string'},
        @{id='k_address'; for='node'; attr='address'; type='string'},
        @{id='k_datacenter'; for='node'; attr='datacenter'; type='string'},
        @{id='k_vlan_name'; for='node'; attr='vlan_name'; type='string'}
    )
    foreach ($k in $keys) {
        $key = $xml.CreateElement("key")
        $key.SetAttribute("id",$k.id)
        $key.SetAttribute("for",$k.for)
        $key.SetAttribute("attr.name",$k.attr)
        $key.SetAttribute("attr.type",$k.type)
        $graphml.AppendChild($key) | Out-Null
    }

    $graph = $xml.CreateElement("graph")
    $graph.SetAttribute("id","G")
    $graph.SetAttribute("edgedefault","undirected")
    $graphml.AppendChild($graph) | Out-Null

    # Noeuds VLAN
    foreach ($kv in $Topology.Vlans.GetEnumerator()) {
        $v = $kv.Value
        $node = $xml.CreateElement("node")
        $node.SetAttribute("id",$v.Id)
        $graph.AppendChild($node) | Out-Null

        $d_name = $xml.CreateElement("data"); $d_name.SetAttribute("key","k_name"); $d_name.InnerText = $v.Name; $node.AppendChild($d_name) | Out-Null
        $d_type = $xml.CreateElement("data"); $d_type.SetAttribute("key","k_type"); $d_type.InnerText = "VLAN"; $node.AppendChild($d_type) | Out-Null
        $d_vlan = $xml.CreateElement("data"); $d_vlan.SetAttribute("key","k_vlan_name"); $d_vlan.InnerText = $v.Id; $node.AppendChild($d_vlan) | Out-Null
    }

    # Noeuds Devices
    foreach ($kv in $Topology.Devices.GetEnumerator()) {
        $d = $kv.Value
        $node = $xml.CreateElement("node")
        $node.SetAttribute("id",$d.Id)
        $graph.AppendChild($node) | Out-Null

        $n = $xml.CreateElement("data"); $n.SetAttribute("key","k_name"); $n.InnerText = $d.Name; $node.AppendChild($n) | Out-Null
        $t = $xml.CreateElement("data"); $t.SetAttribute("key","k_type"); $t.InnerText = "Device"; $node.AppendChild($t) | Out-Null
        $addr = $xml.CreateElement("data"); $addr.SetAttribute("key","k_address"); $addr.InnerText = $d.Address; $node.AppendChild($addr) | Out-Null
        $dc = $xml.CreateElement("data"); $dc.SetAttribute("key","k_datacenter"); $dc.InnerText = $d.Datacenter; $node.AppendChild($dc) | Out-Null
    }

    # Edges
    $edgeId = 0
    foreach ($e in $Topology.Edges) {
        $edge = $xml.CreateElement("edge")
        $edge.SetAttribute("id", "e$edgeId")
        $edge.SetAttribute("source",$e.From)
        $edge.SetAttribute("target",$e.To)
        $graph.AppendChild($edge) | Out-Null
        $edgeId++
    }

    $xml.Save($OutPath)
    Write-Log "Export GraphML => $OutPath"
}

function Export-DOT {
    param([psobject]$Topology, [string]$OutPath)
    $sb = New-Object System.Text.StringBuilder
    $sb.AppendLine("graph Topology {") | Out-Null
    $sb.AppendLine("  node [shape=box,fontname=""Arial""];") | Out-Null

    # VLAN nodes (use label with VLAN name)
    foreach ($kv in $Topology.Vlans.GetEnumerator()) {
        $v = $kv.Value
        $label = $v.Name -replace '"','\"'
        $sb.AppendLine("  ""$($v.Id)"" [label=""$label\n(vlan:$($v.Id))"",shape=ellipse];") | Out-Null
    }

    # Devices
    foreach ($kv in $Topology.Devices.GetEnumerator()) {
        $d = $kv.Value
        $label = ("{0}\n{1}\n{2}" -f $d.Name, $d.Address, $d.Datacenter) -replace '"','\"'
        $sb.AppendLine("  ""$($d.Id)"" [label=""$label"",shape=box];") | Out-Null
    }

    # Edges
    foreach ($e in $Topology.Edges) {
        $sb.AppendLine("  ""$($e.From)"" -- ""$($e.To)"";") | Out-Null
    }

    $sb.AppendLine("}") | Out-Null
    $sb.ToString() | Out-File -FilePath $OutPath -Encoding UTF8
    Write-Log "Export DOT => $OutPath"
}

function Try-GenerateImageFromDot {
    param([string]$DotPath, [string]$OutImagePath)
    $dotExe = "dot"  # suppose dot est dans le PATH
    try {
        $p = Start-Process -FilePath $dotExe -ArgumentList "-Tpng","`"$DotPath`"","-o","`"$OutImagePath`"" -NoNewWindow -Wait -PassThru -ErrorAction Stop
        if ($p.ExitCode -eq 0) {
            Write-Log "Image générée: $OutImagePath"
            return $true
        } else {
            Write-Log "dot a retourné un code $($p.ExitCode) lors de la génération de l'image." "WARN"
            return $false
        }
    } catch {
        Write-Log "Échec génération image (dot absent ou erreur): $_" "WARN"
        return $false
    }
}

# --- Main ---

try {
    # Préparations
    if (-not (Test-Path $OutputFolder)) { New-Item -Path $OutputFolder -ItemType Directory -Force | Out-Null }
    if ($LogPath -eq "") { $LogPath = Join-Path $OutputFolder "topology.log" }
    Write-Log "Démarrage génération topologie"

    Validate-FileExists -Path $VlanListPath -Name "VlanList"
    Validate-FileExists -Path $VlanFolder -Name "VlanFolder"

    # Import VLAN list (support colonnes typiques: VLANId,VLANName,Description)
    Write-Log "Import du fichier VLAN global: $VlanListPath"
    $vlanRows = Import-CSVChecked -Path $VlanListPath -RequiredHeaders @("VLANId")
    # Ajout d'une colonne SourceFile pour traçabilité
    $vlanRows | ForEach-Object { $_ | Add-Member -Name SourceFile -NotePropertyValue (Split-Path -Leaf $VlanListPath) -Force }

    # Build topology
    $topology = Build-Topology -VlanList $vlanRows -VlanFolder $VlanFolder

    # Exports summary CSV & JSON
    $summaryDevices = $topology.Devices.GetEnumerator() | ForEach-Object {
        [PSCustomObject]@{
            DeviceId = $_.Value.Id
            Name = $_.Value.Name
            Address = $_.Value.Address
            Datacenter = $_.Value.Datacenter
            SourceFile = $_.Value.SourceFile
        }
    }
    $summaryVlans = $topology.Vlans.GetEnumerator() | ForEach-Object {
        [PSCustomObject]@{
            VlanId = $_.Value.Id
            Name = $_.Value.Name
            Description = $_.Value.Description
            SourceFile = $_.Value.SourceFile
        }
    }
    $summaryEdges = $topology.Edges | ForEach-Object {
        [PSCustomObject]@{
            From = $_.From
            To = $_.To
            Type = $_.Type
            Vlan = $_.Vlan
        }
    }

    $devicesCsv = Join-Path $OutputFolder "topology_devices.csv"
    $vlansCsv = Join-Path $OutputFolder "topology_vlans.csv"
    $edgesCsv = Join-Path $OutputFolder "topology_edges.csv"
    $jsonOut = Join-Path $OutputFolder "topology.json"

    $summaryDevices | Export-Csv -Path $devicesCsv -NoTypeInformation -Encoding UTF8
    $summaryVlans | Export-Csv -Path $vlansCsv -NoTypeInformation -Encoding UTF8
    $summaryEdges | Export-Csv -Path $edgesCsv -NoTypeInformation -Encoding UTF8

    $fullObject = [PSCustomObject]@{
        Vlans = ($summaryVlans)
        Devices = ($summaryDevices)
        Edges = ($summaryEdges)
    }
    $fullObject | ConvertTo-Json -Depth 5 | Out-File -FilePath $jsonOut -Encoding UTF8

    Write-Log "Résumé exporté: $devicesCsv, $vlansCsv, $edgesCsv, $jsonOut"

    # Graph exports
    if ($GraphFormat -contains "GraphML") {
        $graphmlPath = Join-Path $OutputFolder "topology.graphml"
        Export-GraphML -Topology $topology -OutPath $graphmlPath
    }
    if ($GraphFormat -contains "DOT") {
        $dotPath = Join-Path $OutputFolder "topology.dot"
        Export-DOT -Topology $topology -OutPath $dotPath

        if ($GenerateImages) {
            $pngPath = Join-Path $OutputFolder "topology.png"
            Try-GenerateImageFromDot -DotPath $dotPath -OutImagePath $pngPath | Out-Null
        }
    }

    Write-Log "Topologie générée avec succès."
    Write-Output @{
        Status = "OK"
        OutputFolder = (Resolve-Path $OutputFolder).Path
        GraphML = if (Test-Path $graphmlPath) { (Resolve-Path $graphmlPath).Path } else { $null }
        DOT = if (Test-Path $dotPath) { (Resolve-Path $dotPath).Path } else { $null }
        JSON = (Resolve-Path $jsonOut).Path
        CSVs = @((Resolve-Path $devicesCsv).Path,(Resolve-Path $vlansCsv).Path,(Resolve-Path $edgesCsv).Path)
    } | ConvertTo-Json -Depth 3

} catch {
    Write-Log "Erreur fatale : $_" "ERROR"
    throw
}
