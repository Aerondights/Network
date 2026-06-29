$thumb = "EMPREINTE"
$bytes = [byte[]]::new(20)
for ($i=0; $i -lt 20; $i++) { $bytes[$i] = [Convert]::ToByte($thumb.Substring($i*2,2),16) }
Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" -Name SSLCertificateSHA1Hash -Value $bytes -Type Binary
