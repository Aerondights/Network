#!/usr/bin/env python3
"""
Script professionnel pour gérer l'alimentation des VMs vCenter via API REST.
Auteur: Script automatisé
Version: 2.0.0
"""

import csv
import json
import logging
import sys
import time
import ssl
import socket
from datetime import datetime
from argparse import ArgumentParser
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

# Désactiver les avertissements SSL non vérifiés uniquement si explicitement demandé
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class PowerAction(Enum):
    """Actions d'alimentation disponibles."""
    POWER_ON = "start"
    POWER_OFF = "stop"
    SHUTDOWN = "shutdown"  # Shutdown gracieux
    RESET = "reset"
    SUSPEND = "suspend"


class VMStatus(Enum):
    """États possibles d'une VM."""
    POWERED_ON = "POWERED_ON"
    POWERED_OFF = "POWERED_OFF"
    SUSPENDED = "SUSPENDED"


@dataclass
class VMOperation:
    """Représente une opération sur une VM."""
    vm_name: str
    action: PowerAction
    success: bool = False
    message: str = ""
    duration: float = 0.0


@dataclass
class SSLCertificateInfo:
    """Informations sur le certificat SSL."""
    subject: Dict
    issuer: Dict
    version: int
    serial_number: str
    not_before: str
    not_after: str
    is_valid: bool
    days_remaining: int


