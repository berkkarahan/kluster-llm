//! Fixed OS operations. Caller never supplies executable paths or shell text.
use super::{refuse, Result, Scope};
#[cfg(target_os = "macos")]
use regex::Regex;
use serde_json::{json, Value};
use std::{
    io::Read,
    net::Ipv4Addr,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

const MIN_DEDICATED_LINK_SPEED_MBPS: u64 = 1_000;
#[cfg(any(target_os = "windows", test))]
const MIN_DEDICATED_LINK_SPEED_BPS: u64 = MIN_DEDICATED_LINK_SPEED_MBPS * 1_000_000;

#[cfg(any(unix, test))]
fn has_minimum_link_speed(speed_mbps: u64) -> bool {
    speed_mbps >= MIN_DEDICATED_LINK_SPEED_MBPS
}

#[cfg(any(target_os = "windows", test))]
fn windows_speed_is_eligible(speed_bits_per_second: u64) -> bool {
    speed_bits_per_second >= MIN_DEDICATED_LINK_SPEED_BPS
}

pub fn command(program: &str, args: &[&str]) -> Result<String> {
    #[cfg(unix)]
    let executable = ["/usr/sbin", "/usr/bin", "/sbin", "/bin"]
        .iter()
        .map(|p| format!("{p}/{program}"))
        .find(|p| std::path::Path::new(p).is_file())
        .ok_or_else(|| refuse("Required system networking tool is unavailable"))?;
    #[cfg(windows)]
    let executable = format!(r"C:\Windows\System32\{program}");
    let mut child = Command::new(executable)
        .args(args)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("SystemRoot", r"C:\Windows")
        .env("SYSTEMD_COLORS", "0")
        .env("LC_ALL", "C")
        .env("LANG", "C")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let mut stdout = child.stdout.take().unwrap();
    let reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout
            .by_ref()
            .take(65537)
            .read_to_end(&mut bytes)
            .map(|_| bytes)
    });
    let deadline = Instant::now() + Duration::from_secs(5);
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            let _ = reader.join();
            return Err(refuse("System networking operation timed out"));
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let output = reader
        .join()
        .map_err(|_| refuse("System output unavailable"))??;
    if !status.success() || output.len() > 65536 {
        return Err(refuse("System networking operation failed"));
    }
    Ok(String::from_utf8(output)?)
}
fn valid_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 32
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "_.:-".contains(c))
}
fn private(ip: Ipv4Addr) -> bool {
    ip.is_private() && !ip.is_unspecified() && !ip.is_loopback()
}
#[cfg(target_os = "macos")]
fn mask(prefix: u8) -> String {
    Ipv4Addr::from(u32::MAX << (32 - prefix)).to_string()
}
#[cfg(target_os = "macos")]
fn service(scope: &Scope) -> Result<String> {
    let list = command("networksetup", &["-listnetworkserviceorder"])?;
    let mut name = String::new();
    for line in list.lines() {
        if let Some((_, rest)) = line.strip_prefix('(').and_then(|s| s.split_once(") ")) {
            name = rest.to_owned();
        }
        if line.contains(&format!("Device: {})", scope.adapter)) {
            return Ok(name);
        }
    }
    Err(refuse("Ethernet adapter has no macOS network service"))
}
#[cfg(windows)]
pub(super) fn powershell(scope: &Scope, script: &str) -> Result<String> {
    // Interface IDs are decimal and addresses are validated literals. All script
    // text is fixed here, including the operation and property names.
    if !scope.adapter.chars().all(|c| c.is_ascii_digit()) {
        return Err(refuse("Invalid interface index"));
    }
    let text = format!(
        "$ErrorActionPreference='Stop'; $i=[int]{}; $ip='{}'; $prefix={}; {}",
        scope.adapter, scope.address, scope.prefix, script
    );
    command(
        r"WindowsPowerShell\v1.0\powershell.exe",
        &["-NoProfile", "-NonInteractive", "-Command", &text],
    )
}

