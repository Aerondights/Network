# Connexion RDP via VIP relais — déroulé bas niveau, octet par octet

Scénario : client Windows 11 25H2 → fichier `.rdp` → VIP `zxr104-lb-coop-mcn.yres.ytech`
(relais TCP pur, aucun SPN, pas de terminaison TLS) → VM cible `XAZZR0022015.ycam.ytech`,
session NLA, utilisateur `YGIE\A90AD00`.

Tout est dans l'ordre chronologique réel. Chaque phase donne d'abord l'idée en une ligne,
puis le détail protocolaire (PDU, champs, drapeaux, crypto). Références : MS-RDPBCGR (RDP),
RFC 8446/5246 (TLS), MS-CSSP (CredSSP), MS-SPNG/RFC 4178 (SPNEGO), RFC 4120 (Kerberos),
MS-NLMP (NTLM).

---

## Légende des acteurs

| Acteur | Rôle réel |
|---|---|
| `mstsc` | client RDP sur le poste 25H2 ; appelle SSPI/Schannel/CredSSP localement |
| LSA/SSPI | côté client : orchestre Negotiate → Kerberos/NTLM et la délégation |
| DNS | résolution nom → IP |
| VIP | répartiteur L4 : recopie les octets TCP, ne déchiffre rien, n'a pas de SPN |
| VM | hôte RDSH de destination (le listener `RDP-Tcp`) |
| KDC | contrôleur de domaine : AS/TGS Kerberos |

---

## Phase 0 — Pré-requis (au logon, avant toute connexion RDP)

**En clair.** Quand vous avez ouvert votre session Windows, vous avez déjà obtenu un « passe »
Kerberos. Il servira (ou tentera de servir) plus tard pour la cible RDP.

**Bas niveau.**
- À l'ouverture de session interactive : `AS-REQ` → `AS-REP` auprès du KDC.
  Le client reçoit un **TGT** (Ticket-Granting Ticket) + une clé de session TGS,
  stockés dans le cache LSA (visibles via `klist`).
- Aucun ticket de service n'existe encore pour la cible RDP : il sera demandé à la phase 6.

---

## Phase 1 — Lecture du fichier `.rdp`

**En clair.** Rien sur le réseau. `mstsc` lit ses paramètres.

**Bas niveau.** Paramètres déterminants pour la suite :
- `full address:s:zxr104-lb-coop-mcn.yres.ytech` → c'est ce **nom** qui sera comparé au
  certificat (phase 5) et utilisé pour fabriquer le SPN Kerberos `TERMSRV/...` (phase 6).
- `enablecredsspsupport:i:1` → annonce du protocole `PROTOCOL_HYBRID` à la phase 4.
- `authentication level:i:N` → conduite à tenir si la validation serveur échoue (phase 5).
- `username:s:` / domaine → alimente `TSPasswordCreds` (phase 6f) et le `Client Info PDU` (phase 8).

---

## Phase 2 — Résolution du nom (DNS)

**En clair.** `mstsc` a un nom, pas une adresse ; il demande au DNS.

**Bas niveau.**
```
mstsc → DNS : Standard Query  A   zxr104-lb-coop-mcn.yres.ytech
DNS → mstsc : Standard Query Response  A  <IP_VIP>
```
(Selon la pile : requête `AAAA` aussi.) Aucune notion de SPN ni de certificat ici — juste
nom → `IP_VIP`.

---

## Phase 3 — Connexion TCP + relais

**En clair.** Ouverture d'un canal TCP vers la VIP, port 3389. La VIP recopie les octets vers
une VM, à l'aveugle.

**Bas niveau.**
```
mstsc → VIP : SYN        (dst 3389)
VIP   → mstsc: SYN, ACK
mstsc → VIP : ACK                       ← canal TCP client↔VIP établi
VIP   → VM  : SYN/SYN-ACK/ACK           ← second canal VIP↔VM
VIP : relaie désormais octet pour octet, sans inspection (L4)
```
**Conséquence structurante.** Tout ce qui suit (TLS, CredSSP, RDP) se déroule de bout en bout
**entre `mstsc` et la VM**, mais l'« identité réseau » visée par le client reste le **nom de la
VIP**. Le relais ne possède ni certificat ni SPN : il n'apparaît plus dans les couches hautes.

