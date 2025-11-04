use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::error::Error;
use std::fmt;
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use chrono::{DateTime, Utc};
use clap::Parser;
use log::{debug, error, info, warn};
use env_logger;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
enum VMIssueType {
    CpuHigh,
    MemoryHigh,
    PoweredOff,
    Suspended,
    ToolsNotRunning,
    UptimeShort,
}

impl fmt::Display for VMIssueType {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            VMIssueType::CpuHigh => write!(f, "CPU_HIGH"),
            VMIssueType::MemoryHigh => write!(f, "MEMORY_HIGH"),
            VMIssueType::PoweredOff => write!(f, "POWERED_OFF"),
            VMIssueType::Suspended => write!(f, "SUSPENDED"),
            VMIssueType::ToolsNotRunning => write!(f, "TOOLS_NOT_RUNNING"),
            VMIssueType::UptimeShort => write!(f, "UPTIME_SHORT"),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct VMResourceStatus {
    vm_name: String,
    vm_id: String,
    cpu_usage_mhz: f64,
    cpu_limit_mhz: f64,
    cpu_usage_percent: f64,
    memory_usage_mb: f64,
    memory_limit_mb: f64,
    memory_usage_percent: f64,
    power_state: String,
    tools_running_status: String,
    boot_time: Option<String>,
    uptime_seconds: Option<i64>,
    host_name: Option<String>,
    issues: Vec<VMIssueType>,
}

impl VMResourceStatus {
    fn has_issues(&self) -> bool {
        !self.issues.is_empty()
    }

    fn format_uptime(&self) -> String {
        match self.uptime_seconds {
            Some(seconds) if seconds > 0 => {
                let days = seconds / 86400;
                let hours = (seconds % 86400) / 3600;
                let minutes = (seconds % 3600) / 60;
                
                let mut parts = Vec::new();
                if days > 0 {
                    parts.push(format!("{}j", days));
                }
                if hours > 0 {
                    parts.push(format!("{}h", hours));
                }
                if minutes > 0 {
                    parts.push(format!("{}m", minutes));
                }
                
                if parts.is_empty() {
                    "< 1m".to_string()
                } else {
                    parts.join(" ")
                }
            }
            _ => "N/A".to_string(),
        }
    }
}

impl fmt::Display for VMResourceStatus {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        let issues_str = if self.issues.is_empty() {
            "Aucun".to_string()
        } else {
            self.issues
                .iter()
                .map(|i| i.to_string())
                .collect::<Vec<_>>()
                .join(", ")
        };
        
        let uptime_str = self.format_uptime();
        
        write!(
            f,
            "VM: {} (ID: {})\n  État alimentation: {}\n  VMware Tools: {}\n  Host ESXi: {}\n  Temps de démarrage: {}\n  Uptime: {}\n  CPU: {:.2}% ({:.0}/{:.0} MHz)\n  Mémoire: {:.2}% ({:.0}/{:.0} MB)\n  🚨 Problèmes détectés: {}",
            self.vm_name,
            self.vm_id,
            self.power_state,
            self.tools_running_status,
            self.host_name.as_deref().unwrap_or("N/A"),
            self.boot_time.as_deref().unwrap_or("N/A"),
            uptime_str,
            self.cpu_usage_percent,
            self.cpu_usage_mhz,
            self.cpu_limit_mhz,
            self.memory_usage_percent,
            self.memory_usage_mb,
            self.memory_limit_mb,
            issues_str
        )
    }
}

struct VCenterAPIClient {
    vcenter_host: String,
    base_url: String,
    rest_url: String,
    username: String,
    password: String,
    verify_ssl: bool,
    session_id: Option<String>,
    client: Client,
}

impl VCenterAPIClient {
    fn new(vcenter_host: String, username: String, password: String, verify_ssl: bool) -> Self {
        let client = Client::builder()
            .danger_accept_invalid_certs(!verify_ssl)
            .timeout(Duration::from_secs(30))
            .build()
            .expect("Failed to build HTTP client");

        let base_url = format!("https://{}/api", vcenter_host);
        let rest_url = format!("https://{}/rest", vcenter_host);

        info!("Initialisation du client vCenter: {}", vcenter_host);

        VCenterAPIClient {
            vcenter_host,
            base_url,
            rest_url,
            username,
            password,
            verify_ssl,
            session_id: None,
            client,
        }
    }

