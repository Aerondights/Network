function Get-RdpCert($target,$port=3389){
  $tcp=New-Object Net.Sockets.TcpClient($target,$port); $ns=$tcp.GetStream()
  # X.224 Connection Request demandant PROTOCOL_SSL (TLS)
  $cr=[byte[]](0x03,0x00,0x00,0x13,0x0E,0xE0,0x00,0x00,0x00,0x00,0x00,0x01,0x00,0x08,0x00,0x01,0x00,0x00,0x00)
  $ns.Write($cr,0,$cr.Length); $ns.Flush()
  $r=New-Object byte[] 19; $ns.Read($r,0,19)|Out-Null      # Connection Confirm
  $ssl=New-Object Net.Security.SslStream($ns,$false,{$true})
  $ssl.AuthenticateAsClient($target)
  $c=[Security.Cryptography.X509Certificates.X509Certificate2]$ssl.RemoteCertificate
  [pscustomobject]@{Cible=$target;Sujet=$c.Subject;Emetteur=$c.Issuer;Empreinte=$c.Thumbprint}
  $ssl.Dispose(); $tcp.Close()
}
Get-RdpCert 'zxr10-lb-coop-mcn.yres.ytech'     # via la VIP
Get-RdpCert 'XAZQD0016098.ycam.ytech'          # backend en direct