---

## Phase 4 — Négociation de sécurité RDP (X.224 / TPKT)

**En clair.** Avant tout chiffrement, client et VM s'accordent sur le mode de sécurité.

**Bas niveau.** Encapsulation `TPKT` (RFC 1006) puis TPDU X.224 (RFC 905).

TPKT :
```
+--------+--------+-----------------+
| ver=03 | rsv=00 |   length (16)   |
+--------+--------+-----------------+
```

X.224 Connection Request (code `0xE0`), pouvant être précédé d'un cookie/routingToken
(`Cookie: mstshash=<user>\r\n`, ou un jeton de redirection posé par un broker), suivi du bloc
`RDP_NEG_REQ` (8 octets) :
```
type(1)=0x01  flags(1)  length(2)=0x0008  requestedProtocols(4)
```
`requestedProtocols` (drapeaux MS-RDPBCGR) :
```
PROTOCOL_RDP       = 0x00000000   (sécurité RDP standard, hérité)
PROTOCOL_SSL       = 0x00000001   (TLS)
PROTOCOL_HYBRID    = 0x00000002   (CredSSP / NLA)  ← annoncé car enablecredsspsupport=1
PROTOCOL_RDSTLS    = 0x00000004
PROTOCOL_HYBRID_EX = 0x00000008   (CredSSP + earlyUserAuthResult)
PROTOCOL_RDSAAD    = 0x00000010   (Entra/Azure AD)
```

X.224 Connection Confirm (code `0xD0`) + `RDP_NEG_RSP` :
```
type(1)=0x02  flags(1)  length(2)=0x0008  selectedProtocol(4)
```
Ici `selectedProtocol = PROTOCOL_HYBRID` → on passe en NLA.

En cas de refus, on reçoit `RDP_NEG_FAILURE` (type `0x03`, `failureCode`) :
```
SSL_REQUIRED_BY_SERVER                 = 0x00000001
SSL_NOT_ALLOWED_BY_SERVER              = 0x00000002
SSL_CERT_NOT_ON_SERVER                 = 0x00000003
INCONSISTENT_FLAGS                     = 0x00000004
HYBRID_REQUIRED_BY_SERVER              = 0x00000005
SSL_WITH_USER_AUTH_REQUIRED_BY_SERVER  = 0x00000006
```

---

## Phase 5 — Handshake TLS + validation du certificat  ⚑ DÉCISION 1

**En clair.** Montage du tunnel chiffré ; la VM présente son certificat ; `mstsc` vérifie le nom.

**Bas niveau (TLS 1.2 ; TLS 1.3 réorganise mais la logique de validation est identique).**
```
mstsc → VM : ClientHello
             - extension SNI server_name = zxr104-lb-coop-mcn.yres.ytech
             - supported_versions, cipher_suites, supported_groups, signature_algorithms
VM → mstsc : ServerHello (cipher retenu)
             Certificate           ← chaîne X.509 de la VM (CN/SAN = XAZZR0022015.ycam.ytech)
             ServerKeyExchange     (ECDHE)
             ServerHelloDone
mstsc → VM : ClientKeyExchange, ChangeCipherSpec, Finished
VM → mstsc : ChangeCipherSpec, Finished
```
**Validation du certificat par `mstsc`** (3 contrôles) :
1. Chaîne de confiance jusqu'à une CA racine de confiance.
2. Période de validité, révocation.
3. **Correspondance du nom** : le SAN (ou CN) couvre-t-il le nom contacté
   `zxr104-lb-coop-mcn.yres.ytech` ?

**Dans votre cas.**
- SAN VIP présent → contrôle 3 OK → **identité serveur établie via X.509**.
- SAN VIP absent → échec du contrôle 3 → erreur « Le nom du serveur dans le certificat est
  incorrect ». Le comportement dépend alors de `authentication level` (valeur effective, GPO
  `AuthenticationLevel` comprise) :
  - 0 → connecter sans avertir (ignore le non-match) ;
  - 2 → avertir (Oui/Non) ;
  - 1 → refuser → c'est l'écran « Vous ne pouvez pas continuer car une authentification est
    nécessaire ».