pub fn identity(scope: &Scope) -> Result<()> {
    if !valid_name(&scope.adapter)
        || scope.index == 0
        || !private(scope.address.parse()?)
        || !(8..=30).contains(&scope.prefix)
    {
        return Err(refuse("Invalid dedicated assignment"));
    }
    let ip: Ipv4Addr = scope.address.parse()?;
    let bits = u32::from(ip);
    let host = bits & (u32::MAX >> scope.prefix);
    if host == 0 || host == u32::MAX >> scope.prefix {
        return Err(refuse("Network and broadcast addresses are forbidden"));
    }
    #[cfg(target_os = "linux")]
    {
        let mac = std::fs::read_to_string(format!("/sys/class/net/{}/address", scope.adapter))?;
        let index: u32 =
            std::fs::read_to_string(format!("/sys/class/net/{}/ifindex", scope.adapter))?
                .trim()
                .parse()?;
        if mac.trim().to_lowercase() != scope.mac || index != scope.index {
            return Err(refuse("Adapter identity changed"));
        }
    }
    #[cfg(target_os = "macos")]
    {
        use std::ffi::CString;
        let name = CString::new(scope.adapter.as_str())?;
        if unsafe { libc::if_nametoindex(name.as_ptr()) } != scope.index {
            return Err(refuse("Adapter identity changed"));
        }
        let output = command("ifconfig", &[&scope.adapter])?;
        if !output
            .to_lowercase()
            .contains(&format!("ether {}", scope.mac))
        {
            return Err(refuse("Adapter MAC changed"));
        }
    }
    #[cfg(windows)]
    {
        let output = powershell(
            scope,
            "$a=Get-NetAdapter -InterfaceIndex $i; ($a.MacAddress -replace '-',':').ToLower()",
        )?;
        if output.trim() != scope.mac {
            return Err(refuse("Adapter identity changed"));
        }
    }
    Ok(())
}
pub fn validate(scope: &Scope) -> Result<()> {
    identity(scope)?;
    #[cfg(target_os = "macos")]
    {
        let name = service(scope)?;
        let output = command("ifconfig", &[&scope.adapter])?;
        if !output.contains("status: active")
            || name.to_lowercase().contains("wi-fi")
            || name.to_lowercase().contains("bridge")
        {
            return Err(refuse("Select connected dedicated Ethernet"));
        }
        let speed = Regex::new(r"(?i)(\d+)(Gbase|base)")?
            .captures(&output)
            .map(|c| {
                c[1].parse::<u32>().unwrap_or(0)
                    * if c[2].eq_ignore_ascii_case("Gbase") {
                        1000
                    } else {
                        1
                    }
            })
            .unwrap_or(0);
        if !has_minimum_link_speed(u64::from(speed)) {
            return Err(refuse(
                "A verified Ethernet connection of at least 1 Gbps is required",
            ));
        }
        let default = command("route", &["-n", "get", "default"])?;
        if default
            .lines()
            .any(|line| line.trim() == format!("interface: {}", scope.adapter))
        {
            return Err(refuse("This adapter carries the internet connection"));
        }
        for ip in &scope.management {
            if output.contains(&format!("inet {ip} ")) {
                return Err(refuse("This adapter carries cluster management"));
            }
        }
    }
    #[cfg(target_os = "linux")]
    {
        let dir = format!("/sys/class/net/{}", scope.adapter);
        if !std::path::Path::new(&format!("{dir}/device")).exists()
            || std::path::Path::new(&format!("{dir}/wireless")).exists()
            || std::fs::read_to_string(format!("{dir}/operstate"))?.trim() != "up"
            || !has_minimum_link_speed(
                std::fs::read_to_string(format!("{dir}/speed"))?
                    .trim()
                    .parse::<u64>()?,
            )
        {
            return Err(refuse(
                "Connected physical Ethernet at 1 Gbps or faster is required",
            ));
        }
        let routes: Value = serde_json::from_str(&command(
            "ip",
            &["-j", "-4", "route", "show", "table", "all"],
        )?)?;
        if routes
            .as_array()
            .ok_or_else(|| refuse("Invalid routes"))?
            .iter()
            .any(|r| r["dev"] == scope.adapter && r["dst"] == "default")
        {
            return Err(refuse("This adapter carries the internet connection"));
        }
        let addresses = command(
            "ip",
            &["-o", "-4", "address", "show", "dev", &scope.adapter],
        )?;
        for ip in &scope.management {
            if addresses.contains(&format!("inet {ip}/")) {
                return Err(refuse("This adapter carries cluster management"));
            }
        }
    }
    #[cfg(windows)]
    {
        let data: Value = serde_json::from_str(&powershell(scope, "$a=Get-NetAdapter -InterfaceIndex $i; [pscustomobject]@{Hardware=$a.HardwareInterface; Description=$a.InterfaceDescription; Speed=[long]$a.LinkSpeed; Up=($a.Status -eq 'Up'); Default=@(Get-NetRoute -InterfaceIndex $i -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue).Count; Addresses=@(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress)} | ConvertTo-Json -Compress")?)?;
        if data["Hardware"] != true
            || data["Up"] != true
            || !windows_speed_is_eligible(data["Speed"].as_u64().unwrap_or(0))
            || data["Default"].as_u64().unwrap_or(1) != 0
            || data["Description"]
                .as_str()
                .unwrap_or("")
                .to_lowercase()
                .contains("wireless")
        {
            return Err(refuse(
                "A dedicated physical Ethernet adapter at 1 Gbps or faster is required",
            ));
        }
        for ip in &scope.management {
            if data["Addresses"]
                .as_array()
                .is_some_and(|a| a.iter().any(|v| v == ip))
            {
                return Err(refuse("This adapter carries cluster management"));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod speed_tests {
    use super::{has_minimum_link_speed, windows_speed_is_eligible};

    #[test]
    fn managed_network_requires_known_gigabit_speed() {
        assert!(!has_minimum_link_speed(0));
        assert!(!has_minimum_link_speed(999));
        assert!(has_minimum_link_speed(1_000));
        assert!(has_minimum_link_speed(10_000));
        assert!(!windows_speed_is_eligible(999_999_999));
        assert!(windows_speed_is_eligible(1_000_000_000));
    }
}

pub fn configuration(scope: &Scope) -> Result<Value> {
    #[cfg(target_os = "macos")]
    {
        let name = service(scope)?;
        let info = command("networksetup", &["-getinfo", &name])?;
        let dhcp = info.contains("DHCP Configuration");
        if !dhcp && !info.contains("Manual Configuration") && !info.to_lowercase().contains("off") {
            return Err(refuse("DHCP configuration is unknown"));
        }
        return Ok(json!({"service":name,"dhcp":dhcp}));
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(uuid) = command(
            "nmcli",
            &["-g", "GENERAL.CON-UUID", "device", "show", &scope.adapter],
        ) {
            let uuid = uuid.trim();
            if uuid.len() == 36 && uuid.chars().all(|c| c.is_ascii_hexdigit() || c == '-') {
                let method = command(
                    "nmcli",
                    &["-g", "ipv4.method", "connection", "show", "uuid", uuid],
                )?
                .trim()
                .to_owned();
                if !matches!(
                    method.as_str(),
                    "auto" | "manual" | "disabled" | "link-local"
                ) {
                    return Err(refuse("Unsupported DHCP configuration"));
                }
                if method == "auto"
                    && !command(
                        "nmcli",
                        &[
                            "-g",
                            "ipv4.addresses,ipv4.routes",
                            "connection",
                            "show",
                            "uuid",
                            uuid,
                        ],
                    )?
                    .trim()
                    .replace(':', "")
                    .is_empty()
                {
                    return Err(refuse(
                        "DHCP with custom static settings requires manual review",
                    ));
                }
                return Ok(
                    json!({"manager":"NetworkManager","uuid":uuid,"method":method,"temporary":format!("kllm-{}",scope.network),"temporary_uuid":scope.network}),
                );
            }
        }
        let status = command("networkctl", &["status", "--no-pager", &scope.adapter])?;
        let file = status
            .lines()
            .find_map(|l| l.trim().strip_prefix("Network File: "))
            .and_then(|p| std::path::Path::new(p).file_name())
            .and_then(|s| s.to_str())
            .ok_or_else(|| refuse("Supported network manager is required"))?;
        if !file.ends_with(".network") || file.contains('/') || file.contains("..") {
            return Err(refuse("Unsupported networkd profile"));
        }
        let output = command("networkctl", &["cat", "--no-pager", &scope.adapter])?;
        let mut section = "";
        let mut method = "no";
        for line in output.lines().map(str::trim) {
            if line.starts_with('[') {
                section = line;
            }
            if section == "[Network]" {
                if let Some((key, value)) = line.split_once('=') {
                    if key.trim() == "DHCP" {
                        method = value.trim();
                    }
                }
            }
        }
        if !matches!(method, "yes" | "true" | "ipv4" | "ipv6" | "no" | "false") {
            return Err(refuse("Unsupported networkd DHCP configuration"));
        }
        return Ok(
            json!({"manager":"networkd","file":file,"dhcp":matches!(method,"yes"|"true"|"ipv4"),"override":if matches!(method,"yes"|"true") {"[Network]\nDHCP=ipv6\n"} else {"[Network]\nDHCP=no\n"}}),
        );
    }
    #[cfg(windows)]
    {
        let result = powershell(scope,"(Get-NetIPInterface -InterfaceIndex $i -AddressFamily IPv4 -PolicyStore ActiveStore).Dhcp.ToString()")?;
        if !matches!(result.trim(), "Enabled" | "Disabled") {
            return Err(refuse("DHCP configuration is unknown"));
        }
        return Ok(json!({"dhcp":result.trim()=="Enabled"}));
    }
    #[allow(unreachable_code)]
    Err(refuse("Unsupported operating system"))
}
#[cfg(unix)]
fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value[key]
        .as_str()
        .ok_or_else(|| refuse("Configuration journal is incomplete"))
}
pub fn disable_dhcp(scope: &mut Scope) -> Result<()> {
    #[cfg(target_os = "macos")]
    if scope.original["dhcp"] == true {
        command(
            "networksetup",
            &["-setv4off", text(&scope.original, "service")?],
        )?;
    }
    #[cfg(target_os = "linux")]
    {
        if scope.original["manager"] == "NetworkManager" && scope.original["method"] == "auto" {
            // Do not touch a profile merely because its display name matches.
            if command(
                "nmcli",
                &[
                    "connection",
                    "show",
                    "uuid",
                    text(&scope.original, "temporary_uuid")?,
                ],
            )
            .is_ok()
            {
                return Err(refuse("Temporary profile identity already exists"));
            }
            command(
                "nmcli",
                &[
                    "connection",
                    "clone",
                    "--temporary",
                    "uuid",
                    text(&scope.original, "uuid")?,
                    text(&scope.original, "temporary")?,
                ],
            )?;
            let clone = command(
                "nmcli",
                &[
                    "-g",
                    "connection.uuid",
                    "connection",
                    "show",
                    "id",
                    text(&scope.original, "temporary")?,
                ],
            )?;
            if clone.lines().count() != 1 || !super::valid_uuid(clone.trim()) {
                return Err(refuse("Temporary profile is ambiguous"));
            }
            command(
                "nmcli",
                &[
                    "connection",
                    "modify",
                    "--temporary",
                    "uuid",
                    clone.trim(),
                    "connection.uuid",
                    text(&scope.original, "temporary_uuid")?,
                ],
            )?;
            command(
                "nmcli",
                &[
                    "connection",
                    "modify",
                    "--temporary",
                    "uuid",
                    text(&scope.original, "temporary_uuid")?,
                    "ipv4.method",
                    "disabled",
                    "connection.autoconnect",
                    "no",
                ],
            )?;
            command(
                "nmcli",
                &[
                    "connection",
                    "up",
                    "uuid",
                    text(&scope.original, "temporary_uuid")?,
                    "ifname",
                    &scope.adapter,
                ],
            )?;
        } else if scope.original["manager"] == "networkd" && scope.original["dhcp"] == true {
            let directory = format!("/run/systemd/network/{}.d", text(&scope.original, "file")?);
            std::fs::create_dir_all(&directory)?;
            let path = format!("{directory}/99-kllm-{}.conf", scope.network);
            if std::path::Path::new(&path).exists() {
                return Err(refuse("Existing network configuration must be reviewed"));
            }
            std::fs::write(path, text(&scope.original, "override")?)?;
            command("networkctl", &["reload"])?;
            command("networkctl", &["reconfigure", &scope.adapter])?;
        }
    }
    #[cfg(windows)]
    {
        if scope.original["dhcp"] == true {
            powershell(scope,"Set-NetIPInterface -InterfaceIndex $i -AddressFamily IPv4 -PolicyStore ActiveStore -Dhcp Disabled | Out-Null")?;
        }
        let name = format!("kllm-private-{}-{}", scope.network, scope.index);
        let subnet = scope.address.parse::<Ipv4Addr>()?;
        for peer in &scope.peers {
            let ip: Ipv4Addr = peer.parse()?;
            if !private(ip)
                || u32::from(ip) >> (32 - scope.prefix) != u32::from(subnet) >> (32 - scope.prefix)
            {
                return Err(refuse("Firewall peers must belong to the dedicated subnet"));
            }
        }
        if scope.peers.is_empty() {
            return Err(refuse("Missing private peers"));
        }
        let addresses = scope
            .peers
            .iter()
            .map(|p| format!("'{p}'"))
            .collect::<Vec<_>>()
            .join(",");
        let ports = scope
            .ports
            .iter()
            .map(u16::to_string)
            .collect::<Vec<_>>()
            .join(",");
        powershell(scope,&format!("if (Get-NetFirewallRule -Name '{name}' -ErrorAction SilentlyContinue) {{throw 'Firewall rule already exists'}}; $a=Get-NetAdapter -InterfaceIndex $i; New-NetFirewallRule -Name '{name}' -DisplayName 'Kluster private network' -Direction Inbound -Action Allow -Enabled True -Protocol TCP -LocalAddress $ip -RemoteAddress @({addresses}) -LocalPort @({ports}) -InterfaceAlias $a.Name -Profile Any -PolicyStore PersistentStore | Out-Null"))?;
        scope.original["firewall"] = firewall_snapshot(scope)?;
    }
    Ok(())
}
pub fn restore(scope: &mut Scope) -> Result<()> {
    if !scope.configured {
        return Ok(());
    }
    identity(scope)?;
    #[cfg(target_os = "macos")]
    if scope.original["dhcp"] == true {
        let current = command(
            "networksetup",
            &["-getinfo", text(&scope.original, "service")?],
        )?;
        if !current.contains("DHCP Configuration") {
            if !current.to_lowercase().contains("off") {
                return Err(refuse("DHCP settings changed externally; review required"));
            }
            command(
                "networksetup",
                &["-setdhcp", text(&scope.original, "service")?],
            )?;
        }
    }
    #[cfg(target_os = "linux")]
    {
        if scope.original["manager"] == "NetworkManager" && scope.original["method"] == "auto" {
            let current = command(
                "nmcli",
                &["-g", "GENERAL.CON-UUID", "device", "show", &scope.adapter],
            )?;
            if current.trim() != text(&scope.original, "temporary_uuid")?
                && current.trim() != text(&scope.original, "uuid")?
                && !matches!(current.trim(), "" | "--")
            {
                return Err(refuse("Active profile changed externally; review required"));
            }
            let method = command(
                "nmcli",
                &[
                    "-g",
                    "ipv4.method",
                    "connection",
                    "show",
                    "uuid",
                    text(&scope.original, "uuid")?,
                ],
            )?;
            if method.trim() != "auto" {
                return Err(refuse("Saved profile changed externally; review required"));
            }
            command(
                "nmcli",
                &[
                    "connection",
                    "up",
                    "uuid",
                    text(&scope.original, "uuid")?,
                    "ifname",
                    &scope.adapter,
                ],
            )?;
            if command(
                "nmcli",
                &[
                    "connection",
                    "show",
                    "uuid",
                    text(&scope.original, "temporary_uuid")?,
                ],
            )
            .is_ok()
            {
                command(
                    "nmcli",
                    &[
                        "connection",
                        "delete",
                        "uuid",
                        text(&scope.original, "temporary_uuid")?,
                    ],
                )?;
            }
        } else if scope.original["manager"] == "networkd" && scope.original["dhcp"] == true {
            let path = format!(
                "/run/systemd/network/{}.d/99-kllm-{}.conf",
                text(&scope.original, "file")?,
                scope.network
            );
            if std::path::Path::new(&path).exists() {
                if std::fs::read_to_string(&path)? != text(&scope.original, "override")? {
                    return Err(refuse(
                        "Runtime settings changed externally; review required",
                    ));
                }
                std::fs::remove_file(path)?;
                command("networkctl", &["reload"])?;
                command("networkctl", &["reconfigure", &scope.adapter])?;
            }
        }
    }
    #[cfg(windows)]
    if scope.original["dhcp"] == true {
        let current = configuration(scope)?;
        if current["dhcp"] == false {
            powershell(scope,"Set-NetIPInterface -InterfaceIndex $i -AddressFamily IPv4 -PolicyStore ActiveStore -Dhcp Enabled | Out-Null")?;
        }
    }
    #[cfg(windows)]
    {
        let name = format!("kllm-private-{}-{}", scope.network, scope.index);
        let current = firewall_snapshot(scope)?;
        if current != Value::Null {
            if scope.original["firewall"].is_null() || current != scope.original["firewall"] {
                return Err(refuse(
                    "Firewall settings changed or ownership is uncertain; review required",
                ));
            }
            powershell(scope,&format!("Get-NetFirewallRule -Name '{name}' -PolicyStore PersistentStore | Remove-NetFirewallRule"))?;
        }
    }
    scope.configured = false;
    Ok(())
}
pub fn has_address(scope: &Scope) -> Result<bool> {
    identity(scope)?;
    #[cfg(target_os = "macos")]
    return Ok(
        command("ifconfig", &[&scope.adapter])?.contains(&format!("inet {} ", scope.address))
    );
    #[cfg(target_os = "linux")]
    return Ok(command(
        "ip",
        &["-o", "-4", "address", "show", "dev", &scope.adapter],
    )?
    .contains(&format!("inet {}/", scope.address)));
    #[cfg(windows)] return Ok(powershell(scope,"@(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 | Where-Object {$_.IPAddress -eq $ip}).Count")?.trim() != "0");
    #[allow(unreachable_code)]
    Err(refuse("Unsupported operating system"))
}
pub fn add(scope: &Scope) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        command(
            "ifconfig",
            &[
                &scope.adapter,
                "inet",
                &scope.address,
                "netmask",
                &mask(scope.prefix),
                "alias",
            ],
        )?;
    }
    #[cfg(target_os = "linux")]
    {
        command(
            "ip",
            &[
                "-4",
                "address",
                "add",
                &format!("{}/{}", scope.address, scope.prefix),
                "dev",
                &scope.adapter,
            ],
        )?;
    }
    #[cfg(windows)]
    {
        powershell(scope,"New-NetIPAddress -InterfaceIndex $i -IPAddress $ip -PrefixLength $prefix -PolicyStore ActiveStore -SkipAsSource $true | Out-Null")?;
    }
    Ok(())
}
pub fn delete(scope: &Scope) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        let output = command("ifconfig", &[&scope.adapter])?;
        let pattern = format!(
            r"\binet {} netmask (0x[0-9a-f]+|[0-9.]+)",
            regex::escape(&scope.address)
        );
        let matches = Regex::new(&pattern)?
            .captures(&output)
            .ok_or_else(|| refuse("Exact address disappeared"))?;
        let found = if matches[1].starts_with("0x") {
            Ipv4Addr::from(u32::from_str_radix(&matches[1][2..], 16)?).to_string()
        } else {
            matches[1].to_owned()
        };
        if found != mask(scope.prefix) {
            return Err(refuse("Address prefix changed; keep the current settings"));
        }
    }
    #[cfg(target_os = "macos")]
    {
        command(
            "ifconfig",
            &[&scope.adapter, "inet", &scope.address, "-alias"],
        )?;
    }
    #[cfg(target_os = "linux")]
    {
        command(
            "ip",
            &[
                "-4",
                "address",
                "delete",
                &format!("{}/{}", scope.address, scope.prefix),
                "dev",
                &scope.adapter,
            ],
        )?;
    }
    #[cfg(windows)]
    {
        powershell(scope,"Get-NetIPAddress -InterfaceIndex $i -IPAddress $ip -PrefixLength $prefix -PolicyStore ActiveStore | Where-Object {$_.SkipAsSource} | Remove-NetIPAddress -Confirm:$false")?;
    }
    Ok(())
}