class SSLVerifier:
    """Classe pour vérifier et valider les certificats SSL."""
    
    def __init__(self, host: str, port: int = 443):
        """
        Initialise le vérificateur SSL.
        
        Args:
            host: Hôte à vérifier
            port: Port SSL
        """
        self.host = host
        self.port = port
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def get_certificate_info(self) -> Optional[SSLCertificateInfo]:
        """
        Récupère les informations du certificat SSL.
        
        Returns:
            Informations du certificat ou None
        """
        try:
            context = ssl.create_default_context()
            
            with socket.create_connection((self.host, self.port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=self.host) as ssock:
                    cert = ssock.getpeercert()
                    
                    # Parsing des dates - essayer différents formats
                    date_formats = [
                        '%b %d %H:%M:%S %Y %Z',  # Format standard: "Jul 29 06:30:39 2025 GMT"
                        '%b %d %H:%M:%S %Y GMT',  # Sans %Z: "Jul 29 06:30:39 2025 GMT"
                        '%b  %d %H:%M:%S %Y %Z', # Avec double espace
                        '%b  %d %H:%M:%S %Y GMT', # Avec double espace sans %Z
                    ]
                    
                    not_before = None
                    not_after = None
                    
                    for fmt in date_formats:
                        try:
                            not_before = datetime.strptime(cert['notBefore'], fmt)
                            not_after = datetime.strptime(cert['notAfter'], fmt)
                            break
                        except ValueError:
                            continue
                    
                    if not not_before or not not_after:
                        # Si aucun format ne fonctionne, essayer une approche différente
                        self.logger.warning(f"Format de date non reconnu: {cert['notBefore']}")
                        # Retirer le timezone et réessayer
                        try:
                            date_str_before = cert['notBefore'].replace(' GMT', '').replace(' UTC', '')
                            date_str_after = cert['notAfter'].replace(' GMT', '').replace(' UTC', '')
                            not_before = datetime.strptime(date_str_before, '%b %d %H:%M:%S %Y')
                            not_after = datetime.strptime(date_str_after, '%b %d %H:%M:%S %Y')
                        except ValueError as e:
                            self.logger.error(f"Impossible de parser les dates du certificat: {e}")
                            return None
                    
                    now = datetime.now()
                    
                    # Calcul de la validité
                    is_valid = not_before <= now <= not_after
                    days_remaining = (not_after - now).days
                    
                    # Extraction du sujet et de l'émetteur
                    subject = dict(x[0] for x in cert['subject'])
                    issuer = dict(x[0] for x in cert['issuer'])
                    
                    cert_info = SSLCertificateInfo(
                        subject=subject,
                        issuer=issuer,
                        version=cert['version'],
                        serial_number=cert['serialNumber'],
                        not_before=cert['notBefore'],
                        not_after=cert['notAfter'],
                        is_valid=is_valid,
                        days_remaining=days_remaining
                    )
                    
                    return cert_info
                    
        except ssl.SSLError as e:
            self.logger.error(f"Erreur SSL lors de la vérification du certificat: {e}")
            return None
        except socket.timeout:
            self.logger.error(f"Timeout lors de la connexion à {self.host}:{self.port}")
            return None
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du certificat: {e}")
            return None
    
    def verify_certificate(self, strict: bool = True) -> Tuple[bool, str]:
        """
        Vérifie la validité du certificat SSL.
        
        Args:
            strict: Mode strict (rejette les certificats auto-signés)
            
        Returns:
            Tuple (valide, message)
        """
        cert_info = self.get_certificate_info()
        
        if not cert_info:
            return False, "Impossible de récupérer le certificat SSL"
        
        # Vérifier la validité temporelle
        if not cert_info.is_valid:
            return False, f"Certificat expiré ou pas encore valide (valide du {cert_info.not_before} au {cert_info.not_after})"
        
        # Avertissement si le certificat expire bientôt
        if cert_info.days_remaining < 30:
            self.logger.warning(f"⚠️  Le certificat expire dans {cert_info.days_remaining} jours")
        
        # En mode strict, vérifier si c'est un certificat auto-signé
        if strict and cert_info.subject == cert_info.issuer:
            return False, "Certificat auto-signé détecté (utilisez --allow-self-signed pour continuer)"
        
        # Afficher les informations du certificat
        self.logger.info(f"✓ Certificat SSL valide")
        self.logger.info(f"  Sujet: {cert_info.subject.get('commonName', 'N/A')}")
        self.logger.info(f"  Émetteur: {cert_info.issuer.get('commonName', 'N/A')}")
        self.logger.info(f"  Valide jusqu'au: {cert_info.not_after}")
        self.logger.info(f"  Jours restants: {cert_info.days_remaining}")
        
        return True, "Certificat SSL valide"


class VCenterAPIClient:
    """Client API REST pour vCenter."""
    
    def __init__(self, host: str, username: str, password: str, 
                 verify_ssl: bool = True, allow_self_signed: bool = False,
                 timeout: int = 30):
        """
        Initialise le client vCenter API.
        
        Args:
            host: URL du vCenter (ex: vcenter.example.com)
            username: Nom d'utilisateur
            password: Mot de passe
            verify_ssl: Vérifier le certificat SSL
            allow_self_signed: Autoriser les certificats auto-signés
            timeout: Timeout des requêtes en secondes
        """
        self.host = host.rstrip('/')
        # API REST vCenter utilise /rest pour les nouvelles API et /api pour les anciennes
        self.rest_url = f"https://{self.host}/rest"
        self.api_url = f"https://{self.host}/api"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.allow_self_signed = allow_self_signed
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Configuration de la session avec retry
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Crée une session avec retry automatique."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "DELETE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
    
    def verify_ssl_certificate(self) -> bool:
        """
        Vérifie le certificat SSL avant la connexion.
        
        Returns:
            True si le certificat est valide, False sinon
        """
        if not self.verify_ssl:
            self.logger.warning("⚠️  Vérification SSL désactivée - connexion non sécurisée!")
            return True
        
        self.logger.info(f"Vérification du certificat SSL pour {self.host}...")
        
        verifier = SSLVerifier(self.host, 443)
        is_valid, message = verifier.verify_certificate(strict=not self.allow_self_signed)
        
        if not is_valid:
            self.logger.error(f"✗ {message}")
            return False
        
        return True
    
    def connect(self) -> bool:
        """
        Établit une connexion et récupère un token de session.
        Utilise l'endpoint correct pour vCenter REST API.
        
        Returns:
            True si succès, False sinon
        """
        # Vérification SSL avant connexion
        if not self.verify_ssl_certificate():
            return False
        
        try:
            # Tentative avec l'API /rest/com/vmware/cis/session (vCenter 6.5+)
            url = f"{self.rest_url}/com/vmware/cis/session"
            
            self.logger.info(f"Tentative de connexion à {self.host}...")
            self.logger.debug(f"URL de connexion: {url}")
            
            response = self.session.post(
                url,
                auth=(self.username, self.password),
                verify=self.verify_ssl,
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'}
            )
            
            # Si échec, essayer l'ancienne API
            if response.status_code == 404:
                self.logger.debug("Endpoint /rest non disponible, essai avec /api...")
                url = f"{self.api_url}/session"
                response = self.session.post(
                    url,
                    auth=(self.username, self.password),
                    verify=self.verify_ssl,
                    timeout=self.timeout
                )
            
            response.raise_for_status()
            
            # Récupération du token de session
            result = response.json()
            
            # Le format de réponse peut varier selon la version de l'API
            if isinstance(result, dict) and 'value' in result:
                self.session_id = result['value']
            elif isinstance(result, str):
                self.session_id = result
            else:
                self.logger.error(f"Format de réponse inattendu: {result}")
                return False
            
            # Configuration des headers pour les requêtes suivantes
            self.session.headers.update({
                'vmware-api-session-id': self.session_id,
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            })
            
            self.logger.info(f"✓ Connexion réussie à {self.host}")
            self.logger.debug(f"Session ID: {self.session_id[:20]}...")
            
            return True
            
        except requests.exceptions.SSLError as e:
            self.logger.error(f"✗ Erreur SSL: {e}")
            self.logger.error("Utilisez --no-verify-ssl pour ignorer la vérification SSL (non recommandé)")
            return False
        except requests.exceptions.HTTPError as e:
            self.logger.error(f"✗ Erreur HTTP {e.response.status_code}: {e}")
            if e.response.status_code == 401:
                self.logger.error("Identifiants invalides")
            elif e.response.status_code == 403:
                self.logger.error("Accès refusé - vérifiez les permissions")
            try:
                error_detail = e.response.json()
                self.logger.error(f"Détails: {error_detail}")
            except:
                pass
            return False
        except requests.exceptions.ConnectionError as e:
            self.logger.error(f"✗ Erreur de connexion à {self.host}: {e}")
            self.logger.error("Vérifiez que le serveur est accessible et que le port 443 est ouvert")
            return False
        except requests.exceptions.Timeout:
            self.logger.error(f"✗ Timeout lors de la connexion (>{self.timeout}s)")
            return False
        except requests.exceptions.RequestException as e:
            self.logger.error(f"✗ Échec de connexion: {e}")
            return False
        except Exception as e:
            self.logger.error(f"✗ Erreur inattendue: {e}", exc_info=True)
            return False
    
    def disconnect(self) -> None:
        """Ferme la session vCenter."""
        if self.session_id:
            try:
                # Essayer l'API /rest d'abord
                url = f"{self.rest_url}/com/vmware/cis/session"
                try:
                    self.session.delete(url, verify=self.verify_ssl, timeout=self.timeout)
                except requests.exceptions.HTTPError:
                    # Si échec, essayer l'ancienne API
                    url = f"{self.api_url}/session"
                    self.session.delete(url, verify=self.verify_ssl, timeout=self.timeout)
                
                self.logger.info("✓ Déconnexion réussie")
            except Exception as e:
                self.logger.warning(f"Erreur lors de la déconnexion: {e}")
            finally:
                self.session_id = None
    
    def get_vm_by_name(self, vm_name: str) -> Optional[str]:
        """
        Récupère l'ID d'une VM par son nom.
        
        Args:
            vm_name: Nom de la VM
            
        Returns:
            ID de la VM ou None
        """
        try:
            # Utiliser l'API /rest pour vCenter 6.5+
            url = f"{self.rest_url}/vcenter/vm"
            params = {'filter.names': vm_name}
            
            response = self.session.get(
                url,
                params=params,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            
            # Si échec, essayer l'ancienne API
            if response.status_code == 404:
                url = f"{self.api_url}/vcenter/vm"
                params = {'names': vm_name}
                response = self.session.get(
                    url,
                    params=params,
                    verify=self.verify_ssl,
                    timeout=self.timeout
                )
            
            response.raise_for_status()
            
            result = response.json()
            
            # Gérer les différents formats de réponse
            vms = result.get('value', result) if isinstance(result, dict) else result
            
            if vms and len(vms) > 0:
                return vms[0].get('vm', vms[0].get('id'))
            
            self.logger.warning(f"VM '{vm_name}' non trouvée")
            return None
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la recherche de VM '{vm_name}': {e}")
            return None
    
    def get_vm_power_state(self, vm_id: str) -> Optional[VMStatus]:
        """
        Récupère l'état d'alimentation d'une VM.
        
        Args:
            vm_id: ID de la VM
            
        Returns:
            État de la VM ou None
        """
        try:
            url = f"{self.rest_url}/vcenter/vm/{vm_id}/power"
            response = self.session.get(
                url,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            
            # Si échec, essayer l'ancienne API
            if response.status_code == 404:
                url = f"{self.api_url}/vcenter/vm/{vm_id}/power"
                response = self.session.get(
                    url,
                    verify=self.verify_ssl,
                    timeout=self.timeout
                )
            
            response.raise_for_status()
            
            result = response.json()
            state = result.get('value', {}).get('state', result.get('state'))
            
            return VMStatus(state) if state else None
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération de l'état VM {vm_id}: {e}")
            return None
    
    def execute_power_action(self, vm_id: str, action: PowerAction) -> bool:
        """
        Exécute une action d'alimentation sur une VM.
        
        Args:
            vm_id: ID de la VM
            action: Action à exécuter
            
        Returns:
            True si succès, False sinon
        """
        try:
            url = f"{self.rest_url}/vcenter/vm/{vm_id}/power/{action.value}"
            response = self.session.post(
                url,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            
            # Si échec, essayer l'ancienne API
            if response.status_code == 404:
                url = f"{self.api_url}/vcenter/vm/{vm_id}/power/{action.value}"
                response = self.session.post(
                    url,
                    verify=self.verify_ssl,
                    timeout=self.timeout
                )
            
            response.raise_for_status()
            return True
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                self.logger.warning(f"Action {action.value} non applicable pour VM {vm_id}")
            else:
                self.logger.error(f"Erreur HTTP {e.response.status_code}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Erreur lors de l'action {action.value} sur VM {vm_id}: {e}")
            return False
    
    def wait_for_power_state(self, vm_id: str, target_state: VMStatus, 
                            max_wait: int = 300, poll_interval: int = 5) -> bool:
        """
        Attend qu'une VM atteigne un état spécifique.
        
        Args:
            vm_id: ID de la VM
            target_state: État cible
            max_wait: Temps d'attente maximum en secondes
            poll_interval: Intervalle de polling en secondes
            
        Returns:
            True si état atteint, False sinon
        """
        start_time = time.time()
        
        while (time.time() - start_time) < max_wait:
            current_state = self.get_vm_power_state(vm_id)
            
            if current_state == target_state:
                return True
            
            time.sleep(poll_interval)
        
        return False


class VMPowerManager:
    """Gestionnaire principal pour les opérations d'alimentation des VMs."""
    
    def __init__(self, vcenter_client: VCenterAPIClient, max_workers: int = 10):
        """
        Initialise le gestionnaire.
        
        Args:
            vcenter_client: Client vCenter API
            max_workers: Nombre maximum de workers parallèles
        """
        self.client = vcenter_client
        self.max_workers = max_workers
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def read_csv(self, csv_file: Path) -> List[Tuple[str, PowerAction]]:
        """
        Lit le fichier CSV et retourne les opérations à effectuer.
        
        Format CSV attendu:
        vm_name,action
        vm1,power_on
        vm2,power_off
        
        Args:
            csv_file: Chemin du fichier CSV
            
        Returns:
            Liste de tuples (vm_name, action)
        """
        operations = []
        
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                if 'vm_name' not in reader.fieldnames or 'action' not in reader.fieldnames:
                    raise ValueError("Le CSV doit contenir les colonnes 'vm_name' et 'action'")
                
                for row_num, row in enumerate(reader, start=2):
                    vm_name = row['vm_name'].strip()
                    action_str = row['action'].strip().upper()
                    
                    if not vm_name:
                        self.logger.warning(f"Ligne {row_num}: nom de VM vide, ignoré")
                        continue
                    
                    try:
                        action = PowerAction[action_str]
                        operations.append((vm_name, action))
                    except KeyError:
                        self.logger.warning(
                            f"Ligne {row_num}: action '{action_str}' invalide pour VM '{vm_name}', ignoré"
                        )
                        continue
            
            self.logger.info(f"{len(operations)} opérations chargées depuis {csv_file}")
            return operations
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la lecture du CSV: {e}")
            return []
    
    def process_vm(self, vm_name: str, action: PowerAction, 
                   wait_for_state: bool = True) -> VMOperation:
        """
        Traite une opération sur une VM.
        
        Args:
            vm_name: Nom de la VM
            action: Action à exécuter
            wait_for_state: Attendre que l'état soit atteint
            
        Returns:
            Résultat de l'opération
        """
        start_time = time.time()
        operation = VMOperation(vm_name=vm_name, action=action)
        
        try:
            # Récupérer l'ID de la VM
            vm_id = self.client.get_vm_by_name(vm_name)
            if not vm_id:
                operation.message = "VM non trouvée"
                return operation
            
            # Vérifier l'état actuel
            current_state = self.client.get_vm_power_state(vm_id)
            if not current_state:
                operation.message = "Impossible de récupérer l'état de la VM"
                return operation
            
            # Déterminer l'état cible
            target_state_map = {
                PowerAction.POWER_ON: VMStatus.POWERED_ON,
                PowerAction.POWER_OFF: VMStatus.POWERED_OFF,
                PowerAction.SHUTDOWN: VMStatus.POWERED_OFF,
                PowerAction.SUSPEND: VMStatus.SUSPENDED,
            }
            target_state = target_state_map.get(action)
            
            # Vérifier si l'action est nécessaire
            if current_state == target_state:
                operation.success = True
                operation.message = f"VM déjà dans l'état {target_state.value}"
                return operation
            
            # Exécuter l'action
            if not self.client.execute_power_action(vm_id, action):
                operation.message = f"Échec de l'exécution de {action.value}"
                return operation
            
            # Attendre l'état cible si demandé
            if wait_for_state and target_state:
                if self.client.wait_for_power_state(vm_id, target_state):
                    operation.success = True
                    operation.message = f"Action {action.value} réussie"
                else:
                    operation.message = f"Timeout en attendant l'état {target_state.value}"
            else:
                operation.success = True
                operation.message = f"Action {action.value} lancée"
            
        except Exception as e:
            operation.message = f"Erreur: {str(e)}"
            self.logger.error(f"Erreur lors du traitement de {vm_name}: {e}")
        
        finally:
            operation.duration = time.time() - start_time
        
        return operation
    
    def process_batch(self, operations: List[Tuple[str, PowerAction]], 
                     wait_for_state: bool = True) -> List[VMOperation]:
        """
        Traite un lot d'opérations en parallèle.
        
        Args:
            operations: Liste d'opérations (vm_name, action)
            wait_for_state: Attendre que l'état soit atteint
            
        Returns:
            Liste des résultats
        """
        results = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.process_vm, vm_name, action, wait_for_state): (vm_name, action)
                for vm_name, action in operations
            }
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    
                    status = "✓" if result.success else "✗"
                    self.logger.info(
                        f"{status} {result.vm_name}: {result.message} "
                        f"({result.duration:.2f}s)"
                    )
                    
                except Exception as e:
                    vm_name, action = futures[future]
                    self.logger.error(f"Exception pour {vm_name}: {e}")
                    results.append(VMOperation(
                        vm_name=vm_name,
                        action=action,
                        success=False,
                        message=f"Exception: {str(e)}"
                    ))
        
        return results
    
    def generate_report(self, results: List[VMOperation], output_file: Optional[Path] = None) -> None:
        """
        Génère un rapport des opérations.
        
        Args:
            results: Liste des résultats
            output_file: Fichier de sortie (optionnel)
        """
        total = len(results)
        success = sum(1 for r in results if r.success)
        failed = total - success
        total_duration = sum(r.duration for r in results)
        
        report = [
            "\n" + "=" * 80,
            "RAPPORT D'EXÉCUTION",
            "=" * 80,
            f"Total d'opérations: {total}",
            f"Succès: {success}",
            f"Échecs: {failed}",
            f"Durée totale: {total_duration:.2f}s",
            "=" * 80,
            "\nDÉTAILS:",
        ]
        
        for result in results:
            status = "SUCCÈS" if result.success else "ÉCHEC"
            report.append(
                f"  [{status}] {result.vm_name} ({result.action.value}): "
                f"{result.message} - {result.duration:.2f}s"
            )
        
        report.append("=" * 80 + "\n")
        
        report_text = "\n".join(report)
        print(report_text)
        
        if output_file:
            try:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(report_text)
                self.logger.info(f"Rapport sauvegardé dans {output_file}")
            except Exception as e:
                self.logger.error(f"Erreur lors de la sauvegarde du rapport: {e}")


def setup_logging(log_level: str = "INFO", log_file: Optional[Path] = None) -> None:
    """Configure le système de logging."""
    handlers = [logging.StreamHandler(sys.stdout)]
    
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


def main():
    """Point d'entrée principal du script."""
    parser = ArgumentParser(description="Gestion de l'alimentation des VMs vCenter via API REST")
    parser.add_argument('--host', required=True, help="Hôte vCenter (ex: vcenter.example.com)")
    parser.add_argument('--username', required=True, help="Nom d'utilisateur vCenter")
    parser.add_argument('--password', required=True, help="Mot de passe vCenter")
    parser.add_argument('--csv', required=True, type=Path, help="Fichier CSV d'entrée")
    parser.add_argument('--verify-ssl', action='store_true', default=True, 
                       help="Vérifier le certificat SSL (activé par défaut)")
    parser.add_argument('--no-verify-ssl', action='store_true', 
                       help="Désactiver la vérification SSL (non recommandé)")
    parser.add_argument('--allow-self-signed', action='store_true',
                       help="Autoriser les certificats auto-signés")
    parser.add_argument('--no-wait', action='store_true', help="Ne pas attendre la fin des opérations")
    parser.add_argument('--workers', type=int, default=10, help="Nombre de workers parallèles")
    parser.add_argument('--timeout', type=int, default=30, help="Timeout des requêtes (secondes)")
    parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    parser.add_argument('--log-file', type=Path, help="Fichier de log")
    parser.add_argument('--report', type=Path, help="Fichier de rapport de sortie")
    
    args = parser.parse_args()
    
    # Gérer les options SSL contradictoires
    verify_ssl = args.verify_ssl and not args.no_verify_ssl
    
    # Configuration du logging
    setup_logging(args.log_level, args.log_file)
    logger = logging.getLogger(__name__)
    
    # Vérification du fichier CSV
    if not args.csv.exists():
        logger.error(f"Fichier CSV introuvable: {args.csv}")
        sys.exit(1)
    
    # Avertissement de sécurité
    if not verify_ssl:
        logger.warning("⚠️  " + "=" * 70)
        logger.warning("⚠️  AVERTISSEMENT DE SÉCURITÉ")
        logger.warning("⚠️  La vérification SSL est désactivée!")
        logger.warning("⚠️  Vos identifiants et données peuvent être interceptés!")
        logger.warning("⚠️  Utilisez cette option uniquement dans un environnement de test")
        logger.warning("⚠️  " + "=" * 70)
        
        response = input("Voulez-vous continuer malgré ce risque de sécurité? (oui/non): ")
        if response.lower() not in ['oui', 'yes', 'o', 'y']:
            logger.info("Opération annulée par l'utilisateur")
            sys.exit(0)
    
    try:
        # Initialisation du client vCenter
        client = VCenterAPIClient(
            host=args.host,
            username=args.username,
            password=args.password,
            verify_ssl=verify_ssl,
            allow_self_signed=args.allow_self_signed,
            timeout=args.timeout
        )
        
        # Connexion
        if not client.connect():
            logger.error("✗ Impossible de se connecter à vCenter")
            logger.error("\nConseils de dépannage:")
            logger.error("  1. Vérifiez l'URL du vCenter (sans https://)")
            logger.error("  2. Vérifiez vos identifiants")
            logger.error("  3. Vérifiez que le port 443 est accessible")
            logger.error("  4. Si certificat auto-signé, utilisez --allow-self-signed")
            logger.error("  5. Pour environnement de test uniquement: --no-verify-ssl")
            sys.exit(1)
        
        try:
            # Initialisation du gestionnaire
            manager = VMPowerManager(client, max_workers=args.workers)
            
            # Lecture du CSV
            operations = manager.read_csv(args.csv)
            if not operations:
                logger.warning("Aucune opération à effectuer")
                sys.exit(0)
            
            # Traitement des opérations
            logger.info(f"Traitement de {len(operations)} opérations...")
            results = manager.process_batch(operations, wait_for_state=not args.no_wait)
            
            # Génération du rapport
            manager.generate_report(results, args.report)
            
            # Code de sortie basé sur les résultats
            failed_count = sum(1 for r in results if not r.success)
            sys.exit(0 if failed_count == 0 else 1)
            
        finally:
            # Déconnexion
            client.disconnect()
            
    except KeyboardInterrupt:
        logger.warning("Interruption par l'utilisateur")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Erreur fatale: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()