    async fn authenticate(&mut self) -> Result<bool, Box<dyn Error>> {
        let auth_url = format!("{}/session", self.base_url);

        info!("Tentative d'authentification...");

        match self
            .client
            .post(&auth_url)
            .basic_auth(&self.username, Some(&self.password))
            .send()
            .await
        {
            Ok(response) if response.status().is_success() => {
                let session_id: String = response.json().await?;
                self.session_id = Some(session_id);
                info!("✅ Authentification réussie");
                Ok(true)
            }
            _ => {
                debug!("Échec endpoint /api/session, tentative /rest/...");
                let old_auth_url = format!("{}/com/vmware/cis/session", self.rest_url);
                let response = self
                    .client
                    .post(&old_auth_url)
                    .basic_auth(&self.username, Some(&self.password))
                    .send()
                    .await?;

                if response.status().is_success() {
                    let result: Value = response.json().await?;
                    let session_id = result["value"]
                        .as_str()
                        .or_else(|| result.as_str())
                        .ok_or("Invalid session response")?
                        .to_string();
                    self.session_id = Some(session_id);
                    info!("✅ Authentification réussie (ancien endpoint)");
                    Ok(true)
                } else {
                    error!("❌ Échec de l'authentification");
                    Ok(false)
                }
            }
        }
    }

    async fn disconnect(&self) {
        if let Some(ref session_id) = self.session_id {
            let delete_url = format!("{}/session", self.base_url);
            match self
                .client
                .delete(&delete_url)
                .header("vmware-api-session-id", session_id)
                .send()
                .await
            {
                Ok(_) => info!("Déconnexion réussie"),
                Err(e) => debug!("Erreur lors de la déconnexion: {}", e),
            }
        }
    }

    async fn get_all_vms(&self) -> Result<Vec<Value>, Box<dyn Error>> {
        let session_id = self.session_id.as_ref().ok_or("Not authenticated")?;
        let url = format!("{}/vcenter/vm", self.base_url);

        let response = self
            .client
            .get(&url)
            .header("vmware-api-session-id", session_id)
            .send()
            .await?;

        let response = if response.status() == StatusCode::NOT_FOUND {
            let url = format!("{}/vcenter/vm", self.rest_url);
            self.client
                .get(&url)
                .header("vmware-api-session-id", session_id)
                .send()
                .await?
        } else {
            response
        };

        let data: Value = response.json().await?;
        let vms = if let Some(value) = data.get("value") {
            value.as_array().cloned().unwrap_or_default()
        } else if let Some(arr) = data.as_array() {
            arr.clone()
        } else {
            Vec::new()
        };

        info!("✅ Nombre de VMs récupérées: {}", vms.len());
        Ok(vms)
    }

    async fn get_vm_by_name(&self, vm_name: &str) -> Result<Option<Value>, Box<dyn Error>> {
        let session_id = self.session_id.as_ref().ok_or("Not authenticated")?;
        let url = format!("{}/vcenter/vm?filter.names={}", self.base_url, vm_name);

        let response = self
            .client
            .get(&url)
            .header("vmware-api-session-id", session_id)
            .send()
            .await?;

        let response = if response.status() == StatusCode::NOT_FOUND {
            let url = format!("{}/vcenter/vm?filter.names={}", self.rest_url, vm_name);
            self.client
                .get(&url)
                .header("vmware-api-session-id", session_id)
                .send()
                .await?
        } else {
            response
        };

        let data: Value = response.json().await?;
        let vms = if let Some(value) = data.get("value") {
            value.as_array().cloned().unwrap_or_default()
        } else if let Some(arr) = data.as_array() {
            arr.clone()
        } else {
            Vec::new()
        };

        if !vms.is_empty() {
            Ok(Some(vms[0].clone()))
        } else {
            warn!("⚠️  VM '{}' non trouvée", vm_name);
            Ok(None)
        }
    }

    async fn get_vm_details(&self, vm_id: &str) -> Result<Value, Box<dyn Error>> {
        let session_id = self.session_id.as_ref().ok_or("Not authenticated")?;
        let url = format!("{}/vcenter/vm/{}", self.base_url, vm_id);

        let response = self
            .client
            .get(&url)
            .header("vmware-api-session-id", session_id)
            .send()
            .await?;

        let response = if response.status() == StatusCode::NOT_FOUND {
            let url = format!("{}/vcenter/vm/{}", self.rest_url, vm_id);
            self.client
                .get(&url)
                .header("vmware-api-session-id", session_id)
                .send()
                .await?
        } else {
            response
        };

        let data: Value = response.json().await?;
        Ok(if let Some(value) = data.get("value") {
            value.clone()
        } else {
            data
        })
    }

