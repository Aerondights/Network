#!/usr/bin/env python3
import argparse
import csv
import json
import logging
import os
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configuration du logging
def setup_logging(log_file: str = "vm_power_management.log"):
    logger = logging.getLogger("VSphereManager")
    logger.setLevel(logging.DEBUG)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # Handler Fichier
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    # Handler Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    return logger

logger = setup_logging()

class VSphereClient:
    """Client REST pour VMware vSphere gérant l'authentification et les opérations sur les VMs."""
    
    ACTIONS = ['power_on', 'power_off', 'reboot', 'reset', 'shutdown_guest']
    STATE_ACTION_MAP = {
        'power_on': 'POWERED_ON',
        'power_off': 'POWERED_OFF',
        'reboot': 'POWERED_ON', # Nécessite d'être allumé
        'reset': 'POWERED_ON',  # Nécessite d'être allumé
        'shutdown_guest': 'POWERED_ON' # Nécessite d'être allumé
    }

    def __init__(self, vcenter: str, username: str, password: str, ca_cert: str, timeout: int = 30):
        self.vcenter = vcenter
        self.username = username
        self.password = password
        self.ca_cert = ca_cert
        self.timeout = timeout
        self.base_url = f"https://{self.vcenter}/rest"
        self.session_id: Optional[str] = None
        
        # Configuration de la session avec Retry et Timeout
        self.session = requests.Session()
        self.session.verify = self.ca_cert
        self.session.timeout = self.timeout
        
        retry_strategy = Retry(
            total=2,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json() if response.text else {}
        except requests.exceptions.SSLError as e:
            logger.error(f"Erreur SSL lors de la requête vers {url}: {e}")
            raise
        except requests.exceptions.Timeout:
            logger.error(f"Timeout ({self.timeout}s) atteint pour {url}")
            raise
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error {response.status_code} pour {url}: {response.text}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur réseau vers {url}: {e}")
            raise

    def authenticate(self):
        """Authentification via l'API REST et récupération du token de session."""
        logger.info(f"Authentification au vCenter {self.vcenter}...")
        endpoint = "/com/vmware/cis/session"
        try:
            resp = self._request('POST', endpoint, auth=(self.username, self.password))
            self.session_id = resp.get('value')
            if not self.session_id:
                raise ValueError("Impossible de récupérer le session ID.")
            self.session.headers.update({'vmware-api-session-id': self.session_id})
            logger.info("Authentification réussie.")
        except Exception as e:
            logger.error("Échec de l'authentification.")
            raise

    def get_vm_id(self, vm_name: str) -> Optional[str]:
        """Recherche l'ID d'une VM par son nom."""
        endpoint = f"/vcenter/vm?names={vm_name}"
        try:
            resp = self._request('GET', endpoint)
            vms = resp.get('value', [])
            if not vms:
                logger.warning(f"VM '{vm_name}' non trouvée.")
                return None
            vm_id = vms[0].get('vm')
            logger.debug(f"VM '{vm_name}' trouvée avec l'ID: {vm_id}")
            return vm_id
        except Exception:
            return None

    def get_vm_power_state(self, vm_id: str) -> Optional[str]:
        """Récupère l'état d'alimentation d'une VM."""
        endpoint = f"/vcenter/vm/{vm_id}/power"
        try:
            resp = self._request('GET', endpoint)
            return resp.get('value', {}).get('state')
        except Exception:
            return None

    def perform_action(self, vm_id: str, action: str, dry_run: bool = False) -> Tuple[str, str]:
        """Exécute l'action d'alimentation si l'état le permet."""
        current_state = self.get_vm_power_state(vm_id)
        if not current_state:
            return "FAILED", "Impossible de récupérer l'état actuel de la VM"

        target_state = self.STATE_ACTION_MAP.get(action)

        # Logique d'évitement (SKIP)
        if action == 'power_on' and current_state == 'POWERED_ON':
            return "SKIPPED", f"Already powered on"
        if action in ['power_off', 'shutdown_guest'] and current_state == 'POWERED_OFF':
            return "SKIPPED", f"Already powered off"
        if action in ['reboot', 'reset', 'shutdown_guest'] and current_state == 'POWERED_OFF':
            return "SKIPPED", f"VM is powered off, cannot apply {action}"

        if dry_run:
            return "SUCCESS", f"Dry run: Action '{action}' would be executed (Current state: {current_state})"

        # Exécution de l'action
        endpoint = f"/vcenter/vm/{vm_id}/power?action={action}"
        try:
            self._request('POST', endpoint)
            return "SUCCESS", f"Action '{action}' executed successfully"
        except Exception as e:
            return "FAILED", str(e)

def validate_ssl_cert(ca_cert_path: str):
    """Vérifie la validité basique du fichier certificat CA."""
    if not os.path.exists(ca_cert_path):
        raise FileNotFoundError(f"Le fichier certificat CA est introuvable : {ca_cert_path}")
    try:
        with open(ca_cert_path, 'r') as f:
            cert_data = f.read()
        # Validation du format PEM
        if "-----BEGIN CERTIFICATE-----" not in cert_data:
            raise ValueError("Le fichier ne semble pas être un certificat PEM valide.")
        logger.info(f"Certificat CA validé : {ca_cert_path}")
    except Exception as e:
        raise ValueError(f"Certificat CA invalide : {e}")

def read_csv_input(filepath: str) -> List[str]:
    """Lit le fichier CSV d'entrée et retourne la liste des noms de VMs."""
    vm_names = []
    try:
        with open(filepath, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if 'vm_name' not in reader.fieldnames:
                raise ValueError("La colonne 'vm_name' est manquante dans le fichier CSV.")
            for row in reader:
                vm_names.append(row['vm_name'].strip())
    except Exception as e:
        logger.error(f"Erreur lors de la lecture du CSV : {e}")
        raise
    return vm_names

def process_vm(vm_name: str, action: str, client: VSphereClient, dry_run: bool) -> Dict:
    """Traite une VM individuelle et retourne le résultat pour le rapport."""
    start_time = datetime.now()
    result = {
        "vm_name": vm_name,
        "action": action,
        "status": "FAILED",
        "message": "Initialization error",
        "timestamp": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": "0.0s"
    }
    
    try:
        vm_id = client.get_vm_id(vm_name)
        if not vm_id:
            result["status"] = "FAILED"
            result["message"] = "VM not found in vCenter"
        else:
            status, message = client.perform_action(vm_id, action, dry_run)
            result["status"] = status
            result["message"] = message
    except Exception as e:
        result["message"] = f"Exception: {str(e)}"
    
    end_time = datetime.now()
    result["duration"] = f"{(end_time - start_time).total_seconds():.1f}s"
    return result

def generate_reports(results: List[Dict], output_prefix: str):
    """Génère les rapports CSV et JSON horodatés."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Rapport CSV
    csv_file = f"{output_prefix}_{timestamp}.csv"
    try:
        with open(csv_file, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=["vm_name", "action", "status", "message", "timestamp", "duration"])
            writer.writeheader()
            writer.writerows(results)
        logger.info(f"Rapport CSV généré : {csv_file}")
    except IOError as e:
        logger.error(f"Impossible d'écrire le rapport CSV : {e}")

    # Rapport JSON
    json_file = f"{output_prefix}_{timestamp}.json"
    try:
        with open(json_file, mode='w', encoding='utf-8') as f:
            json.dump(results, f, indent=4)
        logger.info(f"Rapport JSON généré : {json_file}")
    except IOError as e:
        logger.error(f"Impossible d'écrire le rapport JSON : {e}")

def main():
    parser = argparse.ArgumentParser(description="vSphere VM Power Management Tool")
    parser.add_argument('--vcenter', required=True, help="URL ou IP du vCenter")
    parser.add_argument('--user', required=True, help="Nom d'utilisateur vCenter")
    parser.add_argument('--password', required=True, help="Mot de passe vCenter (ou via env var VSPHERE_PASSWORD)")
    parser.add_argument('--ca-cert', required=True, help="Chemin vers le certificat CA (.pem)")
    parser.add_argument('--action', required=True, choices=VSphereClient.ACTIONS, help="Action à appliquer")
    parser.add_argument('--input', required=True, help="Fichier CSV d'entrée")
    parser.add_argument('--dry-run', action='store_true', help="Simuler l'action sans l'exécuter")
    parser.add_argument('--workers', type=int, default=5, help="Nombre de threads parallèles (défaut: 5)")
    
    args = parser.parse_args()

    # Support variable d'environnement pour le mot de passe (.env style)
    password = os.environ.get("VSPHERE_PASSWORD", args.password)

    # Validation stricte du SSL
    try:
        validate_ssl_cert(args.ca_cert)
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    # Lecture des VMs
    try:
        vm_names = read_csv_input(args.input)
        if not vm_names:
            logger.warning("Aucune VM trouvée dans le fichier CSV. Fin du script.")
            sys.exit(0)
    except Exception:
        sys.exit(1)

    # Exécution principale
    results = []
    try:
        client = VSphereClient(args.vcenter, args.user, password, args.ca_cert)
        client.authenticate()

        logger.info(f"Début du traitement de {len(vm_names)} VMs avec l'action '{args.action}' (Dry-run: {args.dry_run})")
        
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(process_vm, vm, args.action, client, args.dry_run): vm 
                for vm in vm_names
            }
            
            for i, future in enumerate(as_completed(futures), 1):
                vm = futures[future]
                try:
                    res = future.result()
                    results.append(res)
                    # Barre de progression basique (sans lib externe)
                    logger.info(f"[{i}/{len(vm_names)}] {res['vm_name']} - {res['status']} - {res['message']}")
                except Exception as e:
                    logger.error(f"Erreur critique pour la VM {vm}: {e}")

    except Exception as e:
        logger.critical(f"Erreur fatale pendant l'exécution : {e}")
        sys.exit(1)
    
    # Génération des rapports
    generate_reports(results, "vm_action_report")

if __name__ == "__main__":
    main()
