#!/usr/bin/env python3
"""
Script de monitoring des ressources CPU et mémoire des VMs vCenter 8+
Utilise l'API REST vSphere + API Performance Manager (SOAP) pour métriques réelles.

Auteur: Expert Python
Version: 4.0.0
Compatible: vCenter 8.0+
"""

import requests
import json
import logging
import sys
import argparse
import warnings
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib3.exceptions import InsecureRequestWarning
from enum import Enum
import ssl
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import atexit

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
    TOOLS_NOT_RUNNING = "TOOLS_NOT_RUNNING"
    UPTIME_SHORT = "UPTIME_SHORT"


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
    power_state: str
    tools_running_status: str
    boot_time: Optional[str]
    uptime_seconds: Optional[int]
    host_name: Optional[str]
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
            f"  VMware Tools: {self.tools_running_status}\n"
            f"  Host ESXi: {self.host_name or 'N/A'}\n"
            f"  Temps de démarrage: {self.boot_time or 'N/A'}\n"
            f"  Uptime: {uptime_str}\n"
            f"  CPU: {self.cpu_usage_percent:.2f}% "
            f"({self.cpu_usage_mhz:.0f}/{self.cpu_limit_mhz:.0f} MHz)\n"
            f"  Mémoire: {self.memory_usage_percent:.2f}% "
            f"({self.memory_usage_mb:.0f}/{self.memory_limit_mb:.0f} MB)\n"
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
            
            logger.info("✅ Authentification réussie")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.debug(f"Échec endpoint /api/session: {e}, tentative /rest/...")
            try:
                old_auth_url = f"{self.rest_url}/com/vmware/cis/session"
                response = self.session.post(
                    old_auth_url,
                    auth=(self.username, self.password),
                    timeout=30
                )
                response.raise_for_status()
                
                result = response.json()
                self.session_id = result.get('value', result)
                self.session.headers.update({
                    'vmware-api-session-id': self.session_id,
                    'Content-Type': 'application/json'
                })
                
                logger.info("✅ Authentification réussie (ancien endpoint)")
                return True
                
            except Exception as e2:
                logger.error(f"❌ Échec de l'authentification: {e2}")
                return False
    
    def disconnect(self) -> None:
        """Ferme la session vCenter"""
        if self.session_id:
            try:
                delete_url = f"{self.base_url}/session"
                self.session.delete(delete_url, timeout=10)
                logger.info("Déconnexion réussie")
            except Exception as e:
                logger.debug(f"Erreur lors de la déconnexion: {e}")
    
    def get_all_vms(self) -> List[Dict]:
        """
        Récupère la liste de toutes les VMs
        
        Returns:
            List[Dict]: Liste des VMs avec leurs informations de base
        """
        try:
            url = f"{self.base_url}/vcenter/vm"
            response = self.session.get(url, timeout=30)
            
            if response.status_code == 404:
                url = f"{self.rest_url}/vcenter/vm"
                response = self.session.get(url, timeout=30)
            
            response.raise_for_status()
            data = response.json()
            
            vms = data.get('value', data) if isinstance(data, dict) else data
            
            logger.info(f"✅ Nombre de VMs récupérées: {len(vms)}")
            return vms
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Erreur lors de la récupération des VMs: {e}")
            return []
    
    def get_vm_by_name(self, vm_name: str) -> Optional[Dict]:
        """
        Récupère une VM par son nom
        
        Args:
            vm_name: Nom de la VM
            
        Returns:
            Dict: Informations de base de la VM ou None
        """
        try:
            url = f"{self.base_url}/vcenter/vm"
            params = {"filter.names": vm_name}
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 404:
                url = f"{self.rest_url}/vcenter/vm"
                response = self.session.get(url, params=params, timeout=30)
            
            response.raise_for_status()
            data = response.json()
            vms = data.get('value', data) if isinstance(data, dict) else data
            
            if vms and len(vms) > 0:
                return vms[0]
            
            logger.warning(f"⚠️  VM '{vm_name}' non trouvée")
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Erreur lors de la recherche de VM '{vm_name}': {e}")
            return None
    
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
            
            if response.status_code == 404:
                url = f"{self.rest_url}/vcenter/vm/{vm_id}"
                response = self.session.get(url, timeout=30)
            
            response.raise_for_status()
            data = response.json()
            
            return data.get('value', data) if isinstance(data, dict) else data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Erreur récupération détails VM {vm_id}: {e}")
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
            
            if cpu_response.status_code == 404:
                cpu_url = f"{self.rest_url}/vcenter/vm/{vm_id}/hardware/cpu"
                memory_url = f"{self.rest_url}/vcenter/vm/{vm_id}/hardware/memory"
                cpu_response = self.session.get(cpu_url, timeout=30)
                memory_response = self.session.get(memory_url, timeout=30)
            
            cpu_response.raise_for_status()
            memory_response.raise_for_status()
            
            cpu_data = cpu_response.json()
            memory_data = memory_response.json()
            
            return {
                'cpu': cpu_data.get('value', cpu_data) if isinstance(cpu_data, dict) else cpu_data,
                'memory': memory_data.get('value', memory_data) if isinstance(memory_data, dict) else memory_data
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Erreur récupération hardware VM {vm_id}: {e}")
            return None
    
    def get_host_name(self, host_id: str) -> Optional[str]:
        """
        Récupère le nom d'un host ESXi
        
        Args:
            host_id: Identifiant du host
            
        Returns:
            str: Nom du host ou None
        """
        try:
            url = f"{self.base_url}/vcenter/host/{host_id}"
            response = self.session.get(url, timeout=30)
            
            if response.status_code == 404:
                url = f"{self.rest_url}/vcenter/host/{host_id}"
                response = self.session.get(url, timeout=30)
            
            response.raise_for_status()
            data = response.json()
            host_data = data.get('value', data) if isinstance(data, dict) else data
            
            return host_data.get('name', host_id)
            
        except Exception as e:
            logger.debug(f"Erreur récupération nom host {host_id}: {e}")
            return None


class PerformanceManager:
    """Client pour récupérer les métriques de performance via API SOAP pyVmomi"""
    
    def __init__(self, vcenter_host: str, username: str, password: str, verify_ssl: bool = False):
        """
        Initialise le client Performance Manager
        
        Args:
            vcenter_host: Hostname ou IP du vCenter
            username: Nom d'utilisateur
            password: Mot de passe
            verify_ssl: Vérifier les certificats SSL
        """
        self.vcenter_host = vcenter_host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.si = None
        self.perf_manager = None
        
        logger.info("Initialisation du Performance Manager")
    
    def connect(self) -> bool:
        """
        Se connecte au vCenter via pyVmomi
        
        Returns:
            bool: True si connexion réussie
        """
        try:
            context = None
            if not self.verify_ssl:
                context = ssl._create_unverified_context()
            
            self.si = SmartConnect(
                host=self.vcenter_host,
                user=self.username,
                pwd=self.password,
                port=443,
                sslContext=context
            )
            
            atexit.register(Disconnect, self.si)
            self.perf_manager = self.si.content.perfManager
            
            logger.info("✅ Connexion Performance Manager réussie")
            return True
            
        except Exception as e:
            logger.error(f"❌ Erreur connexion Performance Manager: {e}")
            return False
    
    def disconnect(self):
        """Déconnexion du vCenter"""
        if self.si:
            try:
                Disconnect(self.si)
                logger.info("Déconnexion Performance Manager réussie")
            except Exception as e:
                logger.debug(f"Erreur déconnexion Performance Manager: {e}")
    
    def get_vm_by_moref(self, vm_moref: str) -> Optional[vim.VirtualMachine]:
        """
        Récupère l'objet VM par son MoRef
        
        Args:
            vm_moref: MoRef de la VM (ex: 'vm-123')
            
        Returns:
            vim.VirtualMachine ou None
        """
        try:
            content = self.si.RetrieveContent()
            container = content.viewManager.CreateContainerView(
                content.rootFolder, [vim.VirtualMachine], True
            )
            
            for vm in container.view:
                if vm._moId == vm_moref:
                    container.Destroy()
                    return vm
            
            container.Destroy()
            return None
            
        except Exception as e:
            logger.error(f"Erreur récupération VM {vm_moref}: {e}")
            return None
    
    def get_vm_performance_metrics(self, vm_moref: str) -> Optional[Dict[str, float]]:
        """
        Récupère les métriques de performance temps réel d'une VM
        
        Args:
            vm_moref: MoRef de la VM
            
        Returns:
            Dict contenant cpu_usage_mhz, cpu_usage_percent, memory_usage_mb, memory_usage_percent
        """
        try:
            vm = self.get_vm_by_moref(vm_moref)
            if not vm:
                logger.warning(f"VM {vm_moref} non trouvée pour métriques")
                return None
            
            if vm.runtime.powerState != 'poweredOn':
                return {
                    'cpu_usage_mhz': 0.0,
                    'cpu_usage_percent': 0.0,
                    'memory_usage_mb': 0.0,
                    'memory_usage_percent': 0.0
                }
            
            # Métriques instantanées disponibles
            metric_ids = [
                vim.PerformanceManager.MetricId(counterId=6, instance=""),    # cpu.usage.average
                vim.PerformanceManager.MetricId(counterId=24, instance=""),   # mem.usage.average
                vim.PerformanceManager.MetricId(counterId=125, instance=""),  # cpu.usagemhz.average
            ]
            
            # Query pour métriques temps réel (20 secondes)
            query_spec = vim.PerformanceManager.QuerySpec(
                entity=vm,
                metricId=metric_ids,
                intervalId=20,
                maxSample=1
            )
            
            perf_results = self.perf_manager.QueryPerf(querySpec=[query_spec])
            
            metrics = {
                'cpu_usage_mhz': 0.0,
                'cpu_usage_percent': 0.0,
                'memory_usage_mb': 0.0,
                'memory_usage_percent': 0.0
            }
            
            if perf_results and len(perf_results) > 0:
                result = perf_results[0]
                
                for metric_series in result.value:
                    counter_id = metric_series.id.counterId
                    
                    if len(metric_series.value) > 0:
                        value = metric_series.value[0]
                        
                        # CPU usage en %
                        if counter_id == 6:
                            metrics['cpu_usage_percent'] = value / 100.0
                        
                        # Memory usage en %
                        elif counter_id == 24:
                            metrics['memory_usage_percent'] = value / 100.0
                            # Calculer MB depuis %
                            if vm.config and vm.config.hardware.memoryMB:
                                metrics['memory_usage_mb'] = (value / 100.0) * vm.config.hardware.memoryMB
                        
                        # CPU usage en MHz
                        elif counter_id == 125:
                            metrics['cpu_usage_mhz'] = float(value)
            
            # Calcul CPU % depuis MHz si non disponible
            if metrics['cpu_usage_percent'] == 0.0 and metrics['cpu_usage_mhz'] > 0:
                if vm.config and vm.config.hardware:
                    num_cpu = vm.config.hardware.numCPU
                    cpu_limit_mhz = num_cpu * 2000  # Estimation 2GHz par CPU
                    if cpu_limit_mhz > 0:
                        metrics['cpu_usage_percent'] = (metrics['cpu_usage_mhz'] / cpu_limit_mhz) * 100.0
            
            return metrics
            
        except Exception as e:
            logger.error(f"❌ Erreur récupération métriques VM {vm_moref}: {e}")
            return None


class VMResourceMonitor:
    """Moniteur de ressources pour les VMs vCenter avec métriques temps réel"""
    
    def __init__(self, api_client: VCenterAPIClient,
                 perf_manager: PerformanceManager,
                 cpu_threshold: float = 80.0,
                 memory_threshold: float = 90.0,
                 check_boot_issues: bool = True,
                 check_tools: bool = True,
                 uptime_threshold_minutes: int = 5):
        """
        Initialise le moniteur de ressources
        
        Args:
            api_client: Client API vCenter
            perf_manager: Performance Manager pour métriques
            cpu_threshold: Seuil d'alerte CPU en pourcentage (défaut: 80%)
            memory_threshold: Seuil d'alerte mémoire en pourcentage (défaut: 90%)
            check_boot_issues: Vérifier les problèmes de boot (défaut: True)
            check_tools: Vérifier l'état des VMware Tools (défaut: True)
            uptime_threshold_minutes: Seuil uptime court en minutes (défaut: 5)
        """
        self.api_client = api_client
        self.perf_manager = perf_manager
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.check_boot_issues = check_boot_issues
        self.check_tools = check_tools
        self.uptime_threshold_seconds = uptime_threshold_minutes * 60
        
        logger.info(f"⚙️  Seuils configurés - CPU: {cpu_threshold}%, Mémoire: {memory_threshold}%")
        logger.info(f"⚙️  Vérification boot: {check_boot_issues}, Tools: {check_tools}, "
                   f"Uptime court: {uptime_threshold_minutes}min")
    
    def analyze_vm_resources(self, vm_id: str, vm_name: str) -> Optional[VMResourceStatus]:
        """
        Analyse complète des ressources et de l'état d'une VM
        
        Args:
            vm_id: Identifiant de la VM
            vm_name: Nom de la VM
            
        Returns:
            VMResourceStatus: Statut complet de la VM ou None en cas d'erreur
        """
        vm_details = self.api_client.get_vm_details(vm_id)
        if not vm_details:
            logger.warning(f"⚠️  Impossible de récupérer les détails pour VM {vm_name}")
            return None
        
        power_state = vm_details.get('power_state', 'UNKNOWN')
        
        guest_os = vm_details.get('guest_OS', {})
        tools_running_status = guest_os.get('tools_running_status', 'UNKNOWN')
        
        boot_time = vm_details.get('boot_time')
        uptime_seconds = None
        if boot_time and power_state == 'POWERED_ON':
            try:
                boot_dt = datetime.fromisoformat(boot_time.replace('Z', '+00:00'))
                uptime_seconds = int((datetime.now(boot_dt.tzinfo) - boot_dt).total_seconds())
            except Exception as e:
                logger.debug(f"Erreur calcul uptime pour VM {vm_name}: {e}")
        
        host_id = vm_details.get('host')
        host_name = None
        if host_id:
            host_name = self.api_client.get_host_name(host_id)
        
        hardware_info = self.api_client.get_vm_hardware_info(vm_id)
        if not hardware_info:
            logger.warning(f"⚠️  Impossible de récupérer les infos hardware pour VM {vm_name}")
            return None
        
        cpu_count = hardware_info['cpu'].get('count', 1)
        cores_per_socket = hardware_info['cpu'].get('cores_per_socket', 1)
        
        cpu_limit_mhz = cpu_count * cores_per_socket * 2000
        memory_limit_mb = hardware_info['memory'].get('size_MiB', 0)
        
        # Récupération des métriques temps réel via Performance Manager
        cpu_usage_mhz = 0.0
        cpu_usage_percent = 0.0
        memory_usage_mb = 0.0
        memory_usage_percent = 0.0
        
        if power_state == 'POWERED_ON':
            metrics = self.perf_manager.get_vm_performance_metrics(vm_id)
            if metrics:
                cpu_usage_mhz = metrics.get('cpu_usage_mhz', 0.0)
                cpu_usage_percent = metrics.get('cpu_usage_percent', 0.0)
                memory_usage_mb = metrics.get('memory_usage_mb', 0.0)
                memory_usage_percent = metrics.get('memory_usage_percent', 0.0)
                
                logger.debug(f"Métriques VM {vm_name}: CPU={cpu_usage_percent:.2f}%, MEM={memory_usage_percent:.2f}%")
            else:
                logger.warning(f"⚠️  Métriques non disponibles pour VM {vm_name}")
        
        issues = self._detect_issues(
            power_state=power_state,
            tools_running_status=tools_running_status,
            cpu_usage_percent=cpu_usage_percent,
            memory_usage_percent=memory_usage_percent,
            uptime_seconds=uptime_seconds
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
            power_state=power_state,
            tools_running_status=tools_running_status,
            boot_time=boot_time,
            uptime_seconds=uptime_seconds,
            host_name=host_name,
            issues=issues
        )
    
    def _detect_issues(self, power_state: str, tools_running_status: str,
                      cpu_usage_percent: float, memory_usage_percent: float,
                      uptime_seconds: Optional[int]) -> List[VMIssueType]:
        """
        Détecte les problèmes sur une VM
        
        Returns:
            List[VMIssueType]: Liste des problèmes détectés
        """
        issues = []
        
        if power_state == 'POWERED_OFF':
            issues.append(VMIssueType.POWERED_OFF)
        elif power_state == 'SUSPENDED':
            issues.append(VMIssueType.SUSPENDED)
        
        if self.check_boot_issues and power_state == 'POWERED_ON':
            if uptime_seconds is not None and uptime_seconds < self.uptime_threshold_seconds:
                issues.append(VMIssueType.UPTIME_SHORT)
                logger.debug(f"VM avec uptime court: {uptime_seconds}s")
        
        if self.check_tools and power_state == 'POWERED_ON':
            if tools_running_status in ['NOT_RUNNING', 'UNKNOWN']:
                issues.append(VMIssueType.TOOLS_NOT_RUNNING)
        
        if power_state == 'POWERED_ON':
            if cpu_usage_percent > self.cpu_threshold:
                issues.append(VMIssueType.CPU_HIGH)
            
            if memory_usage_percent > self.memory_threshold:
                issues.append(VMIssueType.MEMORY_HIGH)
        
        return issues
    
    def monitor_all_vms(self) -> Tuple[List[VMResourceStatus], List[VMResourceStatus]]:
        """
        Monitore toutes les VMs et détecte les problèmes
        
        Returns:
            Tuple contenant (toutes_les_vms, vms_avec_problèmes)
        """
        logger.info("🔍 Début du monitoring de toutes les VMs...")
        
        all_vms = self.api_client.get_all_vms()
        vm_statuses = []
        vms_with_issues = []
        
        for idx, vm in enumerate(all_vms, 1):
            vm_id = vm.get('vm')
            vm_name = vm.get('name', 'Unknown')
            
            logger.info(f"[{idx}/{len(all_vms)}] Analyse: {vm_name}")
            
            status = self.analyze_vm_resources(vm_id, vm_name)
            if status:
                vm_statuses.append(status)
                
                if status.has_issues:
                    vms_with_issues.append(status)
                    logger.warning(f"⚠️  Problèmes détectés sur {vm_name}: "
                                 f"{[issue.value for issue in status.issues]}")
        
        logger.info(f"✅ Monitoring terminé. VMs analysées: {len(vm_statuses)}, "
                   f"VMs avec problèmes: {len(vms_with_issues)}")
        
        return vm_statuses, vms_with_issues
    
    def monitor_vm_list(self, vm_names: List[str]) -> Tuple[List[VMResourceStatus], List[VMResourceStatus]]:
        """
        Monitore une liste spécifique de VMs par leurs noms
        
        Args:
            vm_names: Liste des noms de VMs à monitorer
            
        Returns:
            Tuple contenant (toutes_les_vms_analysées, vms_avec_problèmes)
        """
        logger.info(f"🔍 Début du monitoring de {len(vm_names)} VMs spécifiques...")
        
        vm_statuses = []
        vms_with_issues = []
        vms_not_found = []
        
        for idx, vm_name in enumerate(vm_names, 1):
            logger.info(f"[{idx}/{len(vm_names)}] Recherche et analyse: {vm_name}")
            
            vm_info = self.api_client.get_vm_by_name(vm_name)
            
            if not vm_info:
                vms_not_found.append(vm_name)
                logger.error(f"❌ VM '{vm_name}' non trouvée dans le vCenter")
                continue
            
            vm_id = vm_info.get('vm')
            
            status = self.analyze_vm_resources(vm_id,vm_name)
            if status:
                vm_statuses.append(status)
                
                if status.has_issues:
                    vms_with_issues.append(status)
                    logger.warning(f"⚠️  Problèmes détectés sur {vm_name}: "
                                 f"{[issue.value for issue in status.issues]}")
        
        if vms_not_found:
            logger.warning(f"⚠️  {len(vms_not_found)} VM(s) non trouvées: {', '.join(vms_not_found)}")
        
        logger.info(f"✅ Monitoring liste terminé. VMs trouvées et analysées: {len(vm_statuses)}, "
                   f"VMs avec problèmes: {len(vms_with_issues)}")
        
        return vm_statuses, vms_with_issues
    
    def generate_report(self, vm_statuses: List[VMResourceStatus], 
                       vms_with_issues: List[VMResourceStatus],
                       mode: str = "all") -> str:
        """
        Génère un rapport de monitoring détaillé
        
        Args:
            vm_statuses: Liste de tous les statuts de VMs
            vms_with_issues: Liste des VMs avec problèmes
            mode: Mode de monitoring ("all" ou "list")
            
        Returns:
            str: Rapport formaté
        """
        report_lines = [
            "=" * 80,
            f"RAPPORT DE MONITORING VCENTER - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 80,
            "",
            f"Mode de monitoring: {mode.upper()}",
            f"Nombre de VMs analysées: {len(vm_statuses)}",
            f"VMs avec problèmes détectés: {len(vms_with_issues)}",
            f"Seuil CPU: {self.cpu_threshold}%",
            f"Seuil Mémoire: {self.memory_threshold}%",
            "",
        ]
        
        if vms_with_issues:
            report_lines.append("🚨 ALERTE - VMs AVEC PROBLÈMES:")
            report_lines.append("=" * 80)
            
            issues_by_type = {}
            for vm_status in vms_with_issues:
                for issue in vm_status.issues:
                    if issue not in issues_by_type:
                        issues_by_type[issue] = []
                    issues_by_type[issue].append(vm_status)
            
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
        
        powered_on = [vm for vm in vm_statuses if vm.power_state == 'POWERED_ON']
        powered_off = [vm for vm in vm_statuses if vm.power_state == 'POWERED_OFF']
        suspended = [vm for vm in vm_statuses if vm.power_state == 'SUSPENDED']
        
        report_lines.append(f"État d'alimentation:")
        report_lines.append(f"  ✓ Allumées (POWERED_ON): {len(powered_on)}")
        report_lines.append(f"  ✗ Éteintes (POWERED_OFF): {len(powered_off)}")
        report_lines.append(f"  ⏸ Suspendues (SUSPENDED): {len(suspended)}")
        
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


def export_report_to_file(report: str, output_file: str) -> bool:
    """
    Exporte le rapport texte dans un fichier
    
    Args:
        report: Contenu du rapport
        output_file: Chemin du fichier de sortie
        
    Returns:
        bool: True si succès, False sinon
    """
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"📄 Rapport texte sauvegardé: {output_file}")
        return True
    except IOError as e:
        logger.error(f"❌ Erreur sauvegarde rapport texte: {e}")
        return False