À l'issue : un canal TLS chiffré + intègre. **Le résultat de la validation de nom est mémorisé**
et servira à classer l'authentification serveur en phase 6d.

---

## Phase 6 — CredSSP / SPNEGO (dans le tunnel TLS)

CredSSP (MS-CSSP) échange une suite de structures `TSRequest` (ASN.1 DER) à l'intérieur du
tunnel TLS :
```
TSRequest ::= SEQUENCE {
  version     [0] INTEGER,
  negoTokens  [1] NegoData     OPTIONAL,   -- jetons SPNEGO (Kerberos/NTLM)
  authInfo    [2] OCTET STRING OPTIONAL,   -- identifiants chiffrés (délégation)
  pubKeyAuth  [3] OCTET STRING OPTIONAL,   -- clé publique TLS scellée (anti-MITM)
  errorCode   [4] INTEGER      OPTIONAL,   -- HYBRID_EX
  clientNonce [5] OCTET STRING OPTIONAL
}
```

### 6a — SPNEGO NegTokenInit (choix du mécanisme)
```
mstsc → VM : TSRequest.negoTokens = SPNEGO NegTokenInit
             mechTypes (ordre de préférence) :
               1.2.840.48018.1.2.2    (MS Kerberos)
               1.2.840.113554.1.2.2   (Kerberos v5)
               1.3.6.1.4.1.311.2.2.10 (NTLMSSP)
```
Negotiate tente le **premier mécanisme utilisable** : Kerberos.

### 6b — Tentative Kerberos  ⚑ DÉCISION 2
Pour obtenir un ticket de service, `mstsc` adresse au KDC une demande TGS pour le SPN dérivé du
**nom contacté** :
```
mstsc → KDC : TGS-REQ
              padata = AP-REQ (TGT + Authenticator)
              req-body.sname = TERMSRV/zxr104-lb-coop-mcn.yres.ytech
KDC → mstsc : KRB-ERROR
              error-code = 7  (KDC_ERR_S_PRINCIPAL_UNKNOWN)
```
La VIP n'est enregistrée nulle part comme `servicePrincipalName` → **le KDC ne connaît pas ce
SPN**. Pas de ticket, donc pas de `AP-REQ`/`AP-REP` vers la VM, donc **pas d'authentification
mutuelle**. SPNEGO passe au mécanisme suivant : NTLM.

> Note : enregistrer `TERMSRV/<vip>` sur chaque machine du pool créerait un **SPN dupliqué** →
> Kerberos cassé (non supporté). C'est pourquoi cette voie reste NTLM tant qu'on passe par le
> nom de la VIP.

### 6c — Bascule NTLM (NTLMv2, MS-NLMP)
Trois messages, transportés dans `TSRequest.negoTokens` :
```
Type 1  NEGOTIATE     mstsc → VM
        Signature "NTLMSSP\0", MessageType=0x00000001
        NegotiateFlags : NEGOTIATE_UNICODE | NTLM | EXTENDED_SESSIONSECURITY
                         | ALWAYS_SIGN | NEGOTIATE_TARGET_INFO | 128 | 56 | KEY_EXCH ...

Type 2  CHALLENGE     VM → mstsc
        MessageType=0x00000002
        ServerChallenge (8 octets, aléatoire)
        TargetName, TargetInfo = AV_PAIRs :
            MsvAvNbComputerName, MsvAvNbDomainName,
            MsvAvDnsComputerName, MsvAvDnsDomainName,
            MsvAvTimestamp, MsvAvChannelBindings, MsvAvTargetName

Type 3  AUTHENTICATE  mstsc → VM
        MessageType=0x00000003
        NtChallengeResponse (NTLMv2) :
            NTProofStr (16 o, HMAC-MD5) + blob (timestamp + clientChallenge + AV_PAIRs)
        DomainName=YGIE  UserName=A90AD00  Workstation=<poste>
        EncryptedRandomSessionKey, MIC
```
Calcul NTLMv2 (preuve de connaissance du mot de passe) :
```
NTOWFv2  = HMAC-MD5( NThash , UPPER(UserName) || DomainName )
NTProof  = HMAC-MD5( NTOWFv2 , ServerChallenge || blob )
```
**Propriété centrale** : NTLMv2 prouve l'identité du **client au serveur**, mais **n'authentifie
pas le serveur au client** (pas d'authentification mutuelle, contrairement à `AP-REP` Kerberos).
C'est aussi ce protocole NTLM qui est en cours de dépréciation (réseau désactivé par défaut
dans une prochaine version majeure de Windows Server).

