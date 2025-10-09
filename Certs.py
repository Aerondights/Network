import os
import zipfile
import tempfile
import certifi
import ssl
from pathlib import Path
from typing import Optional

def download_and_install_vcenter_certificates(
    vcenter_host: str,
    cert_url: Optional[str] = None,
    verify_download: bool = False
) -> bool:
    """
    Télécharge et installe les certificats vCenter dans le bundle certifi.
    
    Args:
        vcenter_host: Hôte vCenter (ex: mondomaine.res)
        cert_url: URL de téléchargement des certificats (défaut: https://{vcenter_host}/download/cert.zip)
        verify_download: Vérifier SSL lors du téléchargement (False pour certificats auto-signés)
    
    Returns:
        True si succès, False sinon
    """
    import requests
    
    logger = logging.getLogger(__name__)
    
    # URL par défaut si non spécifiée
    if not cert_url:
        cert_url = f"https://{vcenter_host}/download/cert.zip"
    
    logger.info(f"Téléchargement des certificats depuis {cert_url}")
    
    try:
        # Créer un répertoire temporaire
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / "cert.zip"
            extract_path = temp_path / "certs"
            
            # Télécharger le fichier ZIP
            logger.debug(f"Téléchargement vers {zip_path}")
            response = requests.get(cert_url, verify=verify_download, timeout=30)
            response.raise_for_status()
            
            # Sauvegarder le ZIP
            with open(zip_path, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"✓ Certificats téléchargés ({len(response.content)} octets)")
            
            # Dézipper
            logger.debug(f"Extraction vers {extract_path}")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            
            logger.info("✓ Archive extraite")
            
            # Chemin vers les certificats Windows
            win_certs_path = extract_path / "certs" / "win"
            
            if not win_certs_path.exists():
                logger.error(f"Dossier certs/win introuvable dans l'archive")
                return False
            
            # Récupérer le chemin du bundle certifi
            certifi_bundle = certifi.where()
            logger.debug(f"Bundle certifi: {certifi_bundle}")
            
            # Compter les certificats ajoutés
            certs_added = 0
            
            # Lire tous les fichiers .crt ou .pem dans certs/win
            cert_extensions = ['.crt', '.cer', '.pem']
            cert_files = []
            
            for ext in cert_extensions:
                cert_files.extend(win_certs_path.glob(f"*{ext}"))
            
            if not cert_files:
                logger.warning(f"Aucun certificat trouvé dans {win_certs_path}")
                return False
            
            logger.info(f"Trouvé {len(cert_files)} certificat(s) à installer")
            
            # Ouvrir le bundle certifi en mode append
            with open(certifi_bundle, 'a') as bundle:
                bundle.write("\n# vCenter Custom Certificates\n")
                bundle.write(f"# Ajouté automatiquement le {datetime.now().isoformat()}\n")
                bundle.write(f"# Source: {cert_url}\n\n")
                
                for cert_file in cert_files:
                    try:
                        logger.debug(f"Traitement de {cert_file.name}")
                        
                        # Lire le contenu du certificat
                        cert_content = cert_file.read_text(encoding='utf-8')
                        
                        # Vérifier que c'est un certificat valide
                        if '-----BEGIN CERTIFICATE-----' not in cert_content:
                            logger.warning(f"Fichier {cert_file.name} ne contient pas de certificat valide")
                            continue
                        
                        # Ajouter un séparateur et le certificat
                        bundle.write(f"\n# {cert_file.name}\n")
                        bundle.write(cert_content)
                        if not cert_content.endswith('\n'):
                            bundle.write('\n')
                        
                        certs_added += 1
                        logger.debug(f"✓ Certificat {cert_file.name} ajouté")
                        
                    except Exception as e:
                        logger.error(f"Erreur lors de l'ajout de {cert_file.name}: {e}")
                        continue
            
            if certs_added > 0:
                logger.info(f"✓ {certs_added} certificat(s) installé(s) dans {certifi_bundle}")
                
                # Vérifier que le bundle est toujours valide
                try:
                    ssl.create_default_context(cafile=certifi_bundle)
                    logger.info("✓ Bundle certifi validé")
                except Exception as e:
                    logger.error(f"Le bundle certifi est corrompu: {e}")
                    return False
                
                return True
            else:
                logger.error("Aucun certificat n'a pu être installé")
                return False
                
    except requests.exceptions.RequestException as e:
        logger.error(f"Erreur lors du téléchargement des certificats: {e}")
        return False
    except zipfile.BadZipFile:
        logger.error("Le fichier téléchargé n'est pas un ZIP valide")
        return False
    except Exception as e:
        logger.error(f"Erreur lors de l'installation des certificats: {e}")
        return False


def verify_vcenter_certificate_installed(vcenter_host: str) -> bool:
    """
    Vérifie si le certificat vCenter est installé et fonctionnel.
    
    Args:
        vcenter_host: Hôte vCenter
    
    Returns:
        True si le certificat est valide, False sinon
    """
    logger = logging.getLogger(__name__)
    
    try:
        import socket
        
        context = ssl.create_default_context(cafile=certifi.where())
        
        with socket.create_connection((vcenter_host, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=vcenter_host) as ssock:
                logger.info(f"✓ Certificat vCenter vérifié avec succès")
                cert = ssock.getpeercert()
                subject = dict(x[0] for x in cert['subject'])
                logger.debug(f"Certificat CN: {subject.get('commonName', 'N/A')}")
                return True
                
    except ssl.SSLError as e:
        logger.error(f"Erreur SSL lors de la vérification: {e}")
        return False
    except Exception as e:
        logger.error(f"Erreur lors de la vérification du certificat: {e}")
        return False