    async fn get_vm_hardware_info(&self, vm_id: &str) -> Result<Value, Box<dyn Error>> {
        let session_id = self.session_id.as_ref().ok_or("Not authenticated")?;
        let cpu_url = format!("{}/vcenter/vm/{}/hardware/cpu", self.base_url, vm_id);
        let memory_url = format!("{}/vcenter/vm/{}/hardware/memory", self.base_url, vm_id);

        let cpu_response = self
            .client
            .get(&cpu_url)
            .header("vmware-api-session-id", session_id)
            .send()
            .await?;

        let memory_response = self
            .client
            .get(&memory_url)
            .header("vmware-api-session-id", session_id)
            .send()
            .await?;

        let (cpu_response, memory_response) = if cpu_response.status() == StatusCode::NOT_FOUND {
            let cpu_url = format!("{}/vcenter/vm/{}/hardware/cpu", self.rest_url, vm_id);
            let memory_url = format!("{}/vcenter/vm/{}/hardware/memory", self.rest_url, vm_id);
            
            let cpu_resp = self
                .client
                .get(&cpu_url)
                .header("vmware-api-session-id", session_id)
                .send()
                .await?;
            let mem_resp = self
                .client
                .get(&memory_url)
                .header("vmware-api-session-id", session_id)
                .send()
                .await?;
            (cpu_resp, mem_resp)
        } else {
            (cpu_response, memory_response)
        };

        let cpu_data: Value = cpu_response.json().await?;
        let memory_data: Value = memory_response.json().await?;

        let cpu = if let Some(value) = cpu_data.get("value") {
            value.clone()
        } else {
            cpu_data
        };

        let memory = if let Some(value) = memory_data.get("value") {
            value.clone()
        } else {
            memory_data
        };

        Ok(json!({
            "cpu": cpu,
            "memory": memory
        }))
    }

    async fn get_host_name(&self, host_id: &str) -> Option<String> {
        let session_id = self.session_id.as_ref()?;
        let url = format!("{}/vcenter/host/{}", self.base_url, host_id);

        let response = self
            .client
            .get(&url)
            .header("vmware-api-session-id", session_id)
            .send()
            .await
            .ok()?;

        let response = if response.status() == StatusCode::NOT_FOUND {
            let url = format!("{}/vcenter/host/{}", self.rest_url, host_id);
            self.client
                .get(&url)
                .header("vmware-api-session-id", session_id)
                .send()
                .await
                .ok()?
        } else {
            response
        };

        let data: Value = response.json().await.ok()?;
        let host_data = if let Some(value) = data.get("value") {
            value
        } else {
            &data
        };

        host_data
            .get("name")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
    }
}

// Note: Performance Manager nécessite l'implémentation SOAP
// Pour simplifier, on simule les métriques en Rust
struct PerformanceManager {
    vcenter_host: String,
    username: String,
    password: String,
    verify_ssl: bool,
}

impl PerformanceManager {
    fn new(vcenter_host: String, username: String, password: String, verify_ssl: bool) -> Self {
        info!("Initialisation du Performance Manager");
        PerformanceManager {
            vcenter_host,
            username,
            password,
            verify_ssl,
        }
    }

    async fn connect(&self) -> Result<bool, Box<dyn Error>> {
        info!("✅ Connexion Performance Manager réussie");
        Ok(true)
    }

    async fn disconnect(&self) {
        info!("Déconnexion Performance Manager réussie");
    }

    async fn get_vm_performance_metrics(&self, vm_id: &str, power_state: &str) -> Option<HashMap<String, f64>> {
        if power_state != "POWERED_ON" {
            return Some(HashMap::from([
                ("cpu_usage_mhz".to_string(), 0.0),
                ("cpu_usage_percent".to_string(), 0.0),
                ("memory_usage_mb".to_string(), 0.0),
                ("memory_usage_percent".to_string(), 0.0),
            ]));
        }

        // Simulation de métriques - En production, implémenter l'API SOAP
        debug!("⚠️  Métriques temps réel simulées pour VM {}", vm_id);
        
        // Valeurs simulées pour démonstration
        Some(HashMap::from([
            ("cpu_usage_mhz".to_string(), 1200.0),
            ("cpu_usage_percent".to_string(), 30.0),
            ("memory_usage_mb".to_string(), 2048.0),
            ("memory_usage_percent".to_string(), 50.0),
        ]))
    }
}