### 6d — Classification de l'authentification serveur
CredSSP étiquette la connexion selon **comment le serveur a été authentifié** :
```
certificat X.509 de confiance (phase 5 OK)  OU  Kerberos mutuel  →  étiquette « X509/Kerberos »
ni l'un ni l'autre (cert non valide ET Kerberos KO)             →  étiquette « NTLM seul »
```
**Dans votre cas** : Kerberos KO (6b) ; donc l'étiquette dépend **uniquement du certificat** :
SAN présent → « X509 » ; SAN absent → « NTLM seul ». C'est exactement votre observation
empirique.

### 6e — pubKeyAuth (liaison au canal TLS, anti-MITM)
```
mstsc → VM : TSRequest.pubKeyAuth = E_ctx( SubjectPublicKeyInfo_du_cert_TLS )
VM → mstsc : TSRequest.pubKeyAuth = E_ctx( clé_publique + 1 )   (HYBRID_EX : hash + clientNonce)
```
`E_ctx` = chiffrement/scellement par le contexte SSPI (Kerberos ou NTLM) établi en 6b/6c.
But : prouver que l'entité ayant fait SPNEGO est bien celle qui a terminé le TLS → empêche un
intercepteur de s'intercaler entre les deux couches.

### 6f — Délégation des identifiants  ⚑ DÉCISION 3
Avant d'envoyer le mot de passe, CredSSP consulte la **politique de délégation** selon
l'étiquette de 6d (clé `HKLM\SOFTWARE\Policies\Microsoft\Windows\CredentialsDelegation`) :
```
étiquette « X509/Kerberos »  →  AllowFreshCredentials
                                (par défaut, après auth mutuelle : délégation vers TERMSRV/*)
étiquette « NTLM seul »      →  AllowFreshCredentialsWhenNTLMOnly
                                (NON autorisé par défaut → à activer + liste SPN TERMSRV/*)
```
Si autorisé :
```
mstsc → VM : TSRequest.authInfo = E_ctx( TSCredentials )

TSCredentials   ::= SEQUENCE { credType [0] INTEGER, credentials [1] OCTET STRING }
TSPasswordCreds ::= SEQUENCE { domainName [0] OCTET STRING,   -- YGIE
                               userName   [1] OCTET STRING,   -- A90AD00
                               password   [2] OCTET STRING }
```
Si **refusé** → CredSSP échoue → connexion interrompue (c'est l'erreur que vous obteniez sans le
`WhenNTLMOnly` quand le SAN était absent).

---

## Phase 7 — MCS / GCC (canaux RDP)  — sur le canal désormais sécurisé

**En clair.** Une fois l'identité réglée, on ouvre les « tuyaux » logiques de RDP.

**Bas niveau (MS-RDPBCGR).**
```
mstsc → VM : MCS Connect-Initial  (BER)
             └─ GCC Conference Create Request (PER), userData :
                TS_UD_CS_CORE : version, desktopWidth/Height, colorDepth, clientBuild,
                                clientName, keyboardLayout, earlyCapabilityFlags,
                                serverSelectedProtocol (= HYBRID, recopié)
                TS_UD_CS_SEC  : encryptionMethods
                TS_UD_CS_NET  : channelDefArray (rdpdr, rdpsnd, cliprdr, drdynvc, ...)
                TS_UD_CS_CLUSTER : Flags (REDIRECTED_SESSIONID_FIELD_VALID...),
                                   RedirectedSessionID   ← infos répartition/redirection
VM → mstsc : MCS Connect-Response (BER)
             └─ GCC Conference Create Response : TS_UD_SC_CORE, TS_UD_SC_SEC1,
                TS_UD_SC_NET (identifiants de canaux attribués)
mstsc → VM : MCS Erect Domain Request
mstsc → VM : MCS Attach User Request
VM → mstsc : MCS Attach User Confirm   (User Channel ID)
mstsc ↔ VM : MCS Channel Join Request/Confirm  × (canal user + canal I/O 1003 + chaque VC)
```
(Avec sécurité RDP standard, un `Security Exchange PDU` s'intercalerait ici ; en TLS/Enhanced
il est omis car le tunnel TLS assure déjà le chiffrement.)

