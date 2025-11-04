#!/usr/bin/env python3
"""
Script de monitoring des ressources CPU et mémoire des VMs vCenter 8+
Utilise l'API REST vSphere et l'API Performance pour les métriques en temps réel.

Auteur: Expert Python
Version: 2.0.0
Compatible: vCenter 8.0+
"""

import requests
import json
import logging
import sys
import argparse
import warnings
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib3.exceptions import InsecureRequestWarning
from enum import Enum

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('vcenter_monitor.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Suppression des warnings SSL si nécessaire
warnings.simplefilter('ignore', InsecureRequestWarning)


class VMIssueType(Enum):
    """Types de problèmes détectables sur une VM"""
    CPU_HIGH = "CPU_HIGH"
    MEMORY_HIGH = "MEMORY_HIGH"
    POWERED_OFF = "POWERED_OFF"
    SUSPENDED = "SUSPENDED"
    BOOT_FAILURE = "BOOT_FAILURE"
    TOOLS_NOT_RUNNING = "TOOLS_NOT_RUNNING"
    QUESTION_PENDING = "QUESTION_PENDING"


@dataclass
class VMResourceStatus:
    """Classe pour stocker le statut des ressources d'une VM"""
    vm_name: str
    vm_id: str
    cpu_usage_mhz: float
    cpu_limit_mhz: float
    cpu_usage_percent: float
    memory_usage_mb: float
    memory_limit_mb: float
    memory_usage_percent: float
    memory_active_mb: float
    memory_consumed_mb: float
    power_state: str
    connection_state: str
    tools_running_status: str
    overall_status: str
    boot_time: Optional[str]
    uptime_seconds: Optional[int]
    issues: List[VMIssueType]
    
    @property
    def has_issues(self) -> bool:
        """Retourne True si la VM a des problèmes"""
        return len(self.issues) > 0
    
    def __str__(self) -> str:
        issues_str = ", ".join([issue.value for issue in self.issues]) if self.issues else "Aucun"
        uptime_str = self._format_uptime() if self.uptime_seconds else "N/A"
        
        return (
            f"VM: {self.vm_name} (ID: {self.vm_id})\n"
            f"  État alimentation: {self.power_state}\n"
            f"  État connexion: {self.connection_state}\n"
            f"  État global: {self.overall_status}\n"
            f"  VMware Tools: {self.tools_running_status}\n"
            f"  Temps de démarrage: {self.boot_time or 'N/A'}\n"
            f"  Uptime: {uptime_str}\n"
            f"  CPU: {self.cpu_usage_percent:.2f}% "
            f"({self.cpu_usage_mhz:.0f}/{self.cpu_limit_mhz:.0f} MHz)\n"
            f"  Mémoire: {self.memory_usage_percent:.2f}% "
            f"({self.memory_usage_mb:.0f}/{self.memory_limit_mb:.0f} MB)\n"
            f"  Mémoire active: {self.memory_active_mb:.0f} MB\n"
            f"  Mémoire consommée: {self.memory_consumed_mb:.0f} MB\n"
            f"  🚨 Problèmes détectés: {issues_str}"
        )
    
    def _format_uptime(self) -> str:
        """Formate l'uptime en format lisible"""
        if not self.uptime_seconds:
            return "N/A"
        
        days = self.uptime_seconds // 86400
        hours = (self.uptime_seconds % 86400) // 3600
        minutes = (self.uptime_seconds % 3600) // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days}j")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        
        return " ".join(parts) if parts else "< 1m"


class VCenterAPIClient:
    """Client pour interagir avec l'API REST vCenter 8+"""
    
    def __init__(self, vcenter_host: str, username: str, password: str, 
                 verify_ssl: bool = False):
        """
        Initialise le client vCenter API
        
        Args:
            vcenter_host: Hostname ou IP du vCenter
            username: Nom d'utilisateur
            password: Mot de passe
            verify_ssl: Vérifier les certificats SSL (False pour self-signed)
        """
        self.vcenter_host = vcenter_host
        self.base_url = f"https://{vcenter_host}/api"
        self.rest_url = f"https://{vcenter_host}/rest"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.session_id: Optional[str] = None
        self.session = requests.Session()
        self.session.verify = verify_ssl
        
        logger.info(f"Initialisation du client vCenter: {vcenter_host}")
    
    def authenticate(self) -> bool:
        """
        Authentifie l'utilisateur et obtient un token de session
        
        Returns:
            bool: True si l'authentification réussit, False sinon
        """
        auth_url = f"{self.base_url}/session"
        
        try:
            logger.info("Tentative d'authentification...")
            response = self.session.post(
                auth_url,
                auth=(self.username, self.password),
                timeout=30
            )
            response.raise_for_status()
            
            self.session_id = response.json()
            self.session.headers.update({
                'vmware-api-session-id': self.session_id,
                'Content-Type': 'application/json'
            })
            
            logger.info("Authentification réussie")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Échec de l'authentification: {e}")
            return False
    
    def disconnect(self) -> None:
        """Ferme la session vCenter"""
        if self.session_id:
            try:
                delete_url = f"{self.base_url}/session"
                self.session.delete(delete_url, timeout=10)
                logger.info("Déconnexion réussie")
            except Exception as e:
                logger.warning(f"Erreur lors de la déconnexion: {e}")
    
    def get_all_vms(self) -> List[Dict]:
        """
        Récupère la liste de toutes les VMs
        
        Returns:
            List[Dict]: Liste des VMs avec leurs informations de base
        """
        try:
            url = f"{self.base_url}/vcenter/vm"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            vms = response.json()
            logger.info(f"Nombre de VMs récupérées: {len(vms)}")
            return vms
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors de la récupération des VMs: {e}")
            return []
    
    def get_vm_details(self, vm_id: str) -> Optional[Dict]:
        """
        Récupère les détails complets d'une VM
        
        Args:
            vm_id: Identifiant de la VM
            
        Returns:
            Dict: Détails de la VM ou None en cas d'erreur
        """
        try:
            url = f"{self.base_url}/vcenter/vm/{vm_id}"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors de la récupération des détails VM {vm_id}: {e}")
            return None
    
    def get_vm_stats(self, vm_id: str, interval: str = "MINUTES5") -> Optional[Dict]:
        """
        Récupère les statistiques de performance d'une VM via l'API Stats
        
        Args:
            vm_id: Identifiant de la VM
            interval: Intervalle de collecte (REALTIME, MINUTES5, HOURS2, DAYS)
            
        Returns:
            Dict: Statistiques de performance ou None en cas d'erreur
        """
        try:
            # Utilisation de l'API vcenter/vm/{vm}/guest/stats (vCenter 8.0+)
            url = f"{self.base_url}/vcenter/vm/{vm_id}/guest/stats"
            params = {"interval": interval}
            
            response = self.session.get(url, params=params, timeout=30)
            
            # Si l'endpoint n'existe pas, on essaie une autre approche
            if response.status_code == 404:
                logger.debug(f"Endpoint stats non disponible pour VM {vm_id}, utilisation méthode alternative")
                return self._get_vm_stats_alternative(vm_id)
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.debug(f"Erreur stats VM {vm_id}: {e}, tentative méthode alternative")
            return self._get_vm_stats_alternative(vm_id)
    
    def _get_vm_stats_alternative(self, vm_id: str) -> Optional[Dict]:
        """
        Méthode alternative pour récupérer les stats via l'API Appliance Monitoring
        
        Args:
            vm_id: Identifiant de la VM
            
        Returns:
            Dict: Statistiques de performance ou None
        """
        try:
            # Récupération via l'API monitoring/query
            url = f"{self.base_url}/appliance/monitoring/query"
            
            # Construction de la requête pour CPU et mémoire
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(minutes=5)
            
            payload = {
                "item": {
                    "type": "VM",
                    "id": vm_id
                },
                "metrics": [
                    "cpu.usage.average",
                    "mem.usage.average",
                    "mem.active.average",
                    "mem.consumed.average"
                ],
                "start_time": start_time.isoformat() + "Z",
                "end_time": end_time.isoformat() + "Z",
                "interval": "PT5M"
            }
            
            response = self.session.post(url, json=payload, timeout=30)
            
            if response.status_code == 404:
                # API non disponible, retour valeurs nulles
                return None
            
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.debug(f"Méthode alternative stats échouée pour VM {vm_id}: {e}")
            return None
    
    def get_vm_hardware_info(self, vm_id: str) -> Optional[Dict]:
        """
        Récupère les informations hardware d'une VM (CPU, mémoire)
        
        Args:
            vm_id: Identifiant de la VM
            
        Returns:
            Dict: Informations hardware ou None en cas d'erreur
        """
        try:
            cpu_url = f"{self.base_url}/vcenter/vm/{vm_id}/hardware/cpu"
            memory_url = f"{self.base_url}/vcenter/vm/{vm_id}/hardware/memory"
            
            cpu_response = self.session.get(cpu_url, timeout=30)
            memory_response = self.session.get(memory_url, timeout=30)
            
            cpu_response.raise_for_status()
            memory_response.raise_for_status()
            
            return {
                'cpu': cpu_response.json(),
                'memory': memory_response.json()
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur récupération hardware VM {vm_id}: {e}")
            return None


class VMResourceMonitor:
    """Moniteur de ressources pour les VMs vCenter avec métriques temps réel"""
    
    def __init__(self, api_client: VCenterAPIClient, 
                 cpu_threshold: float = 80.0,
                 memory_threshold: float = 90.0,
                 check_boot_issues: bool = True,
                 check_tools: bool = True):
        """
        Initialise le moniteur de ressources
        
        Args:
            api_client: Client API vCenter
            cpu_threshold: Seuil d'alerte CPU en pourcentage (défaut: 80%)
            memory_threshold: Seuil d'alerte mémoire en pourcentage (défaut: 90%)
            check_boot_issues: Vérifier les problèmes de boot (défaut: True)
            check_tools: Vérifier l'état des VMware Tools (défaut: True)
        """
        self.api_client = api_client
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.check_boot_issues = check_boot_issues
        self.check_tools = check_tools
        
        logger.info(f"Seuils configurés - CPU: {cpu_threshold}%, Mémoire: {memory_threshold}%")
        logger.info(f"Vérification boot: {check_boot_issues}, Vérification Tools: {check_tools}")
    
    def analyze_vm_resources(self, vm_id: str, vm_name: str) -> Optional[VMResourceStatus]:
        """
        Analyse complète des ressources et de l'état d'une VM
        
        Args:
            vm_id: Identifiant de la VM
            vm_name: Nom de la VM
            
        Returns:
            VMResourceStatus: Statut complet de la VM ou None en cas d'erreur
        """
        # Récupération des détails de la VM
        vm_details = self.api_client.get_vm_details(vm_id)
        if not vm_details:
            logger.warning(f"Impossible de récupérer les détails pour VM {vm_name}")
            return None
        
        # Extraction des informations d'état
        power_state = vm_details.get('power_state', 'UNKNOWN')
        connection_state = vm_details.get('connection_state', 'UNKNOWN')
        
        # État global de la VM
        # Note: L'API REST ne fournit pas directement overall_status
        # On le déduit de connection_state
        overall_status_map = {
            'CONNECTED': 'green',
            'DISCONNECTED': 'red',
            'ORPHANED': 'red',
            'INACCESSIBLE': 'red',
            'INVALID': 'red'
        }
        overall_status = overall_status_map.get(connection_state, 'gray')
        
        # VMware Tools status
        tools_info = vm_details.get('guest_OS', {})
        tools_running_status = tools_info.get('tools_running_status', 'UNKNOWN')
        
        # Boot time et uptime
        boot_time = vm_details.get('boot_time')
        uptime_seconds = None
        if boot_time and power_state == 'POWERED_ON':
            try:
                boot_dt = datetime.fromisoformat(boot_time.replace('Z', '+00:00'))
                uptime_seconds = int((datetime.now(boot_dt.tzinfo) - boot_dt).total_seconds())
            except Exception as e:
                logger.debug(f"Erreur calcul uptime pour VM {vm_name}: {e}")
        
        # Récupération des informations hardware
        hardware_info = self.api_client.get_vm_hardware_info(vm_id)
        if not hardware_info:
            logger.warning(f"Impossible de récupérer les infos hardware pour VM {vm_name}")
            return None
        
        # Calcul des limites de ressources
        cpu_count = hardware_info['cpu'].get('count', 1)
        cpu_limit_mhz = cpu_count * 2000  # Estimation: 2 GHz par vCPU
        memory_limit_mb = hardware_info['memory'].get('size_MiB', 0)
        
        # Initialisation des métriques
        cpu_usage_mhz = 0.0
        cpu_usage_percent = 0.0
        memory_usage_mb = 0.0
        memory_usage_percent = 0.0
        memory_active_mb = 0.0
        memory_consumed_mb = 0.0
        
        # Récupération des statistiques de performance pour les VMs allumées
        if power_state == 'POWERED_ON':
            stats = self.api_client.get_vm_stats(vm_id)
            if stats:
                # Traitement des stats selon le format retourné
                # Format peut varier selon l'API utilisée
                cpu_usage_percent = self._extract_metric(stats, 'cpu.usage.average', 0.0)
                memory_usage_percent = self._extract_metric(stats, 'mem.usage.average', 0.0)
                memory_active_mb = self._extract_metric(stats, 'mem.active.average', 0.0)
                memory_consumed_mb = self._extract_metric(stats, 'mem.consumed.average', 0.0)
                
                cpu_usage_mhz = (cpu_usage_percent / 100.0) * cpu_limit_mhz
                memory_usage_mb = (memory_usage_percent / 100.0) * memory_limit_mb
            else:
                logger.debug(f"Pas de stats disponibles pour VM {vm_name}, utilisation valeurs par défaut")
        
        # Détection des problèmes
        issues = self._detect_issues(
            power_state=power_state,
            connection_state=connection_state,
            tools_running_status=tools_running_status,
            cpu_usage_percent=cpu_usage_percent,
            memory_usage_percent=memory_usage_percent,
            uptime_seconds=uptime_seconds,
            overall_status=overall_status
        )
        
        return VMResourceStatus(
            vm_name=vm_name,
            vm_id=vm_id,
            cpu_usage_mhz=cpu_usage_mhz,
            cpu_limit_mhz=cpu_limit_mhz,
            cpu_usage_percent=cpu_usage_percent,
            memory_usage_mb=memory_usage_mb,
            memory_limit_mb=memory_limit_mb,
            memory_usage_percent=memory_usage_percent,
            memory_active_mb=memory_active_mb,
            memory_consumed_mb=memory_consumed_mb,
            power_state=power_state,
            connection_state=connection_state,
            tools_running_status=tools_running_status,
            overall_status=overall_status,
            boot_time=boot_time,
            uptime_seconds=uptime_seconds,
            issues=issues
        )
    
    def _extract_metric(self, stats: Dict, metric_name: str, default: float) -> float:
        """
        Extrait une métrique des statistiques retournées
        
        Args:
            stats: Dictionnaire de statistiques
            metric_name: Nom de la métrique à extraire
            default: Valeur par défaut si métrique non trouvée
            
        Returns:
            float: Valeur de la métrique
        """
        try:
            # Format 1: stats directement en dict avec clés métriques
            if metric_name in stats:
                value = stats[metric_name]
                if isinstance(value, (int, float)):
                    return float(value)
                elif isinstance(value, list) and len(value) > 0:
                    # Prendre la dernière valeur
                    return float(value[-1])
            
            # Format 2: stats avec structure data/metrics
            if 'data' in stats:
                for item in stats['data']:
                    if item.get('name') == metric_name:
                        values = item.get('values', [])
                        if values:
                            return float(values[-1])
            
            # Format 3: stats avec structure metrics array
            if 'metrics' in stats:
                for metric in stats['metrics']:
                    if metric.get('name') == metric_name:
                        values = metric.get('values', [])
                        if values:
                            return float(values[-1])
            
            return default
            
        except Exception as e:
            logger.debug(f"Erreur extraction métrique {metric_name}: {e}")
            return default
    
    def _detect_issues(self, power_state: str, connection_state: str,
                      tools_running_status: str, cpu_usage_percent: float,
                      memory_usage_percent: float, uptime_seconds: Optional[int],
                      overall_status: str) -> List[VMIssueType]:
        """
        Détecte les problèmes sur une VM
        
        Returns:
            List[VMIssueType]: Liste des problèmes détectés
        """
        issues = []
        
        # Problèmes d'alimentation
        if power_state == 'POWERED_OFF':
            issues.append(VMIssueType.POWERED_OFF)
        elif power_state == 'SUSPENDED':
            issues.append(VMIssueType.SUSPENDED)
        
        # Problèmes de connexion/état
        if connection_state in ['DISCONNECTED', 'ORPHANED', 'INACCESSIBLE', 'INVALID']:
            issues.append(VMIssueType.BOOT_FAILURE)
        
        # Problème de boot récent (moins de 5 minutes d'uptime)
        if self.check_boot_issues and power_state == 'POWERED_ON':
            if uptime_seconds is not None and uptime_seconds < 300:
                # VM redémarrée récemment, surveiller
                logger.debug(f"VM avec uptime court: {uptime_seconds}s")
        
        # Problèmes VMware Tools
        if self.check_tools and power_state == 'POWERED_ON':
            if tools_running_status in ['NOT_RUNNING', 'UNKNOWN']:
                issues.append(VMIssueType.TOOLS_NOT_RUNNING)
        
        # Problèmes de ressources (uniquement si VM allumée)
        if power_state == 'POWERED_ON':
            if cpu_usage_percent > self.cpu_threshold:
                issues.append(VMIssueType.CPU_HIGH)
            
            if memory_usage_percent > self.memory_threshold:
                issues.append(VMIssueType.MEMORY_HIGH)
        
        # État global dégradé
        if overall_status == 'red':
            # Déjà géré par les autres checks
            pass
        
        return issues
    
    def monitor_all_vms(self) -> Tuple[List[VMResourceStatus], List[VMResourceStatus]]:
        """
        Monitore toutes les VMs et détecte les problèmes
        
        Returns:
            Tuple contenant (toutes_les_vms, vms_avec_problèmes)
        """
        logger.info("Début du monitoring de toutes les VMs...")
        
        all_vms = self.api_client.get_all_vms()
        vm_statuses = []
        vms_with_issues = []
        
        for idx, vm in enumerate(all_vms, 1):
            vm_id = vm.get('vm')
            vm_name = vm.get('name', 'Unknown')
            
            logger.info(f"[{idx}/{len(all_vms)}] Analyse de la VM: {vm_name}")
            
            status = self.analyze_vm_resources(vm_id, vm_name)
            if status:
                vm_statuses.append(status)
                
                if status.has_issues:
                    vms_with_issues.append(status)
                    logger.warning(f"⚠️  Problèmes détectés sur VM {vm_name}: "
                                 f"{[issue.value for issue in status.issues]}")
        
        logger.info(f"Monitoring terminé. VMs analysées: {len(vm_statuses)}, "
                   f"VMs avec problèmes: {len(vms_with_issues)}")
        
        return vm_statuses, vms_with_issues
    
    def generate_report(self, vm_statuses: List[VMResourceStatus], 
                       vms_with_issues: List[VMResourceStatus]) -> str:
        """
        Génère un rapport de monitoring détaillé
        
        Args:
            vm_statuses: Liste de tous les statuts de VMs
            vms_with_issues: Liste des VMs avec problèmes
            
        Returns:
            str: Rapport formaté
        """
        report_lines = [
            "=" * 80,
            f"RAPPORT DE MONITORING VCENTER - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 80,
            "",
            f"Nombre total de VMs analysées: {len(vm_statuses)}",
            f"VMs avec problèmes détectés: {len(vms_with_issues)}",
            f"Seuil CPU: {self.cpu_threshold}%",
            f"Seuil Mémoire: {self.memory_threshold}%",
            "",
        ]
        
        if vms_with_issues:
            report_lines.append("🚨 ALERTE - VMs AVEC PROBLÈMES:")
            report_lines.append("=" * 80)
            
            # Grouper par type de problème
            issues_by_type = {}
            for vm_status in vms_with_issues:
                for issue in vm_status.issues:
                    if issue not in issues_by_type:
                        issues_by_type[issue] = []
                    issues_by_type[issue].append(vm_status)
            
            # Afficher par catégorie
            for issue_type, vms in sorted(issues_by_type.items(), 
                                         key=lambda x: x[0].value):
                report_lines.append(f"\n📋 {issue_type.value} ({len(vms)} VM(s)):")
                report_lines.append("-" * 80)
                for vm_status in vms:
                    report_lines.append(str(vm_status))
                    report_lines.append("-" * 80)
        else:
            report_lines.append("✅ Aucun problème détecté sur les VMs")
        
        report_lines.append("")
        report_lines.append("📊 STATISTIQUES GLOBALES:")
        report_lines.append("-" * 80)
        
        # Statistiques par état d'alimentation
        powered_on = [vm for vm in vm_statuses if vm.power_state == 'POWERED_ON']
        powered_off = [vm for vm in vm_statuses if vm.power_state == 'POWERED_OFF']
        suspended = [vm for vm in vm_statuses if vm.power_state == 'SUSPENDED']
        
        report_lines.append(f"État d'alimentation:")
        report_lines.append(f"  ✓ Allumées (POWERED_ON): {len(powered_on)}")
        report_lines.append(f"  ✗ Éteintes (POWERED_OFF): {len(powered_off)}")
        report_lines.append(f"  ⏸ Suspendues (SUSPENDED): {len(suspended)}")
        
        # Statistiques des VMs allumées
        if powered_on:
            avg_cpu = sum(vm.cpu_usage_percent for vm in powered_on) / len(powered_on)
            avg_mem = sum(vm.memory_usage_percent for vm in powered_on) / len(powered_on)
            max_cpu_vm = max(powered_on, key=lambda x: x.cpu_usage_percent)
            max_mem_vm = max(powered_on, key=lambda x: x.memory_usage_percent)
            
            report_lines.append(f"\nRessources moyennes (VMs allumées):")
            report_lines.append(f"  CPU moyen: {avg_cpu:.2f}%")
            report_lines.append(f"  Mémoire moyenne: {avg_mem:.2f}%")
            report_lines.append(f"  CPU max: {max_cpu_vm.cpu_usage_percent:.2f}% ({max_cpu_vm.vm_name})")
            report_lines.append(f"  Mémoire max: {max_mem_vm.memory_usage_percent:.2f}% ({max_mem_vm.vm_name})")
        
        # Statistiques VMware Tools
        tools_ok = sum(1 for vm in vm_statuses 
                      if vm.tools_running_status == 'RUNNING' and vm.power_state == 'POWERED_ON')
        tools_not_running = sum(1 for vm in vm_statuses 
                               if vm.tools_running_status in ['NOT_RUNNING', 'UNKNOWN'] 
                               and vm.power_state == 'POWERED_ON')
        
        report_lines.append(f"\nÉtat VMware Tools (VMs allumées):")
        report_lines.append(f"  ✓ En cours d'exécution: {tools_ok}")
        report_lines.append(f"  ✗ Non fonctionnels: {tools_not_running}")
        
        report_lines.append("")
        report_lines.append("=" * 80)
        
        return "\n".join(report_lines)


def main():
    """Fonction principale du script"""
    parser = argparse.ArgumentParser(
        description='Monitoring avancé des VMs vCenter 8+ avec API Performance',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples d'utilisation:
  %(prog)s --vcenter vcenter.example.com --username admin@vsphere.local --password 'Pass123!'
  %(prog)s --vcenter 192.168.1.10 --username admin@vsphere.local --password 'Pass123!' --cpu-threshold 85 --memory-threshold 95
  %(prog)s --vcenter vcenter.local --username admin@vsphere.local --password 'Pass123!' --output rapport.txt --json-output results.json
        """
    )
    
    # Arguments obligatoires
    parser.add_argument('--vcenter', required=True,
                       help='Hostname ou IP du vCenter')
    parser.add_argument('--username', required=True,
                       help='Nom d\'utilisateur vCenter (ex: administrator@vsphere.local)')
    parser.add_argument('--password', required=True,
                       help='Mot de passe vCenter')
    
    # Arguments optionnels - Seuils
    parser.add_argument('--cpu-threshold', type=float, default=80.0,
                       help='Seuil d\'alerte CPU en pourcentage (défaut: 80)')
    parser.add_argument('--memory-threshold', type=float, default=90.0,
                       help='Seuil d\'alerte mémoire en pourcentage (défaut: 90)')
    
    # Arguments optionnels - Options de vérification
    parser.add_argument('--no-check-boot', action='store_true',
                       help='Désactiver la vérification des problèmes de boot')
    parser.add_argument('--no-check-tools', action='store_true',
                       help='Désactiver la vérification des VMware Tools')
    
    # Arguments optionnels - SSL et sortie
    parser.add_argument('--verify-ssl', action='store_true',
                       help='Vérifier les certificats SSL (désactivé par défaut)')
    parser.add_argument('--output', type=str,
                       help='Fichier de sortie pour le rapport texte')
    parser.add_argument('--json-output', type=str,
                       help='Fichier de sortie pour le rapport JSON')
    
    # Arguments optionnels - Logging
    parser.add_argument('--verbose', action='store_true',
                       help='Mode verbeux (affiche plus de détails de debug)')
    parser.add_argument('--quiet', action='store_true',
                       help='Mode silencieux (affiche uniquement les erreurs)')
    
    args = parser.parse_args()
    
    # Configuration du niveau de logging
    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    elif args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Validation des seuils
    if not (0 < args.cpu_threshold <= 100):
        logger.error("Le seuil CPU doit être entre 0 et 100")
        sys.exit(1)
    
    if not (0 < args.memory_threshold <= 100):
        logger.error("Le seuil mémoire doit être entre 0 et 100")
        sys.exit(1)
    
    # Initialisation du client API
    logger.info(f"Connexion à vCenter: {args.vcenter}")
    api_client = VCenterAPIClient(
        vcenter_host=args.vcenter,
        username=args.username,
        password=args.password,
        verify_ssl=args.verify_ssl
    )
    
    exit_code = 0
    
    try:
        # Authentification
        if not api_client.authenticate():
            logger.error("❌ Impossible de se connecter au vCenter")
            logger.error("Vérifiez les informations de connexion et la disponibilité du vCenter")
            sys.exit(1)
        
        logger.info("✅ Connexion au vCenter réussie")
        
        # Initialisation du moniteur
        monitor = VMResourceMonitor(
            api_client=api_client,
            cpu_threshold=args.cpu_threshold,
            memory_threshold=args.memory_threshold,
            check_boot_issues=not args.no_check_boot,
            check_tools=not args.no_check_tools
        )
        
        # Monitoring des VMs
        logger.info("🔍 Démarrage du monitoring des VMs...")
        vm_statuses, vms_with_issues = monitor.monitor_all_vms()
        
        if not vm_statuses:
            logger.warning("⚠️  Aucune VM trouvée ou analysée")
            sys.exit(0)
        
        # Génération du rapport
        report = monitor.generate_report(vm_statuses, vms_with_issues)
        
        # Affichage du rapport
        if not args.quiet:
            print("\n" + report)
        
        # Sauvegarde du rapport texte si demandé
        if args.output:
            try:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(report)
                logger.info(f"📄 Rapport texte sauvegardé: {args.output}")
            except IOError as e:
                logger.error(f"❌ Erreur sauvegarde rapport texte: {e}")
                exit_code = 1
        
        # Export JSON si demandé
        if args.json_output:
            try:
                # Construction du rapport JSON structuré
                json_data = {
                    'metadata': {
                        'timestamp': datetime.now().isoformat(),
                        'vcenter_host': args.vcenter,
                        'total_vms': len(vm_statuses),
                        'vms_with_issues': len(vms_with_issues),
                        'thresholds': {
                            'cpu_percent': args.cpu_threshold,
                            'memory_percent': args.memory_threshold
                        }
                    },
                    'statistics': {
                        'power_states': {
                            'powered_on': sum(1 for vm in vm_statuses if vm.power_state == 'POWERED_ON'),
                            'powered_off': sum(1 for vm in vm_statuses if vm.power_state == 'POWERED_OFF'),
                            'suspended': sum(1 for vm in vm_statuses if vm.power_state == 'SUSPENDED')
                        },
                        'issues_by_type': {}
                    },
                    'vms': []
                }
                
                # Statistiques par type de problème
                for vm in vms_with_issues:
                    for issue in vm.issues:
                        issue_key = issue.value
                        if issue_key not in json_data['statistics']['issues_by_type']:
                            json_data['statistics']['issues_by_type'][issue_key] = 0
                        json_data['statistics']['issues_by_type'][issue_key] += 1
                
                # Détails de toutes les VMs
                for vm in vm_statuses:
                    vm_data = {
                        'name': vm.vm_name,
                        'id': vm.vm_id,
                        'power_state': vm.power_state,
                        'connection_state': vm.connection_state,
                        'overall_status': vm.overall_status,
                        'tools_running_status': vm.tools_running_status,
                        'boot_time': vm.boot_time,
                        'uptime_seconds': vm.uptime_seconds,
                        'cpu': {
                            'usage_percent': round(vm.cpu_usage_percent, 2),
                            'usage_mhz': round(vm.cpu_usage_mhz, 2),
                            'limit_mhz': round(vm.cpu_limit_mhz, 2),
                            'has_issue': VMIssueType.CPU_HIGH in vm.issues
                        },
                        'memory': {
                            'usage_percent': round(vm.memory_usage_percent, 2),
                            'usage_mb': round(vm.memory_usage_mb, 2),
                            'limit_mb': round(vm.memory_limit_mb, 2),
                            'active_mb': round(vm.memory_active_mb, 2),
                            'consumed_mb': round(vm.memory_consumed_mb, 2),
                            'has_issue': VMIssueType.MEMORY_HIGH in vm.issues
                        },
                        'issues': [issue.value for issue in vm.issues],
                        'has_issues': vm.has_issues
                    }
                    json_data['vms'].append(vm_data)
                
                # Sauvegarde du fichier JSON
                with open(args.json_output, 'w', encoding='utf-8') as f:
                    json.dump(json_data, f, indent=2, ensure_ascii=False)
                
                logger.info(f"📊 Rapport JSON sauvegardé: {args.json_output}")
                
            except (IOError, TypeError, ValueError) as e:
                logger.error(f"❌ Erreur sauvegarde rapport JSON: {e}")
                exit_code = 1
        
        # Résumé final
        if vms_with_issues:
            logger.warning(f"⚠️  {len(vms_with_issues)} VM(s) avec problèmes détectés")
            exit_code = 2  # Code 2 pour indiquer des problèmes détectés
            
            # Affichage résumé des problèmes critiques
            critical_issues = [
                vm for vm in vms_with_issues 
                if VMIssueType.BOOT_FAILURE in vm.issues 
                or vm.power_state == 'POWERED_OFF'
            ]
            
            if critical_issues:
                logger.error(f"🔴 {len(critical_issues)} VM(s) avec problèmes CRITIQUES:")
                for vm in critical_issues:
                    logger.error(f"   - {vm.vm_name}: {[i.value for i in vm.issues]}")
        else:
            logger.info("✅ Monitoring terminé avec succès, aucun problème détecté")
        
        # Statistiques finales
        logger.info(f"📈 Statistiques: {len(vm_statuses)} VMs analysées, "
                   f"{len(vms_with_issues)} avec problèmes")
        
    except KeyboardInterrupt:
        logger.info("⏹️  Interruption utilisateur (Ctrl+C)")
        exit_code = 130
    
    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ Erreur de connexion au vCenter: {e}")
        logger.error("Vérifiez que le vCenter est accessible et que l'URL est correcte")
        exit_code = 1
    
    except requests.exceptions.Timeout as e:
        logger.error(f"❌ Timeout lors de la connexion au vCenter: {e}")
        logger.error("Le vCenter met trop de temps à répondre")
        exit_code = 1
    
    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ Erreur HTTP: {e}")
        if e.response.status_code == 401:
            logger.error("Authentification échouée - Vérifiez vos identifiants")
        elif e.response.status_code == 403:
            logger.error("Accès refusé - Vérifiez les permissions de l'utilisateur")
        exit_code = 1
    
    except Exception as e:
        logger.exception(f"❌ Erreur inattendue: {e}")
        exit_code = 1
    
    finally:
        # Déconnexion propre
        try:
            api_client.disconnect()
        except Exception as e:
            logger.debug(f"Erreur lors de la déconnexion: {e}")
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()