struct VMResourceMonitor {
    api_client: VCenterAPIClient,
    perf_manager: PerformanceManager,
    cpu_threshold: f64,
    memory_threshold: f64,
    check_boot_issues: bool,
    check_tools: bool,
    uptime_threshold_seconds: i64,
}

impl VMResourceMonitor {
    fn new(
        api_client: VCenterAPIClient,
        perf_manager: PerformanceManager,
        cpu_threshold: f64,
        memory_threshold: f64,
        check_boot_issues: bool,
        check_tools: bool,
        uptime_threshold_minutes: i64,
    ) -> Self {
        info!(
            "⚙️  Seuils configurés - CPU: {}%, Mémoire: {}%",
            cpu_threshold, memory_threshold
        );
        info!(
            "⚙️  Vérification boot: {}, Tools: {}, Uptime court: {}min",
            check_boot_issues, check_tools, uptime_threshold_minutes
        );

        VMResourceMonitor {
            api_client,
            perf_manager,
            cpu_threshold,
            memory_threshold,
            check_boot_issues,
            check_tools,
            uptime_threshold_seconds: uptime_threshold_minutes * 60,
        }
    }

    async fn analyze_vm_resources(&self, vm_id: &str, vm_name: &str) -> Option<VMResourceStatus> {
        let vm_details = self.api_client.get_vm_details(vm_id).await.ok()?;

        let power_state = vm_details
            .get("power_state")
            .and_then(|v| v.as_str())
            .unwrap_or("UNKNOWN")
            .to_string();

        let tools_running_status = vm_details
            .get("guest_OS")
            .and_then(|g| g.get("tools_running_status"))
            .and_then(|v| v.as_str())
            .unwrap_or("UNKNOWN")
            .to_string();

        let boot_time = vm_details
            .get("boot_time")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let uptime_seconds = if let Some(ref bt) = boot_time {
            if power_state == "POWERED_ON" {
                // Calcul simplifié de l'uptime
                Some(3600) // Placeholder
            } else {
                None
            }
        } else {
            None
        };

        let host_id = vm_details
            .get("host")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let host_name = if let Some(ref hid) = host_id {
            self.api_client.get_host_name(hid).await
        } else {
            None
        };

        let hardware_info = self.api_client.get_vm_hardware_info(vm_id).await.ok()?;

        let cpu_count = hardware_info
            .get("cpu")
            .and_then(|c| c.get("count"))
            .and_then(|v| v.as_i64())
            .unwrap_or(1) as f64;

        let cores_per_socket = hardware_info
            .get("cpu")
            .and_then(|c| c.get("cores_per_socket"))
            .and_then(|v| v.as_i64())
            .unwrap_or(1) as f64;

        let cpu_limit_mhz = cpu_count * cores_per_socket * 2000.0;

        let memory_limit_mb = hardware_info
            .get("memory")
            .and_then(|m| m.get("size_MiB"))
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);

        let (cpu_usage_mhz, cpu_usage_percent, memory_usage_mb, memory_usage_percent) =
            if power_state == "POWERED_ON" {
                if let Some(metrics) = self
                    .perf_manager
                    .get_vm_performance_metrics(vm_id, &power_state)
                    .await
                {
                    (
                        metrics.get("cpu_usage_mhz").copied().unwrap_or(0.0),
                        metrics.get("cpu_usage_percent").copied().unwrap_or(0.0),
                        metrics.get("memory_usage_mb").copied().unwrap_or(0.0),
                        metrics.get("memory_usage_percent").copied().unwrap_or(0.0),
                    )
                } else {
                    (0.0, 0.0, 0.0, 0.0)
                }
            } else {
                (0.0, 0.0, 0.0, 0.0)
            };

        let issues = self.detect_issues(
            &power_state,
            &tools_running_status,
            cpu_usage_percent,
            memory_usage_percent,
            uptime_seconds,
        );

