#!/usr/bin/env python3
"""
Script de décommissionnement automatisé de VMs via PSSIT
Auteur: Script automatisé
Version: 1.0.0
Description: Décommissionne des VMs à partir d'un fichier CSV en appelant les webservices PSSIT
"""

import csv
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import argparse
import json
from enum import Enum


class DecommissionStatus(Enum):
    """Statuts possibles du décommissionnement"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class VMDecommissionRequest:
    """Représente une demande de décommissionnement"""
    vm_name: str
    subscription_id: str
    vcenter: Optional[str] = None
    environment: Optional[str] = None
    row_number: int = 0
    status: DecommissionStatus = DecommissionStatus.PENDING
    error_message: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


@dataclass
class DecommissionResult:
    """Résultat d'un décommissionnement"""
    vm_name: str
    subscription_id: str
    status: DecommissionStatus
    duration: float
    error_message: Optional[str] = None
    response_data: Optional[Dict] = None


class PSSITClient:
    """Client pour interagir avec les APIs PSSIT"""
    
    def __init__(self, base_url: str, username: str, password: str, 
                 timeout: int = 300, verify_ssl: bool = True):
        """
        Initialise le client PSSIT
        
        Args:
            base_url: URL de base de l'API PSSIT
            username: Nom d'utilisateur
            password: Mot de passe
            timeout: Timeout pour les requêtes (secondes)
            verify_ssl: Vérifier les certificats SSL
        """
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.session = self._create_session()
        self.token = None
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def _create_session(self) -> requests.Session:
        """Crée une session avec retry automatique"""
        session = requests.Session()
        
        # Configuration du retry
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def authenticate(self) -> bool:
        """
        Authentification auprès de PSSIT
        
        Returns:
            True si authentification réussie
        """
        try:
            self.logger.info("Authentification en cours...")
            
            auth_url = f"{self.base_url}/api/auth/login"
            payload = {
                "username": self.username,
                "password": self.password
            }
            
            response = self.session.post(
                auth_url,
                json=payload,
                timeout=30,
                verify=self.verify_ssl
            )
            response.raise_for_status()
            
            data = response.json()
            self.token = data.get('token') or data.get('access_token')
            
            if self.token:
                self.session.headers.update({
                    'Authorization': f'Bearer {self.token}',
                    'Content-Type': 'application/json'
                })
                self.logger.info("Authentification réussie")
                return True
            else:
                self.logger.error("Token non trouvé dans la réponse")
                return False
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Erreur d'authentification: {e}")
            return False
    
    def decommission_subscription(self, subscription_id: str, 
                                  vm_name: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Décommissionne un abonnement (annulation)
        
        Args:
            subscription_id: ID de l'abonnement
            vm_name: Nom de la VM (pour logging)
            
        Returns:
            Tuple (succès, message_erreur, données_réponse)
        """
        try:
            self.logger.info(f"Décommissionnement de {vm_name} (subscription: {subscription_id})...")
            
            # URL du webservice de décommissionnement
            # Adapter selon l'API réelle de PSSIT
            decom_url = f"{self.base_url}/api/subscriptions/{subscription_id}/cancel"
            
            payload = {
                "reason": "Automated decommissioning",
                "force": False,
                "delete_resources": True
            }
            
            response = self.session.post(
                decom_url,
                json=payload,
                timeout=self.timeout,
                verify=self.verify_ssl
            )
            
            response.raise_for_status()
            response_data = response.json()
            
            self.logger.info(f"Décommissionnement de {vm_name} réussi")
            return True, None, response_data
            
        except requests.exceptions.HTTPError as e:
            error_msg = f"Erreur HTTP {e.response.status_code}: {e.response.text}"
            self.logger.error(f"Échec du décommissionnement de {vm_name}: {error_msg}")
            return False, error_msg, None
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Erreur réseau: {str(e)}"
            self.logger.error(f"Échec du décommissionnement de {vm_name}: {error_msg}")
            return False, error_msg, None
    
    def check_subscription_status(self, subscription_id: str) -> Optional[Dict]:
        """
        Vérifie le statut d'un abonnement
        
        Args:
            subscription_id: ID de l'abonnement
            
        Returns:
            Dictionnaire avec les informations de l'abonnement ou None
        """
        try:
            status_url = f"{self.base_url}/api/subscriptions/{subscription_id}/status"
            
            response = self.session.get(
                status_url,
                timeout=30,
                verify=self.verify_ssl
            )
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Erreur lors de la vérification du statut: {e}")
            return None
    
    def close(self):
        """Ferme proprement la session"""
        if self.session:
            self.session.close()


class VMDecommissioner:
    """Orchestrateur du décommissionnement de VMs"""
    
    def __init__(self, pssit_client: PSSITClient, 
                 max_workers: int = 5,
                 dry_run: bool = False):
        """
        Initialise le décommissioner
        
        Args:
            pssit_client: Client PSSIT configuré
            max_workers: Nombre de threads parallèles
            dry_run: Mode simulation (pas d'actions réelles)
        """
        self.client = pssit_client
        self.max_workers = max_workers
        self.dry_run = dry_run
        self.logger = logging.getLogger(self.__class__.__name__)
        self.results: List[DecommissionResult] = []
    
    def load_csv(self, csv_path: Path) -> List[VMDecommissionRequest]:
        """
        Charge les VMs depuis un fichier CSV
        
        Format CSV attendu:
        vm_name,subscription_id,vcenter,environment
        
        Args:
            csv_path: Chemin vers le fichier CSV
            
        Returns:
            Liste des demandes de décommissionnement
        """
        requests_list = []
        
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as csvfile:
                reader = csv.DictReader(csvfile)
                
                # Validation des colonnes requises
                required_columns = {'vm_name', 'subscription_id'}
                if not required_columns.issubset(reader.fieldnames or []):
                    raise ValueError(
                        f"Colonnes requises manquantes. Attendu: {required_columns}, "
                        f"Trouvé: {reader.fieldnames}"
                    )
                
                for idx, row in enumerate(reader, start=2):  # start=2 car ligne 1 = header
                    vm_name = row['vm_name'].strip()
                    subscription_id = row['subscription_id'].strip()
                    
                    if not vm_name or not subscription_id:
                        self.logger.warning(
                            f"Ligne {idx}: vm_name ou subscription_id vide, ligne ignorée"
                        )
                        continue
                    
                    request = VMDecommissionRequest(
                        vm_name=vm_name,
                        subscription_id=subscription_id,
                        vcenter=row.get('vcenter', '').strip() or None,
                        environment=row.get('environment', '').strip() or None,
                        row_number=idx
                    )
                    
                    requests_list.append(request)
            
            self.logger.info(f"Chargement de {len(requests_list)} VMs depuis {csv_path}")
            return requests_list
            
        except FileNotFoundError:
            self.logger.error(f"Fichier CSV non trouvé: {csv_path}")
            raise
        except Exception as e:
            self.logger.error(f"Erreur lors du chargement du CSV: {e}")
            raise
    
    def decommission_vm(self, request: VMDecommissionRequest) -> DecommissionResult:
        """
        Décommissionne une VM
        
        Args:
            request: Demande de décommissionnement
            
        Returns:
            Résultat du décommissionnement
        """
        start_time = time.time()
        request.start_time = datetime.now()
        
        try:
            if self.dry_run:
                self.logger.info(
                    f"[DRY RUN] Simulation du décommissionnement de {request.vm_name}"
                )
                time.sleep(0.5)  # Simule un délai
                success = True
                error_msg = None
                response_data = {"dry_run": True, "status": "simulated"}
            else:
                success, error_msg, response_data = self.client.decommission_subscription(
                    request.subscription_id,
                    request.vm_name
                )
            
            duration = time.time() - start_time
            request.end_time = datetime.now()
            
            status = DecommissionStatus.SUCCESS if success else DecommissionStatus.FAILED
            request.status = status
            request.error_message = error_msg
            
            result = DecommissionResult(
                vm_name=request.vm_name,
                subscription_id=request.subscription_id,
                status=status,
                duration=duration,
                error_message=error_msg,
                response_data=response_data
            )
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            error_msg = f"Exception inattendue: {str(e)}"
            self.logger.error(f"Erreur lors du décommissionnement de {request.vm_name}: {e}")
            
            request.status = DecommissionStatus.FAILED
            request.error_message = error_msg
            request.end_time = datetime.now()
            
            return DecommissionResult(
                vm_name=request.vm_name,
                subscription_id=request.subscription_id,
                status=DecommissionStatus.FAILED,
                duration=duration,
                error_message=error_msg
            )
    
    def decommission_batch(self, requests: List[VMDecommissionRequest]) -> List[DecommissionResult]:
        """
        Décommissionne un lot de VMs en parallèle
        
        Args:
            requests: Liste des demandes
            
        Returns:
            Liste des résultats
        """
        results = []
        
        self.logger.info(
            f"Démarrage du décommissionnement de {len(requests)} VMs "
            f"avec {self.max_workers} workers"
        )
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_request = {
                executor.submit(self.decommission_vm, req): req 
                for req in requests
            }
            
            for future in as_completed(future_to_request):
                try:
                    result = future.result()
                    results.append(result)
                    
                    # Log de progression
                    completed = len(results)
                    total = len(requests)
                    success_count = sum(1 for r in results if r.status == DecommissionStatus.SUCCESS)
                    
                    self.logger.info(
                        f"Progression: {completed}/{total} "
                        f"(Succès: {success_count}, Échecs: {completed - success_count})"
                    )
                    
                except Exception as e:
                    request = future_to_request[future]
                    self.logger.error(
                        f"Exception lors du traitement de {request.vm_name}: {e}"
                    )
        
        self.results = results
        return results
    
    def generate_report(self, results: List[DecommissionResult], 
                       output_path: Optional[Path] = None) -> str:
        """
        Génère un rapport de décommissionnement
        
        Args:
            results: Liste des résultats
            output_path: Chemin du fichier de rapport (optionnel)
            
        Returns:
            Contenu du rapport
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        success_count = sum(1 for r in results if r.status == DecommissionStatus.SUCCESS)
        failed_count = sum(1 for r in results if r.status == DecommissionStatus.FAILED)
        total_duration = sum(r.duration for r in results)
        
        report_lines = [
            "=" * 80,
            "RAPPORT DE DÉCOMMISSIONNEMENT DE VMs",
            "=" * 80,
            f"Date: {timestamp}",
            f"Mode: {'DRY RUN (Simulation)' if self.dry_run else 'PRODUCTION'}",
            f"Total VMs: {len(results)}",
            f"Succès: {success_count}",
            f"Échecs: {failed_count}",
            f"Durée totale: {total_duration:.2f}s",
            f"Durée moyenne: {total_duration/len(results):.2f}s" if results else "N/A",
            "",
            "DÉTAILS:",
            "-" * 80,
        ]
        
        # Détails par VM
        for result in results:
            status_symbol = "✓" if result.status == DecommissionStatus.SUCCESS else "✗"
            report_lines.append(
                f"{status_symbol} {result.vm_name} | "
                f"Subscription: {result.subscription_id} | "
                f"Durée: {result.duration:.2f}s | "
                f"Statut: {result.status.value}"
            )
            if result.error_message:
                report_lines.append(f"  Erreur: {result.error_message}")
            report_lines.append("")
        
        # Échecs détaillés
        if failed_count > 0:
            report_lines.extend([
                "=" * 80,
                "ÉCHECS DÉTAILLÉS:",
                "-" * 80,
            ])
            
            for result in results:
                if result.status == DecommissionStatus.FAILED:
                    report_lines.extend([
                        f"VM: {result.vm_name}",
                        f"Subscription: {result.subscription_id}",
                        f"Erreur: {result.error_message}",
                        ""
                    ])
        
        report_lines.append("=" * 80)
        report = "\n".join(report_lines)
        
        # Sauvegarde du rapport
        if output_path:
            try:
                output_path.write_text(report, encoding='utf-8')
                self.logger.info(f"Rapport sauvegardé: {output_path}")
            except Exception as e:
                self.logger.error(f"Erreur lors de la sauvegarde du rapport: {e}")
        
        return report
    
    def export_results_csv(self, results: List[DecommissionResult], 
                          output_path: Path):
        """
        Exporte les résultats en CSV
        
        Args:
            results: Liste des résultats
            output_path: Chemin du fichier CSV de sortie
        """
        try:
            with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'vm_name', 'subscription_id', 'status', 
                    'duration', 'error_message'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for result in results:
                    writer.writerow({
                        'vm_name': result.vm_name,
                        'subscription_id': result.subscription_id,
                        'status': result.status.value,
                        'duration': f"{result.duration:.2f}",
                        'error_message': result.error_message or ''
                    })
            
            self.logger.info(f"Résultats exportés: {output_path}")
            
        except Exception as e:
            self.logger.error(f"Erreur lors de l'export CSV: {e}")


def setup_logging(log_file: Optional[Path] = None, 
                  verbose: bool = False) -> logging.Logger:
    """
    Configure le système de logging
    
    Args:
        log_file: Chemin du fichier de log (optionnel)
        verbose: Mode verbose (DEBUG)
        
    Returns:
        Logger configuré
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    
    # Format des logs
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # Configuration du logger racine
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    # Handler console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(console_handler)
    
    # Handler fichier
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        logger.addHandler(file_handler)
    
    return logger


def main():
    """Point d'entrée principal du script"""
    
    parser = argparse.ArgumentParser(
        description='Décommissionnement automatisé de VMs via PSSIT',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples d'utilisation:
  # Mode normal
  python decommission_vms.py -i vms.csv -u admin -p password -s https://pssit.example.com
  
  # Mode dry-run (simulation)
  python decommission_vms.py -i vms.csv -u admin -p password -s https://pssit.example.com --dry-run
  
  # Avec plus de workers et logs verbeux
  python decommission_vms.py -i vms.csv -u admin -p password -s https://pssit.example.com -w 10 -v
        """
    )
    
    parser.add_argument('-i', '--input', required=True, type=Path,
                       help='Fichier CSV contenant les VMs à décommissioner')
    parser.add_argument('-u', '--username', required=True,
                       help='Nom d\'utilisateur PSSIT')
    parser.add_argument('-p', '--password', required=True,
                       help='Mot de passe PSSIT')
    parser.add_argument('-s', '--server', required=True,
                       help='URL du serveur PSSIT (ex: https://pssit.example.com)')
    parser.add_argument('-w', '--workers', type=int, default=5,
                       help='Nombre de workers parallèles (défaut: 5)')
    parser.add_argument('-t', '--timeout', type=int, default=300,
                       help='Timeout en secondes pour les requêtes (défaut: 300)')
    parser.add_argument('--no-ssl-verify', action='store_true',
                       help='Désactiver la vérification SSL')
    parser.add_argument('--dry-run', action='store_true',
                       help='Mode simulation (aucune action réelle)')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Mode verbose (logs DEBUG)')
    parser.add_argument('-l', '--log-file', type=Path,
                       help='Fichier de log (optionnel)')
    parser.add_argument('-o', '--output-dir', type=Path, default=Path('.'),
                       help='Répertoire de sortie pour les rapports (défaut: répertoire courant)')
    
    args = parser.parse_args()
    
    # Configuration du logging
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not args.log_file:
        args.log_file = args.output_dir / f"decommission_{timestamp}.log"
    
    logger = setup_logging(args.log_file, args.verbose)
    logger.info("=" * 80)
    logger.info("DÉMARRAGE DU SCRIPT DE DÉCOMMISSIONNEMENT")
    logger.info("=" * 80)
    
    try:
        # Validation du fichier d'entrée
        if not args.input.exists():
            logger.error(f"Fichier CSV introuvable: {args.input}")
            sys.exit(1)
        
        # Création du répertoire de sortie
        args.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialisation du client PSSIT
        logger.info(f"Connexion au serveur PSSIT: {args.server}")
        client = PSSITClient(
            base_url=args.server,
            username=args.username,
            password=args.password,
            timeout=args.timeout,
            verify_ssl=not args.no_ssl_verify
        )
        
        # Authentification
        if not client.authenticate():
            logger.error("Échec de l'authentification")
            sys.exit(1)
        
        # Initialisation du décommissioner
        decommissioner = VMDecommissioner(
            pssit_client=client,
            max_workers=args.workers,
            dry_run=args.dry_run
        )
        
        # Chargement des VMs
        logger.info(f"Chargement du fichier CSV: {args.input}")
        requests = decommissioner.load_csv(args.input)
        
        if not requests:
            logger.warning("Aucune VM à décommissioner")
            sys.exit(0)
        
        # Confirmation utilisateur
        if not args.dry_run:
            logger.warning(f"ATTENTION: Vous allez décommissioner {len(requests)} VMs!")
            response = input("Voulez-vous continuer? (oui/non): ")
            if response.lower() not in ['oui', 'yes', 'y', 'o']:
                logger.info("Opération annulée par l'utilisateur")
                sys.exit(0)
        
        # Décommissionnement
        results = decommissioner.decommission_batch(requests)
        
        # Génération des rapports
        report_file = args.output_dir / f"report_{timestamp}.txt"
        report = decommissioner.generate_report(results, report_file)
        print("\n" + report)
        
        # Export CSV des résultats
        csv_file = args.output_dir / f"results_{timestamp}.csv"
        decommissioner.export_results_csv(results, csv_file)
        
        # Statistiques finales
        success_count = sum(1 for r in results if r.status == DecommissionStatus.SUCCESS)
        failed_count = len(results) - success_count
        
        logger.info("=" * 80)
        logger.info("DÉCOMMISSIONNEMENT TERMINÉ")
        logger.info(f"Succès: {success_count}/{len(results)}")
        logger.info(f"Échecs: {failed_count}/{len(results)}")
        logger.info(f"Rapport: {report_file}")
        logger.info(f"Résultats CSV: {csv_file}")
        logger.info("=" * 80)
        
        # Code de sortie
        sys.exit(0 if failed_count == 0 else 1)
        
    except KeyboardInterrupt:
        logger.warning("Interruption par l'utilisateur (Ctrl+C)")
        sys.exit(130)
        
    except Exception as e:
        logger.exception(f"Erreur fatale: {e}")
        sys.exit(1)
        
    finally:
        if 'client' in locals():
            client.close()


if __name__ == "__main__":
    main()