---

## Phase 8 — Client Info PDU + Licences

```
mstsc → VM : Client Info PDU
             TS_INFO_PACKET : CodePage, flags (INFO_AUTOLOGON, INFO_UNICODE, ...),
                              Domain=YGIE, UserName=A90AD00, [Password],
                              AlternateShell, WorkingDir,
                              extraInfo (clientAddress, clientDir, timezone, ...)
VM → mstsc : Server License Error PDU (STATUS_VALID_CLIENT / phase de licence si nécessaire)
```
Avec NLA, les identifiants ont déjà été délégués (6f) ; le `Client Info PDU` porte les
informations d'ouverture de session/autologon.

---

## Phase 9 — Échange de capacités

```
VM → mstsc : Demand Active PDU    (Capability Sets serveur : General, Bitmap, Order,
                                   Pointer, Input, VirtualChannel, Surface Commands, ...)
mstsc → VM : Confirm Active PDU   (Capability Sets client)
```

---

## Phase 10 — Finalisation de la connexion

```
mstsc ↔ VM : Synchronize PDU
mstsc ↔ VM : Control PDU (Cooperate)
mstsc → VM : Control PDU (Request Control)
VM → mstsc : Control PDU (Granted Control)
mstsc → VM : Font List PDU
VM → mstsc : Font Map PDU
```
À partir d'ici, la session est active : la VM valide l'ouverture auprès du DC et crée la session
de bureau.

---

## Phase 11 — Session active

```
VM → mstsc : Fast-Path Update PDUs   (graphismes : Bitmap/Surface Commands, ou RDPGFX
                                       sur le canal dynamique drdynvc)
mstsc → VM : Fast-Path Input PDUs     (clavier, souris)
mstsc ↔ VM : Virtual Channel PDUs     (cliprdr presse-papiers, rdpdr lecteurs, rdpsnd son, ...)
```
Note : votre VIP « sans son » est un relais L4 ; le son passe par le canal virtuel `rdpsnd`
chiffré dans le tunnel, donc invisible/ininspectable côté relais — sa présence dépend des
capacités (phase 9) et des stratégies, pas du relais lui-même.

---

## Récapitulatif : les 3 décisions et votre panne

| Décision | Phase | Mécanisme exact | Effet de votre montage |
|---|---|---|---|
| Nom du certificat | 5 | validation X.509 du SNI vs SAN | sans SAN VIP → échec nom ; conduite réglée par `AuthenticationLevel` |
| Kerberos vs NTLM | 6b–6c | TGS-REQ → `KDC_ERR_S_PRINCIPAL_UNKNOWN` → NTLMv2 | la VIP n'a pas de SPN → **toujours NTLM** pour l'auth utilisateur |
| Délégation | 6f | politique selon étiquette 6d | étiquette « NTLM seul » → exige `AllowFreshCredentialsWhenNTLMOnly` |

**Lecture transversale.** Le SAN agit en phase 5 (auth **serveur**, donc choix de la *politique*
de délégation via 6d). Le SPN agit en phase 6b (auth **utilisateur**, donc choix Kerberos/NTLM
*protocole*). Ce sont deux leviers distincts :
- un **certificat valide** (SAN VIP ou wildcard scopé) vous met sur l'étiquette « X509 » → plus
  besoin du `WhenNTLMOnly`, et auth serveur réellement vérifiée ;
- mais l'auth **utilisateur** reste NTLM tant que la cible n'est pas adressable en Kerberos →
  pour s'affranchir de la dépréciation NTLM, il faut un SPN résoluble (broker avec redirection
  vers le nom réel de la VM, ou IAKerb), pas un certificat.