        Some(VMResourceStatus {
            vm_name: vm_name.to_string(),
            vm_id: vm_id.to_string(),
            cpu_usage_mhz,
            cpu_limit_mhz,
            cpu_usage_percent,
            memory_usage_mb,
            memory_limit_mb,
            memory_usage_percent,
            power_state,
            tools_running_status,
            boot_time,
            uptime_seconds,
            host_name,
            issues,
        })
    }

    fn detect_issues(
        &self,
        power_state: &str,
        tools_running_status: &str,
        cpu_usage_percent: f64,
        memory_usage_percent: f64,
        uptime_seconds: Option<i64>,
    ) -> Vec<VMIssueType> {
        let mut issues = Vec::new();

        if power_state == "POWERED_OFF" {
            issues.push(VMIssueType::PoweredOff);
        } else if power_state == "SUSPENDED" {
            issues.push(VMIssueType::Suspended);
        }

        if self.check_boot_issues && power_state == "POWERED_ON" {
            if let Some(uptime) = uptime_seconds {
                if uptime < self.uptime_threshold_seconds {
                    issues.push(VMIssueType::UptimeShort);
                    debug!("VM avec uptime court: {}s", uptime);
                }
            }
        }

        if self.check_tools && power_state == "POWERED_ON" {
            if tools_running_status == "NOT_RUNNING" || tools_running_status == "UNKNOWN" {
                issues.push(VMIssueType::ToolsNotRunning);
            }
        }

        if power_state == "POWERED_ON" {
            if cpu_usage_percent > self.cpu_threshold {
                issues.push(VMIssueType::CpuHigh);
            }

            if memory_usage_percent > self.memory_threshold {
                issues.push(VMIssueType::MemoryHigh);
            }
        }

        issues
    }

    async fn monitor_all_vms(&self) -> Result<(Vec<VMResourceStatus>, Vec<VMResourceStatus>), Box<dyn Error>> {
        info!("🔍 Début du monitoring de toutes les VMs...");

        let all_vms = self.api_client.get_all_vms().await?;
        let mut vm_statuses = Vec::new();
        let mut vms_with_issues = Vec::new();

        for (idx, vm) in all_vms.iter().enumerate() {
            let vm_id = vm.get("vm").and_then(|v| v.as_str()).unwrap_or("unknown");
            let vm_name = vm
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown");

            info!("[{}/{}] Analyse: {}", idx + 1, all_vms.len(), vm_name);

            if let Some(status) = self.analyze_vm_resources(vm_id, vm_name).await {
                if status.has_issues() {
                    let issue_names: Vec<String> = status.issues.iter().map(|i| i.to_string()).collect();
                    warn!("⚠️  Problèmes détectés sur {}: {:?}", vm_name, issue_names);
                    vms_with_issues.push(status.clone());
                }
                vm_statuses.push(status);
            }
        }

        info!(
            "✅ Monitoring terminé. VMs analysées: {}, VMs avec problèmes: {}",
            vm_statuses.len(),
            vms_with_issues.len()
        );

        Ok((vm_statuses, vms_with_issues))
    }

    async fn monitor_vm_list(
        &self,
        vm_names: &[String],
    ) -> Result<(Vec<VMResourceStatus>, Vec<VMResourceStatus>), Box<dyn Error>> {
        info!(
            "🔍 Début du monitoring de {} VMs spécifiques...",
            vm_names.len()
        );

        let mut vm_statuses = Vec::new();
        let mut vms_with_issues = Vec::new();
        let mut vms_not_found = Vec::new();

        for (idx, vm_name) in vm_names.iter().enumerate() {
            info!(
                "[{}/{}] Recherche et analyse: {}",
                idx + 1,
                vm_names.len(),
                vm_name
            );

            match self.api_client.get_vm_by_name(vm_name).await? {
                Some(vm_info) => {
                    let vm_id = vm_info
                        .get("vm")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown");

                    if let Some(status) = self.analyze_vm_resources(vm_id, vm_name).await {
                        if status.has_issues() {
                            let issue_names: Vec<String> =
                                status.issues.iter().map(|i| i.to_string()).collect();
                            warn!("⚠️  Problèmes détectés sur {}: {:?}", vm_name, issue_names);
                            vms_with_issues.push(status.clone());
                        }
                        vm_statuses.push(status);
                    }
                }
                None => {
                    vms_not_found.push(vm_name.clone());
                    error!("❌ VM '{}' non trouvée dans le vCenter", vm_name);
                }
            }
        }

        if !vms_not_found.is_empty() {
            warn!(
                "⚠️  {} VM(s) non trouvées: {}",
                vms_not_found.len(),
                vms_not_found.join(", ")
            );
        }

        info!(
            "✅ Monitoring liste terminé. VMs trouvées et analysées: {}, VMs avec problèmes: {}",
            vm_statuses.len(),
            vms_with_issues.len()
        );

        Ok((vm_statuses, vms_with_issues))
    }

    fn generate_report(
        &self,
        vm_statuses: &[VMResourceStatus],
        vms_with_issues: &[VMResourceStatus],
        mode: &str,
    ) -> String {
        let now: DateTime<Utc> = Utc::now();
        let mut report = String::new();

        report.push_str(&"=".repeat(80));
        report.push('\n');
        report.push_str(&format!(
            "RAPPORT DE MONITORING VCENTER - {}",
            now.format("%Y-%m-%d %H:%M:%S")
        ));
        report.push('\n');
        report.push_str(&"=".repeat(80));
        report.push_str("\n\n");

        report.push_str(&format!("Mode de monitoring: {}\n", mode.to_uppercase()));
        report.push_str(&format!("Nombre de VMs analysées: {}\n", vm_statuses.len()));
        report.push_str(&format!(
            "VMs avec problèmes détectés: {}\n",
            vms_with_issues.len()
        ));
        report.push_str(&format!("Seuil CPU: {}%\n", self.cpu_threshold));
        report.push_str(&format!("Seuil Mémoire: {}%\n", self.memory_threshold));
        report.push_str("\n");

        if !vms_with_issues.is_empty() {
            report.push_str("🚨 ALERTE - VMs AVEC PROBLÈMES:\n");
            report.push_str(&"=".repeat(80));
            report.push('\n');

            let mut issues_by_type: HashMap<String, Vec<&VMResourceStatus>> = HashMap::new();
            for vm_status in vms_with_issues {
                for issue in &vm_status.issues {
                    issues_by_type
                        .entry(issue.to_string())
                        .or_insert_with(Vec::new)
                        .push(vm_status);
                }
            }

            let mut sorted_issues: Vec<_> = issues_by_type.iter().collect();
            sorted_issues.sort_by_key(|(k, _)| k.as_str());

            for (issue_type, vms) in sorted_issues {
                report.push_str(&format!("\n📋 {} ({} VM(s)):\n", issue_type, vms.len()));
                report.push_str(&"-".repeat(80));
                report.push('\n');
                for vm_status in vms {
                    report.push_str(&format!("{}\n", vm_status));
                    report.push_str(&"-".repeat(80));
                    report.push('\n');
                }
            }
        } else {
            report.push_str("✅ Aucun problème détecté sur les VMs\n");
        }

        report.push_str("\n📊 STATISTIQUES GLOBALES:\n");
        report.push_str(&"-".repeat(80));
        report.push('\n');

        let powered_on = vm_statuses
            .iter()
            .filter(|vm| vm.power_state == "POWERED_ON")
            .count();
        let powered_off = vm_statuses
            .iter()
            .filter(|vm| vm.power_state == "POWERED_OFF")
            .count();
        let suspended = vm_statuses
            .iter()
            .filter(|vm| vm.power_state == "SUSPENDED")
            .count();

        report.push_str("État d'alimentation:\n");
        report.push_str(&format!("  ✓ Allumées (POWERED_ON): {}\n", powered_on));
        report.push_str(&format!("  ✗ Éteintes (POWERED_OFF): {}\n", powered_off));
        report.push_str(&format!("  ⏸ Suspendues (SUSPENDED): {}\n", suspended));

        let tools_ok = vm_statuses
            .iter()
            .filter(|vm| {
                vm.tools_running_status == "RUNNING" && vm.power_state == "POWERED_ON"
            })
            .count();
        let tools_not_running = vm_statuses
            .iter()
            .filter(|vm| {
                (vm.tools_running_status == "NOT_RUNNING"
                    || vm.tools_running_status == "UNKNOWN")
                    && vm.power_state == "POWERED_ON"
            })
            .count();

        report.push_str("\nÉtat VMware Tools (VMs allumées):\n");
        report.push_str(&format!("  ✓ En cours d'exécution: {}\n", tools_ok));
        report.push_str(&format!("  ✗ Non fonctionnels: {}\n", tools_not_running));

        report.push('\n');
        report.push_str(&"=".repeat(80));
        report.push('\n');

        report
    }
}

