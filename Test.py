import requests
from typing import List, Dict, Optional

# --- Utilitaires ---
def _req_json(session: requests.Session, method: str, url: str, **kwargs):
    r = session.request(method, url, verify=True, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()
import xml.etree.ElementTree as ET

def get_vms_on_hosts_cpu_below_requests(session, base_url, threshold_mhz=50.0):
    """
    Version 100% requests : envoie des requêtes SOAP pour interroger PerformanceManager.
    Nécessite que la session soit déjà authentifiée.
    """
    sdk_url = f"{base_url}/sdk"
import xml.etree.ElementTree as ET

def get_perf_manager_ref(session, base_url):
    sdk_url = f"{base_url}/sdk"
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "urn:vim25/5.5",
        "Accept-Encoding": "identity"
    }

    body = """<?xml version="1.0" encoding="UTF-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                      xmlns:vim25="urn:vim25">
      <soapenv:Body>
        <vim25:RetrieveServiceContent>
          <_this type="ServiceInstance">ServiceInstance</_this>
        </vim25:RetrieveServiceContent>
      </soapenv:Body>
    </soapenv:Envelope>"""

    r = session.post(sdk_url, data=body, headers=headers, verify=False)
    r.raise_for_status()

    # Utiliser le binaire brut et parser avec namespace
    root = ET.fromstring(r.content)
    ns = {
        "soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
        "vim25": "urn:vim25"
    }

    # Cherche l’élément perfManager
    perf_elem = root.find(".//vim25:perfManager", ns)
    if perf_elem is None:
        perf_elem = root.find(".//perfManager")  # fallback sans namespace

    if perf_elem is None:
        raise RuntimeError("Impossible de trouver <perfManager> dans la réponse SOAP.")

    perf_ref = perf_elem.text.strip() if perf_elem.text else None
    if not perf_ref:
        raise RuntimeError("Le tag <perfManager> est vide, impossible d’obtenir le MoRef.")

    return perf_ref

    def soap_request(body: str) -> ET.Element:
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "urn:vim25/5.5"}
        r = session.post(sdk_url, data=body, headers=headers, verify=False)
        r.raise_for_status()
        return ET.fromstring(r.text)

    # 1. Récupération du ServiceContent
    body_service = """<?xml version="1.0" encoding="UTF-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                      xmlns:vim25="urn:vim25">
      <soapenv:Body>
        <vim25:RetrieveServiceContent>
          <_this type="ServiceInstance">ServiceInstance</_this>
        </vim25:RetrieveServiceContent>
      </soapenv:Body>
    </soapenv:Envelope>"""
    resp = soap_request(body_service)
    content = resp.find(".//returnval")
    perf_manager_ref = content.find("./perfManager").attrib["value"]

    # 2. Récupération des hosts
    r = session.get(f"{base_url}/rest/vcenter/host", verify=False)
    r.raise_for_status()
    hosts = r.json().get("value", [])

    results = []

    # 3. QueryPerf pour chaque host
    for h in hosts:
        host_ref = h["host"]
        # SOAP QueryPerfRequest
        body_query = f"""<?xml version="1.0" encoding="UTF-8"?>
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                          xmlns:vim25="urn:vim25">
          <soapenv:Body>
            <vim25:QueryPerf>
              <_this type="PerformanceManager">{perf_manager_ref}</_this>
              <querySpec>
                <entity type="HostSystem">{host_ref}</entity>
                <metricId>
                  <counterId>6</counterId> <!-- 6 est souvent cpu.usagemhz.average -->
                </metricId>
                <intervalId>20</intervalId>
                <maxSample>1</maxSample>
              </querySpec>
            </vim25:QueryPerf>
          </soapenv:Body>
        </soapenv:Envelope>
        """
        resp = soap_request(body_query)
        vals = [v.text for v in resp.findall(".//value/value")]
        if not vals:
            continue
        try:
            cpu_mhz = float(vals[-1])
        except ValueError:
            continue

        if cpu_mhz < threshold_mhz:
            # récupérer les VMs sur cet host (via REST)
            params = {"filter.hosts": host_ref}
            r_vms = session.get(f"{base_url}/rest/vcenter/vm", params=params, verify=False)
            r_vms.raise_for_status()
            for vm in r_vms.json().get("value", []):
                vm["_host_id"] = host_ref
                vm["_host_cpu_mhz"] = cpu_mhz
                results.append(vm)

    return results

# --- 1) VMs éteintes ---
def get_powered_off_vms(session: requests.Session, base_url: str) -> List[Dict]:
    """
    Récupère toutes les VMs ayant power_state = POWERED_OFF via l'API REST vCenter.

    session : requests.Session déjà authentifiée (Basic auth or header 'vmware-api-session-id').
    base_url: ex. "https://vcenter.example.local" (ne pas mettre le / final)
    Retour : liste de dicts pour chaque VM: { 'vm': 'vm-123', 'name': 'vm01', 'power_state': 'POWERED_OFF', ... }
    """
    url = f"{base_url}/rest/vcenter/vm"
    params = {"filter.power_states": "POWERED_OFF"}  # filtre standard
    resp = _req_json(session, "GET", url, params=params)
    # la réponse standard a une clé 'value' contenant la liste (selon version)
    vms = resp.get("value", resp)
    return vms


