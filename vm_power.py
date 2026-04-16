import requests
import csv
import logging
import json
import argparse
import os
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# CONFIGURATION LOGGING
# =========================
def setup_logging(log_file):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File
    fh = logging.FileHandler(log_file)
    fh.setFormatter(formatter)
    logger.addHandler(fh)


# =========================
# CLIENT VSPHERE
# =========================
class VSphereClient:
    def __init__(self, host, username, password, ca_cert, timeout=10):
        self.base_url = f"https://{host}"
        self.session = requests.Session()
        self.session.verify = ca_cert
        self.session.auth = (username, password)
        self.timeout = timeout
        self.session.headers.update({"Content-Type": "application/json"})
        self.token = None

    def authenticate(self):
        url = f"{self.base_url}/rest/com/vmware/cis/session"
        try:
            response = self.session.post(url, timeout=self.timeout)
            response.raise_for_status()
            self.token = response.json()["value"]
            self.session.headers.update(
                {"vmware-api-session-id": self.token}
            )
            logging.info("Authenticated successfully to vCenter")
        except requests.exceptions.SSLError as e:
            logging.error(f"SSL error: {e}")
            raise
        except Exception as e:
            logging.error(f"Authentication failed: {e}")
            raise

    def get_vm_id(self, vm_name):
        url = f"{self.base_url}/rest/vcenter/vm"
        params = {"filter.names": vm_name}

        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()

        vms = response.json().get("value", [])
        if not vms:
            return None
        return vms[0]["vm"]

    def get_power_state(self, vm_id):
        url = f"{self.base_url}/rest/vcenter/vm/{vm_id}/power"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()["value"]["state"]

    def perform_action(self, vm_id, action):
        action_map = {
            "power_on": "start",
            "power_off": "stop",
            "reset": "reset",
            "reboot": "reset",
            "shutdown_guest": "shutdown"
        }

        if action not in action_map:
            raise ValueError(f"Unsupported action: {action}")

        endpoint = action_map[action]
        url = f"{self.base_url}/rest/vcenter/vm/{vm_id}/power/{endpoint}"

        response = self.session.post(url, timeout=self.timeout)
        response.raise_for_status()


# =========================
# TRAITEMENT VM
# =========================
def process_vm(client, vm_name, action, dry_run=False):
    start_time = time.time()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    result = {
        "vm_name": vm_name,
        "action": action,
        "status": "",
        "message": "",
        "timestamp": timestamp,
        "duration": ""
    }

    try:
        vm_id = client.get_vm_id(vm_name)
        if not vm_id:
            result["status"] = "FAILED"
            result["message"] = "VM not found"
            return result

        state = client.get_power_state(vm_id)

        # Logique de skip intelligente
        if action == "power_on" and state == "POWERED_ON":
            result["status"] = "SKIPPED"
            result["message"] = "Already powered on"
            return result

        if action == "power_off" and state == "POWERED_OFF":
            result["status"] = "SKIPPED"
            result["message"] = "Already powered off"
            return result

        if dry_run:
            result["status"] = "SKIPPED"
            result["message"] = "Dry-run mode"
            return result

        # Retry simple
        for attempt in range(2):
            try:
                client.perform_action(vm_id, action)
                result["status"] = "SUCCESS"
                result["message"] = "Action completed"
                break
            except Exception as e:
                if attempt == 1:
                    raise
                time.sleep(1)

    except Exception as e:
        result["status"] = "FAILED"
        result["message"] = str(e)

    finally:
        result["duration"] = f"{round(time.time() - start_time, 2)}s"

    return result


# =========================
# LECTURE CSV
# =========================
def read_csv(file_path):
    with open(file_path, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        return [row["vm_name"] for row in reader]


# =========================
# RAPPORT
# =========================
def write_report(results, output_prefix):
    csv_file = f"{output_prefix}.csv"
    json_file = f"{output_prefix}.json"

    keys = results[0].keys()

    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(results)

    with open(json_file, "w") as f:
        json.dump(results, f, indent=4)

    logging.info(f"Reports generated: {csv_file}, {json_file}")


# =========================
# MAIN
# =========================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcenter", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--ca-cert", required=True)
    parser.add_argument("--threads", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"vm_power_{timestamp}.log"

    setup_logging(log_file)

    client = VSphereClient(
        args.vcenter,
        args.username,
        args.password,
        args.ca_cert
    )

    client.authenticate()

    vm_list = read_csv(args.input)

    results = []

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = {
            executor.submit(process_vm, client, vm, args.action, args.dry_run): vm
            for vm in vm_list
        }

        for future in as_completed(futures):
            result = future.result()
            logging.info(f"{result}")
            results.append(result)

    output_prefix = f"report_{timestamp}"
    write_report(results, output_prefix)


if __name__ == "__main__":
    main()