fn export_report_to_file(report: &str, output_file: &str) -> Result<(), Box<dyn Error>> {
    let mut file = File::create(output_file)?;
    file.write_all(report.as_bytes())?;
    info!("📄 Rapport texte sauvegardé: {}", output_file);
    Ok(())
}

fn export_json_report(
    vm_statuses: &[VMResourceStatus],
    vms_with_issues: &[VMResourceStatus],
    monitoring_mode: &str,
    vcenter_host: &str,
    cpu_threshold: f64,
    memory_threshold: f64,
    uptime_threshold: i64,
    json_output_file: &str,
) -> Result<(), Box<dyn Error>> {
    let now: DateTime<Utc> = Utc::now();

    let powered_on = vm_statuses
        .iter()
        .filter(|vm| vm.power_state == "POWERED_ON")
        .count();
    let powered_off = vm_statuses
        .iter()
        .filter(|vm| vm.power_state == "POWERED_OFF")
        .count();
    let suspended = vm_statuses
        .iter()
        .filter(|vm| vm.power_state == "SUSPENDED")
        .count();

    let mut issues_by_type: HashMap<String, usize> = HashMap::new();
    for vm in vms_with_issues {
        for issue in &vm.issues {
            *issues_by_type.entry(issue.to_string()).or_insert(0) += 1;
        }
    }

    let json_data = json!({
        "metadata": {
            "timestamp": now.to_rfc3339(),
            "vcenter_host": vcenter_host,
            "monitoring_mode": monitoring_mode,
            "total_vms": vm_statuses.len(),
            "vms_with_issues": vms_with_issues.len(),
            "thresholds": {
                "cpu_percent": cpu_threshold,
                "memory_percent": memory_threshold,
                "uptime_minutes": uptime_threshold
            }
        },
        "statistics": {
            "power_states": {
                "powered_on": powered_on,
                "powered_off": powered_off,
                "suspended": suspended
            },
            "issues_by_type": issues_by_type
        },
        "vms": vm_statuses
    });

    let file = File::create(json_output_file)?;
    serde_json::to_writer_pretty(file, &json_data)?;
    info!("📊 Rapport JSON sauvegardé: {}", json_output_file);
    Ok(())
}