# --- 2) VMs dont l'hôte a un CPU (MHz) < threshold_mhz ---
def get_vms_on_hosts_cpu_below(session: requests.Session, base_url: str, threshold_mhz: float = 50.0) -> List[Dict]:
    """
    Tente d'identifier les VMs dont l'ESXi host a un CPU < threshold_mhz (en MHz).

    Méthode :
      1) récupère la liste des hosts (/rest/vcenter/host)
      2) pour chaque host essaie plusieurs endpoints plausibles qui pourraient contenir une métrique CPU en MHz
         - si un endpoint retourne une métrique identifiable (ex: cpu_used_mhz, cpu_capacity_mhz, cpu.used_mhz...), on l'utilise
      3) récupère les VMs sur ces hosts (/rest/vcenter/vm?filter.hosts=<host-id>) et retourne la liste.

    Limitation importante : beaucoup de versions vCenter **ne** fournissent **pas** les métriques temps réel via l'API REST.
    Dans ce cas la fonction lève RuntimeError en expliquant quoi faire (pyvmomi / PerformanceManager).
    """
    # 1) lister hosts
    hosts_url = f"{base_url}/rest/vcenter/host"
    hosts_resp = _req_json(session, "GET", hosts_url)
    hosts = hosts_resp.get("value", hosts_resp)

    # candidate endpoints à tester (selon versions/custom extensions)
    host_metric_endpoints = [
        "/rest/vcenter/host/{host}/stats",
        "/rest/vcenter/host/{host}/performance",
        "/rest/appliance/monitoring/hosts/{host}/metrics",
        "/rest/vcenter/host/{host}",  # certains retours contiennent summary avec cpu info
        "/rest/vcenter/host/{host}/hardware",  # peut contenir cpu info statique (capacity)
    ]

    hosts_with_low_cpu = set()  # host ids qui satisfont condition

    for h in hosts:
        host_id = h.get("host") or h.get("host_id") or h.get("id")  # compatibilité
        if not host_id:
            continue

        # Try to get cpu info from the host listing itself (fast)
        # some API versions return 'summary' or 'cpu' fields in the host object
        maybe_cpu = None
        # check fields inside host listing
        for field in ("cpu", "cpu_info", "summary", "hardware"):
            if field in h:
                # bruteforce extraction
                candidate = h[field]
                if isinstance(candidate, dict):
                    # try common names
                    for key in ("mhz", "hz", "cpu_capacity_mhz", "cpu_capacity", "cpu_used_mhz", "cpu_used"):
                        if key in candidate:
                            maybe_cpu = candidate[key]
                            break
                elif isinstance(candidate, (int, float)):
                    maybe_cpu = candidate
            if maybe_cpu is not None:
                break

        # If we already found a numeric value, use it (assume MHz when plausible)
        if maybe_cpu is not None:
            try:
                cpu_val = float(maybe_cpu)
            except Exception:
                cpu_val = None
            if cpu_val is not None:
                if cpu_val < threshold_mhz:
                    hosts_with_low_cpu.add(host_id)
                continue  # go to next host

        # Otherwise try candidate endpoints
        found = False
        for ep in host_metric_endpoints:
            url = base_url + ep.format(host=host_id)
            try:
                resp = _req_json(session, "GET", url)
            except requests.HTTPError:
                continue
            # recherche heuristique d'une valeur cpu en MHz dans la réponse JSON
            def find_cpu_value(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        lk = str(k).lower()
                        if lk in ("cpu", "cpu_used_mhz", "cpu_capacity_mhz", "cpu_used", "cpu.value", "cpu_mhz", "mhz", "cpu.capacity"):
                            try:
                                return float(v)
                            except Exception:
                                pass
                        # dive deeper
                        res = find_cpu_value(v)
                        if res is not None:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_cpu_value(item)
                        if res is not None:
                            return res
                return None

            cpu_val = find_cpu_value(resp)
            if cpu_val is not None:
                # heuristique : si valeur trop grande et exprimée en Hz, normaliser (Hz -> MHz)
                if cpu_val > 1e6:
                    cpu_val = cpu_val / 1e6
                if cpu_val < threshold_mhz:
                    hosts_with_low_cpu.add(host_id)
                found = True
                break

        # si aucun endpoint renvoyé une métrique, continuer (on essaie tous les hosts)
        if not found:
            continue

    # Si on n'a trouvé AUCUN host avec métriques via REST -> lever une erreur informative
    if not hosts_with_low_cpu:
        # avant de lever, il se peut simplement qu'aucun host n'ait cpu < threshold; mais très souvent
        # c'est parce que l'API REST vCenter ne fournit pas ces métriques.
        # Nous allons vérifier si on a au moins tenté un endpoint qui a renvoyé des valeurs.
        # pour être simple ici : on lève une erreur expliquant la limitation.
        raise RuntimeError(
            "Aucune métrique CPU hôte accessible via l'API REST (ou aucun host < threshold). "
            "Beaucoup de versions de vCenter n'exposent pas les métriques temps-réel via REST. "
            "Pour obtenir des métriques CPU en MHz fiables, utilisez l'API Performance (pyvmomi / PerformanceManager / Get-Stat). "
            "Si vous voulez, je fournis un exemple pyvmomi pour filtrer les VMs par utilisation CPU hôte."
        )

    # Récupérer les VMs sur ces hosts
    result_vms = []
    for host_id in hosts_with_low_cpu:
        vm_list_url = f"{base_url}/rest/vcenter/vm"
        params = {"filter.hosts": host_id}
        try:
            resp = _req_json(session, "GET", vm_list_url, params=params)
        except requests.HTTPError:
            continue
        vms = resp.get("value", resp)
        # enrichir avec host info
        for vm in vms:
            vm['_host_id'] = host_id
            result_vms.append(vm)

    return result_vms