pub fn review(scope: &Scope) -> Result<Value> {
    identity(scope)?;
    #[cfg(target_os = "macos")]
    let addresses = command("ifconfig", &[&scope.adapter])?;
    #[cfg(target_os = "linux")]
    let addresses = command(
        "ip",
        &["-o", "-4", "address", "show", "dev", &scope.adapter],
    )?;
    #[cfg(windows)] let addresses = powershell(scope,"@(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 | Select-Object IPAddress,PrefixLength,SkipAsSource,InstanceID) | ConvertTo-Json -Compress")?;
    #[allow(unused_mut)]
    let mut result = json!({"adapter":scope.adapter,"mac":scope.mac,"index":scope.index,"configuration":configuration(scope)?,"addresses":addresses});
    #[cfg(windows)]
    {
        result["firewall"] = firewall_snapshot(scope)?;
    }
    Ok(result)
}

#[cfg(windows)]
fn firewall_snapshot(scope: &Scope) -> Result<Value> {
    let name = format!("kllm-private-{}-{}", scope.network, scope.index);
    let output = powershell(scope,&format!("$r=Get-NetFirewallRule -Name '{name}' -PolicyStore PersistentStore -ErrorAction SilentlyContinue; if ($r) {{$p=$r|Get-NetFirewallPortFilter; $a=$r|Get-NetFirewallAddressFilter; $f=$r|Get-NetFirewallInterfaceFilter; [pscustomobject]@{{Name=$r.Name; Direction=$r.Direction.ToString(); Action=$r.Action.ToString(); Enabled=$r.Enabled.ToString(); Profile=$r.Profile.ToString(); Protocol=$p.Protocol; Ports=@($p.LocalPort); Local=@($a.LocalAddress); Remote=@($a.RemoteAddress); Interface=@($f.InterfaceAlias)}} | ConvertTo-Json -Compress}} else {{'null'}}"))?;
    Ok(serde_json::from_str(&output)?)
}