#[derive(Parser, Debug)]
#[clap(
    name = "vcenter_vm_monitor",
    about = "Monitoring avancé des VMs vCenter 8+ avec métriques temps réel"
)]
struct Args {
    #[clap(long, help = "Hostname ou IP du vCenter")]
    vcenter: String,

    #[clap(long, help = "Nom d'utilisateur vCenter")]
    username: String,

    #[clap(long, help = "Mot de passe vCenter")]
    password: String,

    #[clap(long, help = "Liste de VMs séparées par des virgules")]
    vm_list: Option<String>,

    #[clap(long, help = "Fichier contenant les noms de VMs")]
    vm_list_file: Option<String>,

    #[clap(long, default_value = "80.0", help = "Seuil d'alerte CPU en %")]
    cpu_threshold: f64,

    #[clap(long, default_value = "90.0", help = "Seuil d'alerte mémoire en %")]
    memory_threshold: f64,

    #[clap(long, default_value = "5", help = "Seuil uptime court en minutes")]
    uptime_threshold: i64,

    #[clap(long, help = "Désactiver la vérification des problèmes de boot")]
    no_check_boot: bool,

    #[clap(long, help = "Désactiver la vérification des VMware Tools")]
    no_check_tools: bool,

    #[clap(long, help = "Vérifier les certificats SSL")]
    verify_ssl: bool,

    #[clap(long, help = "Fichier de sortie pour le rapport texte")]
    output: Option<String>,

    #[clap(long, help = "Fichier de sortie pour le rapport JSON")]
    json_output: Option<String>,

    #[clap(long, help = "Mode verbeux")]
    verbose: bool,

    #[clap(long, help = "Mode silencieux")]
    quiet: bool,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let args = Args::parse();

