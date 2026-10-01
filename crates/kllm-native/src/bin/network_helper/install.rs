use super::{host::command, refuse, Grant, Result};
use std::{fs, path::Path};
pub fn require_admin() -> Result<()> {
    #[cfg(unix)]
    if unsafe { libc::geteuid() } != 0 {
        return Err(refuse("Administrator authorization is required"));
    }
    #[cfg(windows)]
    {
        let output = command(r"WindowsPowerShell\v1.0\powershell.exe",&["-NoProfile","-NonInteractive","-Command","([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"])?;
        if output.trim() != "True" {
            return Err(refuse("Administrator authorization is required"));
        }
    }
    Ok(())
}
fn protected_directory(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        // Every ancestor is root owned and cannot redirect the installed helper.
        let mut ancestors: Vec<_> = path.ancestors().collect();
        ancestors.reverse();
        for ancestor in ancestors {
            if !ancestor.exists() {
                fs::create_dir(ancestor)?;
            }
            let metadata = fs::symlink_metadata(ancestor)?;
            if !metadata.is_dir()
                || metadata.file_type().is_symlink()
                || metadata.uid() != 0
                || metadata.mode() & 0o022 != 0
            {
                return Err(refuse(
                    "Helper installation directory must be protected and root owned",
                ));
            }
        }
    }
    #[cfg(windows)]
    {
        fs::create_dir_all(path)?;
        if fs::symlink_metadata(path)?.file_type().is_symlink() {
            return Err(refuse("Installed path cannot be redirected"));
        }
    }
    Ok(())
}
fn copy_binary(source: &Path, target: &Path) -> Result<()> {
    if fs::symlink_metadata(target).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err(refuse("Installed binary cannot be a symlink"));
    }
    let temporary = target.with_extension("new");
    if fs::symlink_metadata(&temporary).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err(refuse("Installed path cannot be a symlink"));
    }
    fs::copy(source, &temporary)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o700))?;
    }
    fs::rename(temporary, target)?;
    Ok(())
}
fn write(path: &Path, bytes: &[u8]) -> Result<()> {
    if fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err(refuse("Installed path cannot be a symlink"));
    }
    fs::write(path, bytes)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}
fn verify_settled(config: &Path) -> Result<()> {
    let path = config.with_extension("journal.json");
    if path.exists() {
        let journal: super::Journal = serde_json::from_slice(&fs::read(path)?)?;
        if journal
            .scopes
            .values()
            .any(|s| s.configured || s.added || s.uncertain)
        {
            return Err(refuse("Restore existing private network changes before updating administrator authorization"));
        }
    }
    Ok(())
}
fn prepare_grant(config: &Path, grant: &Grant) -> Result<()> {
    let journal_path = config.with_extension("journal.json");
    if journal_path.exists() {
        let mut journal: super::Journal = serde_json::from_slice(&fs::read(&journal_path)?)?;
        if journal
            .scopes
            .values()
            .any(|s| s.configured || s.added || s.uncertain)
        {
            return Err(refuse(
                "Restore the existing private network before updating administrator authorization",
            ));
        }
        journal.revoked = false;
        // A new locally approved cluster starts a new authority namespace.
        let prior: Grant = serde_json::from_slice(&fs::read(config)?)?;
        if prior.cluster_binding_id != grant.cluster_binding_id
            || prior.master_public_key != grant.master_public_key
        {
            journal = super::Journal::default();
        }
        write(&journal_path, &serde_json::to_vec(&journal)?)?;
    }
    Ok(())
}
pub fn install(source: &Path) -> Result<()> {
    require_admin()?;
    if fs::metadata(source)?.len() > 8192 {
        return Err(refuse("Authorization file exceeds limit"));
    }
    let grant: Grant = serde_json::from_slice(&fs::read(source)?)?;
    grant.validate()?;
    let executable = std::env::current_exe()?;
    #[cfg(target_os = "macos")]
    {
        let directory = Path::new("/Library/PrivilegedHelperTools/com.kllm.network");
        protected_directory(directory)?;
        let binary = directory.join("kllm-network-helper");
        let config = directory.join(format!("{}.json", grant.account));
        verify_settled(&config)?;
        let label = format!("com.kllm.network.{}", grant.account);
        let plist = std::path::PathBuf::from(format!("/Library/LaunchDaemons/{label}.plist"));
        let _ = command("launchctl", &["bootout", &format!("system/{label}")]);
        if command("launchctl", &["print", &format!("system/{label}")]).is_ok() {
            return Err(refuse("Existing helper did not stop"));
        }
        prepare_grant(&config, &grant)?;
        copy_binary(&executable, &binary)?;
        write(&config, &serde_json::to_vec(&grant)?)?;
        let text=format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\"><plist version=\"1.0\"><dict><key>Label</key><string>{label}</string><key>ProgramArguments</key><array><string>{}</string><string>--serve</string><string>{}</string></array><key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/sbin:/usr/bin:/sbin:/bin</string></dict></dict></plist>",binary.display(),config.display());
        write(&plist, text.as_bytes())?;
        command(
            "launchctl",
            &["bootstrap", "system", plist.to_str().unwrap()],
        )?;
    }
    #[cfg(target_os = "linux")]
    {
        let directory = Path::new("/etc/kllm-network");
        protected_directory(directory)?;
        protected_directory(Path::new("/usr/local/libexec"))?;
        let binary = Path::new("/usr/local/libexec/kllm-network-helper");
        let config = directory.join(format!("{}.json", grant.account));
        verify_settled(&config)?;
        let unit = format!("kllm-network-{}.service", grant.account);
        let _ = command("systemctl", &["stop", &unit]);
        if command("systemctl", &["is-active", &unit]).is_ok() {
            return Err(refuse("Existing helper did not stop"));
        }
        prepare_grant(&config, &grant)?;
        copy_binary(&executable, binary)?;
        write(&config, &serde_json::to_vec(&grant)?)?;
        let text=format!("[Unit]\nDescription=Kluster dedicated network helper\nAfter=network.target\n[Service]\nExecStart={} --serve {}\nRestart=on-failure\nUMask=0077\nNoNewPrivileges=true\nProtectHome=true\n[Install]\nWantedBy=multi-user.target\n",binary.display(),config.display());
        write(
            Path::new(&format!("/etc/systemd/system/{unit}")),
            text.as_bytes(),
        )?;
        command("systemctl", &["daemon-reload"])?;
        command("systemctl", &["enable", "--now", &unit])?;
    }
    #[cfg(windows)]
    {
        let directory = Path::new(r"C:\Program Files\KlusterLLM\NetworkHelper");
        protected_directory(directory)?;
        command(
            "icacls.exe",
            &[
                directory.to_str().unwrap(),
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
        )?;
        let binary = directory.join("kllm-network-helper.exe");
        let config = directory.join(format!("{}.json", grant.account));
        verify_settled(&config)?;
        let task = format!("KlusterNetwork-{}", grant.account);
        let _ = command("schtasks.exe", &["/End", "/TN", &task]);
        prepare_grant(&config, &grant)?;
        copy_binary(&executable, &binary)?;
        write(&config, &serde_json::to_vec(&grant)?)?;
        let invocation = format!("\"{}\" --serve \"{}\"", binary.display(), config.display());
        command(
            "schtasks.exe",
            &[
                "/Create",
                "/F",
                "/TN",
                &task,
                "/SC",
                "ONSTART",
                "/RU",
                "SYSTEM",
                "/RL",
                "HIGHEST",
                "/TR",
                &invocation,
            ],
        )?;
        command("schtasks.exe", &["/Run", "/TN", &task])?;
    }
    Ok(())
}