def export_json_report(vm_statuses: List[VMResourceStatus],
                      vms_with_issues: List[VMResourceStatus],
                      monitoring_mode: str,
                      vcenter_host: str,
                      cpu_threshold: float,
                      memory_threshold: float,
                      uptime_threshold: int,
                      json_output_file: str) -> bool:
    """
    Exporte le rapport au format JSON
    
    Args:
        vm_statuses: Liste de tous les statuts de VMs
        vms_with_issues: Liste des VMs avec problèmes
        monitoring_mode: Mode de monitoring
        vcenter_host: Hostname du vCenter
        cpu_threshold: Seuil CPU
        memory_threshold: Seuil mémoire
        uptime_threshold: Seuil uptime
        json_output_file: Chemin du fichier JSON
        
    Returns:
        bool: True si succès, False sinon
    """
    try:
        json_data = {
            'metadata': {
                'timestamp': datetime.now().isoformat(),
                'vcenter_host': vcenter_host,
                'monitoring_mode': monitoring_mode,
                'total_vms': len(vm_statuses),
                'vms_with_issues': len(vms_with_issues),
                'thresholds': {
                    'cpu_percent': cpu_threshold,
                    'memory_percent': memory_threshold,
                    'uptime_minutes': uptime_threshold
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
        
        for vm in vms_with_issues:
            for issue in vm.issues:
                issue_key = issue.value
                if issue_key not in json_data['statistics']['issues_by_type']:
                    json_data['statistics']['issues_by_type'][issue_key] = 0
                json_data['statistics']['issues_by_type'][issue_key] += 1
        
        for vm in vm_statuses:
            vm_data = {
                'name': vm.vm_name,
                'id': vm.vm_id,
                'power_state': vm.power_state,
                'tools_running_status': vm.tools_running_status,
                'host_name': vm.host_name,
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
                    'has_issue': VMIssueType.MEMORY_HIGH in vm.issues
                },
                'issues': [issue.value for issue in vm.issues],
                'has_issues': vm.has_issues
            }
            json_data['vms'].append(vm_data)
        
        with open(json_output_file, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"📊 Rapport JSON sauvegardé: {json_output_file}")
        return True
        
    except (IOError, TypeError, ValueError) as e:
        logger.error(f"❌ Erreur sauvegarde rapport JSON: {e}")
        return False


def main():
    """Fonction principale du script"""
    parser = argparse.ArgumentParser(
        description='Monitoring avancé des VMs vCenter 8+ avec métriques temps réel',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples d'utilisation:

  # Monitoring de toutes les VMs
  %(prog)s --vcenter vcenter.example.com --username admin@vsphere.local --password 'Pass123!'
  
  # Monitoring d'une liste spécifique de VMs
  %(prog)s --vcenter vcenter.local --username admin@vsphere.local --password 'Pass123!' \\
    --vm-list "VM-Web-01,VM-DB-01,VM-App-01"
  
  # Monitoring avec fichier contenant les noms de VMs
  %(prog)s --vcenter vcenter.local --username admin@vsphere.local --password 'Pass123!' \\
    --vm-list-file vms_to_monitor.txt
  
  # Avec seuils personnalisés et export
  %(prog)s --vcenter 192.168.1.10 --username admin@vsphere.local --password 'Pass123!' \\
    --cpu-threshold 85 --memory-threshold 95 \\
    --output rapport.txt --json-output results.json
        """
    )
    
    parser.add_argument('--vcenter', required=True,
                       help='Hostname ou IP du vCenter')
    parser.add_argument('--username', required=True,
                       help='Nom d\'utilisateur vCenter (ex: administrator@vsphere.local)')
    parser.add_argument('--password', required=True,
                       help='Mot de passe vCenter')
    
    monitoring_group = parser.add_mutually_exclusive_group()
    monitoring_group.add_argument('--vm-list', type=str,
                                 help='Liste de VMs séparées par des virgules (ex: "VM1,VM2,VM3")')
    monitoring_group.add_argument('--vm-list-file', type=str,
                                 help='Fichier contenant les noms de VMs (un par ligne)')
    
    parser.add_argument('--cpu-threshold', type=float, default=80.0,
                       help='Seuil d\'alerte CPU en pourcentage (défaut: 80)')
    parser.add_argument('--memory-threshold', type=float, default=90.0,
                       help='Seuil d\'alerte mémoire en pourcentage (défaut: 90)')
    parser.add_argument('--uptime-threshold', type=int, default=5,
                       help='Seuil uptime court en minutes (défaut: 5)')
    
    parser.add_argument('--no-check-boot', action='store_true',
                       help='Désactiver la vérification des problèmes de boot')
    parser.add_argument('--no-check-tools', action='store_true',
                       help='Désactiver la vérification des VMware Tools')
    
    parser.add_argument('--verify-ssl', action='store_true',
                       help='Vérifier les certificats SSL (désactivé par défaut)')
    parser.add_argument('--output', type=str,
                       help='Fichier de sortie pour le rapport texte')
    parser.add_argument('--json-output', type=str,
                       help='Fichier de sortie pour le rapport JSON')
    
    parser.add_argument('--verbose', action='store_true',
                       help='Mode verbeux (affiche plus de détails de debug)')
    parser.add_argument('--quiet', action='store_true',
                       help='Mode silencieux (affiche uniquement les erreurs)')
    
    args = parser.parse_args()
    
    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    elif args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if not (0 < args.cpu_threshold <= 100):
        logger.error("❌ Le seuil CPU doit être entre 0 et 100")
        sys.exit(1)
    
    if not (0 < args.memory_threshold <= 100):
        logger.error("❌ Le seuil mémoire doit être entre 0 et 100")
        sys.exit(1)
    
    if args.uptime_threshold < 0:
        logger.error("❌ Le seuil uptime doit être positif")
        sys.exit(1)
    
    vm_names_to_monitor = None
    monitoring_mode = "all"
    
    if args.vm_list:
        vm_names_to_monitor = [name.strip() for name in args.vm_list.split(',') if name.strip()]
        monitoring_mode = "list"
        logger.info(f"📋 Mode liste: {len(vm_names_to_monitor)} VMs à monitorer")
    
    elif args.vm_list_file:
        try:
            with open(args.vm_list_file, 'r', encoding='utf-8') as f:
                vm_names_to_monitor = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            monitoring_mode = "list"
            logger.info(f"📋 Mode liste depuis fichier: {len(vm_names_to_monitor)} VMs à monitorer")
        except IOError as e:
            logger.error(f"❌ Erreur lecture fichier VM list: {e}")
            sys.exit(1)
    
    logger.info(f"🔌 Connexion à vCenter: {args.vcenter}")
    api_client = VCenterAPIClient(
        vcenter_host=args.vcenter,
        username=args.username,
        password=args.password,
        verify_ssl=args.verify_ssl
    )
    
    perf_manager = PerformanceManager(
        vcenter_host=args.vcenter,
        username=args.username,
        password=args.password,
        verify_ssl=args.verify_ssl
    )
    
    exit_code = 0
    
    try:
        if not api_client.authenticate():
            logger.error("❌ Impossible de se connecter au vCenter")
            logger.error("Vérifiez les informations de connexion et la disponibilité du vCenter")
            sys.exit(1)
        
        logger.info("✅ Connexion au vCenter réussie")
        
        if not perf_manager.connect():
            logger.error("❌ Impossible de se connecter au Performance Manager")
            sys.exit(1)
        
        monitor = VMResourceMonitor(
            api_client=api_client,
            perf_manager=perf_manager,
            cpu_threshold=args.cpu_threshold,
            memory_threshold=args.memory_threshold,
            check_boot_issues=not args.no_check_boot,
            check_tools=not args.no_check_tools,
            uptime_threshold_minutes=args.uptime_threshold
        )
        
        logger.info("🔍 Démarrage du monitoring des VMs...")
        
        if monitoring_mode == "list" and vm_names_to_monitor:
            vm_statuses, vms_with_issues = monitor.monitor_vm_list(vm_names_to_monitor)
        else:
            vm_statuses, vms_with_issues = monitor.monitor_all_vms()
        
        if not vm_statuses:
            logger.warning("⚠️  Aucune VM trouvée ou analysée")
            sys.exit(0)
        
        report = monitor.generate_report(vm_statuses, vms_with_issues, monitoring_mode)
        
        if not args.quiet:
            print("\n" + report)
        
        if args.output:
            if not export_report_to_file(report, args.output):
                exit_code = 1
        
        if args.json_output:
            if not export_json_report(
                vm_statuses=vm_statuses,
                vms_with_issues=vms_with_issues,
                monitoring_mode=monitoring_mode,
                vcenter_host=args.vcenter,
                cpu_threshold=args.cpu_threshold,
                memory_threshold=args.memory_threshold,
                uptime_threshold=args.uptime_threshold,
                json_output_file=args.json_output
            ):
                exit_code = 1
        
        if vms_with_issues:
            logger.warning(f"⚠️  {len(vms_with_issues)} VM(s) avec problèmes détectés")
            exit_code = 2
            
            critical_issues = [
                vm for vm in vms_with_issues 
                if vm.power_state in ['POWERED_OFF', 'SUSPENDED']
                or VMIssueType.TOOLS_NOT_RUNNING in vm.issues
            ]
            
            if critical_issues:
                logger.error(f"🔴 {len(critical_issues)} VM(s) avec problèmes CRITIQUES:")
                for vm in critical_issues:
                    logger.error(f"   - {vm.vm_name}: {[i.value for i in vm.issues]}")
        else:
            logger.info("✅ Monitoring terminé avec succès, aucun problème détecté")
        
        logger.info(f"📈 Statistiques: {len(vm_statuses)} VMs analysées, "
                   f"{len(vms_with_issues)} avec problèmes")
        
    except KeyboardInterrupt:
        logger.info("ℹ️  Interruption utilisateur (Ctrl+C)")
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
        try:
            api_client.disconnect()
            perf_manager.disconnect()
        except Exception as e:
            logger.debug(f"Erreur lors de la déconnexion: {e}")
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()