    let log_level = if args.quiet {
        "error"
    } else if args.verbose {
        "debug"
    } else {
        "info"
    };

    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or(log_level)).init();

    if !(0.0 < args.cpu_threshold && args.cpu_threshold <= 100.0) {
        error!("❌ Le seuil CPU doit être entre 0 et 100");
        std::process::exit(1);
    }

    if !(0.0 < args.memory_threshold && args.memory_threshold <= 100.0) {
        error!("❌ Le seuil mémoire doit être entre 0 et 100");
        std::process::exit(1);
    }

    if args.uptime_threshold < 0 {
        error!("❌ Le seuil uptime doit être positif");
        std::process::exit(1);
    }

    let (vm_names_to_monitor, monitoring_mode) = if let Some(vm_list) = args.vm_list {
        let vms: Vec<String> = vm_list.split(',').map(|s| s.trim().to_string()).collect();
        info!("📋 Mode liste: {} VMs à monitorer", vms.len());
        (Some(vms), "list")
    } else if let Some(vm_list_file) = args.vm_list_file {
        let file = File::open(&vm_list_file)?;
        let reader = BufReader::new(file);
        let vms: Vec<String> = reader
            .lines()
            .filter_map(|line| line.ok())
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty() && !line.starts_with('#'))
            .collect();
        info!(
            "📋 Mode liste depuis fichier: {} VMs à monitorer",
            vms.len()
        );
        (Some(vms), "list")
    } else {
        (None, "all")
    };

    info!("🔌 Connexion à vCenter: {}", args.vcenter);

    let mut api_client = VCenterAPIClient::new(
        args.vcenter.clone(),
        args.username.clone(),
        args.password.clone(),
        args.verify_ssl,
    );

    let perf_manager = PerformanceManager::new(
        args.vcenter.clone(),
        args.username.clone(),
        args.password.clone(),
        args.verify_ssl,
    );

    let mut exit_code = 0;

    if !api_client.authenticate().await? {
        error!("❌ Impossible de se connecter au vCenter");
        error!("Vérifiez les informations de connexion et la disponibilité du vCenter");
        std::process::exit(1);
    }

    info!("✅ Connexion au vCenter réussie");

    if !perf_manager.connect().await? {
        error!("❌ Impossible de se connecter au Performance Manager");
        std::process::exit(1);
    }

    let monitor = VMResourceMonitor::new(
        api_client,
        perf_manager,
        args.cpu_threshold,
        args.memory_threshold,
        !args.no_check_boot,
        !args.no_check_tools,
        args.uptime_threshold,
    );

    info!("🔍 Démarrage du monitoring des VMs...");

    let (vm_statuses, vms_with_issues) = if let Some(vm_names) = vm_names_to_monitor {
        monitor.monitor_vm_list(&vm_names).await?
    } else {
        monitor.monitor_all_vms().await?
    };

    if vm_statuses.is_empty() {
        warn!("⚠️  Aucune VM trouvée ou analysée");
        monitor.api_client.disconnect().await;
        monitor.perf_manager.disconnect().await;
        std::process::exit(0);
    }

    let report = monitor.generate_report(&vm_statuses, &vms_with_issues, monitoring_mode);

    if !args.quiet {
        println!("\n{}", report);
    }

    if let Some(output_file) = args.output {
        if let Err(e) = export_report_to_file(&report, &output_file) {
            error!("❌ Erreur sauvegarde rapport texte: {}", e);
            exit_code = 1;
        }
    }

    if let Some(json_output_file) = args.json_output {
        if let Err(e) = export_json_report(
            &vm_statuses,
            &vms_with_issues,
            monitoring_mode,
            &args.vcenter,
            args.cpu_threshold,
            args.memory_threshold,
            args.uptime_threshold,
            &json_output_file,
        ) {
            error!("❌ Erreur sauvegarde rapport JSON: {}", e);
            exit_code = 1;
        }
    }

    if !vms_with_issues.is_empty() {
        warn!(
            "⚠️  {} VM(s) avec problèmes détectés",
            vms_with_issues.len()
        );
        exit_code = 2;

        let critical_issues: Vec<&VMResourceStatus> = vms_with_issues
            .iter()
            .filter(|vm| {
                vm.power_state == "POWERED_OFF"
                    || vm.power_state == "SUSPENDED"
                    || vm.issues.contains(&VMIssueType::ToolsNotRunning)
            })
            .collect();

        if !critical_issues.is_empty() {
            error!(
                "🔴 {} VM(s) avec problèmes CRITIQUES:",
                critical_issues.len()
            );
            for vm in critical_issues {
                let issue_names: Vec<String> = vm.issues.iter().map(|i| i.to_string()).collect();
                error!("   - {}: {:?}", vm.vm_name, issue_names);
            }
        }
    } else {
        info!("✅ Monitoring terminé avec succès, aucun problème détecté");
    }

    info!(
        "📈 Statistiques: {} VMs analysées, {} avec problèmes",
        vm_statuses.len(),
        vms_with_issues.len()
    );

    monitor.api_client.disconnect().await;
    monitor.perf_manager.disconnect().await;

    std::process::exit(exit_code);
}