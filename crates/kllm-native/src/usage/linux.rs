#[cfg(target_os = "linux")]
use std::fs::File;
#[cfg(target_os = "linux")]
use std::io::{BufRead, BufReader, Read};
#[cfg(target_os = "linux")]
use std::sync::OnceLock;

#[cfg(target_os = "linux")]
static CLK_TCK: OnceLock<u64> = OnceLock::new();
#[cfg(target_os = "linux")]
static BOOT_TIME_SECS: OnceLock<u64> = OnceLock::new();

#[cfg(target_os = "linux")]
fn get_clk_tck() -> u64 {
    *CLK_TCK.get_or_init(|| unsafe {
        let ticks = libc::sysconf(libc::_SC_CLK_TCK);
        if ticks > 0 {
            ticks as u64
        } else {
            100
        }
    })
}

#[cfg(target_os = "linux")]
fn get_boot_time_secs() -> u64 {
    *BOOT_TIME_SECS.get_or_init(|| {
        // Read /proc/stat for line: "btime <timestamp>"
        if let Ok(file) = File::open("/proc/stat") {
            let reader = BufReader::new(file);
            for line in reader.lines().map_while(Result::ok) {
                if let Some(rest) = line.strip_prefix("btime ") {
                    if let Ok(ts) = rest.trim().parse::<u64>() {
                        return ts;
                    }
                }
            }
        }
        // Fallback: system time minus uptime
        let mut sysinfo_data: libc::sysinfo = unsafe { std::mem::zeroed() };
        if unsafe { libc::sysinfo(&mut sysinfo_data) } == 0 {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            return now.saturating_sub(sysinfo_data.uptime as u64);
        }
        0
    })
}

/// Pure parser for /proc/[pid]/stat content.
///
/// Returns `(token, started_rfc3339, cpu_ns)`.
pub fn parse_stat_content(
    stat_content: &str,
    clk_tck: u64,
    boot_time: u64,
) -> Result<(String, String, u64), String> {
    // The process comm field is enclosed in parens: "pid (comm) state ..."
    // Comm can contain spaces or parens, so locate the last ')' to parse remaining fields reliably.
    let close_paren = stat_content.rfind(')').ok_or("invalid_counter")?;
    let rest = stat_content[close_paren + 1..].trim_start();
    let fields: Vec<&str> = rest.split_whitespace().collect();

    // Field indices relative to post-paren:
    // field 0: state (field 3 in man 5 proc)
    // field 1: ppid (field 4)
    // ...
    // field 11: utime (field 14 in man 5 proc)
    // field 12: stime (field 15 in man 5 proc)
    // ...
    // field 19: starttime (field 22 in man 5 proc, ticks after system boot)
    if fields.len() < 20 {
        return Err("invalid_counter".into());
    }

    if fields[0] == "Z" || fields[0] == "X" {
        return Err("process_exited".into());
    }

    let utime: u64 = fields[11].parse().map_err(|_| "invalid_counter")?;
    let stime: u64 = fields[12].parse().map_err(|_| "invalid_counter")?;
    let starttime_ticks: u64 = fields[19].parse().map_err(|_| "invalid_counter")?;

    let clk_tck = if clk_tck > 0 { clk_tck } else { 100 };
    let start_offset_secs = starttime_ticks / clk_tck;
    let start_offset_rem_ticks = starttime_ticks % clk_tck;
    let start_epoch_secs = boot_time + start_offset_secs;
    let start_epoch_nanos =
        (start_offset_rem_ticks as u128 * 1_000_000_000 / clk_tck as u128) as u32;

    let token = format!("{}_{}", start_epoch_secs, starttime_ticks);
    let started = chrono::DateTime::from_timestamp(start_epoch_secs as i64, start_epoch_nanos)
        .ok_or("invalid_counter")?
        .to_rfc3339_opts(chrono::SecondsFormat::Micros, true);

    let total_ticks = utime.saturating_add(stime);
    let cpu_ns = (total_ticks as u128 * 1_000_000_000 / clk_tck as u128) as u64;

    Ok((token, started, cpu_ns))
}

/// Pure parser for /proc/[pid]/statm content.
///
/// Returns resident memory in bytes given the system page size.
pub fn parse_statm_content(statm_content: &str, page_size: u64) -> Option<u64> {
    let parts: Vec<&str> = statm_content.split_whitespace().collect();
    if parts.len() >= 2 {
        if let Ok(pages) = parts[1].parse::<u64>() {
            let page_size = if page_size > 0 { page_size } else { 4096 };
            return Some(pages.saturating_mul(page_size));
        }
    }
    None
}

/// Pure parser for /proc/[pid]/status content looking for VmRSS.
///
/// Returns resident memory in bytes.
pub fn parse_status_rss_content(status_content: &str) -> Option<u64> {
    for line in status_content.lines() {
        if let Some(rest) = line.strip_prefix("VmRSS:") {
            let parts: Vec<&str> = rest.split_whitespace().collect();
            if let Some(kb_str) = parts.first() {
                if let Ok(kb) = kb_str.parse::<u64>() {
                    return Some(kb.saturating_mul(1024));
                }
            }
        }
    }
    None
}

#[cfg(target_os = "linux")]
pub fn process(pid: u32) -> Result<super::ProcessCounters, String> {
    let stat_path = format!("/proc/{pid}/stat");
    let mut stat_content = String::with_capacity(512);
    File::open(&stat_path)
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                "process_exited"
            } else {
                "permission_denied"
            }
        })?
        .read_to_string(&mut stat_content)
        .map_err(|_| "invalid_counter")?;

    let clk_tck = get_clk_tck();
    let boot_time = get_boot_time_secs();
    let (token, started, cpu_ns) = parse_stat_content(&stat_content, clk_tck, boot_time)?;

    let mut rss: Option<u64> = None;
    if let Ok(statm_file) = File::open(format!("/proc/{pid}/statm")) {
        let mut statm_content = String::with_capacity(64);
        if BufReader::new(statm_file).read_line(&mut statm_content).is_ok() {
            let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
            let page_size = if page_size > 0 { page_size as u64 } else { 4096 };
            rss = parse_statm_content(&statm_content, page_size);
        }
    }

    if rss.is_none() {
        if let Ok(mut status_file) = File::open(format!("/proc/{pid}/status")) {
            let mut status_content = String::with_capacity(2048);
            if status_file.read_to_string(&mut status_content).is_ok() {
                rss = parse_status_rss_content(&status_content);
            }
        }
    }

    Ok(super::ProcessCounters {
        token,
        started,
        cpu_ns,
        rss,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_stat_standard_process() {
        let stat = "1234 (my_server) S 1 1234 1234 0 -1 4194304 1000 0 0 0 150 50 0 0 20 0 4 0 5000 1234567 100 18446744073709551615";
        let clk_tck = 100;
        let boot_time = 1700000000;
        let (token, started, cpu_ns) = parse_stat_content(stat, clk_tck, boot_time).unwrap();
        assert_eq!(token, "1700000050_5000");
        assert_eq!(started, "2023-11-14T22:14:10.000000Z");
        assert_eq!(cpu_ns, 2_000_000_000);
    }

    #[test]
    fn parse_stat_comm_with_parentheses_and_spaces() {
        let stat = "4321 (worker (kllm) rank:0) R 1 4321 4321 0 -1 4194304 500 0 0 0 300 100 0 0 20 0 2 0 10000 2345678 200 18446744073709551615";
        let clk_tck = 100;
        let boot_time = 1700000000;
        let (token, started, cpu_ns) = parse_stat_content(stat, clk_tck, boot_time).unwrap();
        assert_eq!(token, "1700000100_10000");
        assert!(started.ends_with('Z'));
        assert_eq!(cpu_ns, 4_000_000_000);
    }

    #[test]
    fn parse_stat_zombie_and_dead_states_return_process_exited() {
        let stat_zombie = "1234 (zombie_proc) Z 1 1234 1234 0 -1 0 0 0 0 0 10 5 0 0 20 0 1 0 500 0 0 0";
        assert_eq!(
            parse_stat_content(stat_zombie, 100, 1700000000).unwrap_err(),
            "process_exited"
        );

        let stat_dead = "1234 (dead_proc) X 1 1234 1234 0 -1 0 0 0 0 0 10 5 0 0 20 0 1 0 500 0 0 0";
        assert_eq!(
            parse_stat_content(stat_dead, 100, 1700000000).unwrap_err(),
            "process_exited"
        );
    }

    #[test]
    fn parse_stat_truncated_and_corrupt() {
        assert_eq!(
            parse_stat_content("1234 (no_paren", 100, 1700000000).unwrap_err(),
            "invalid_counter"
        );
        assert_eq!(
            parse_stat_content("1234 (proc) S 1 2 3", 100, 1700000000).unwrap_err(),
            "invalid_counter"
        );
    }

    #[test]
    fn parse_statm_calculates_bytes_from_pages() {
        let statm = "25000 12500 3000 100 0 500 0";
        let page_size = 4096;
        let rss = parse_statm_content(statm, page_size);
        assert_eq!(rss, Some(12500 * 4096));

        assert_eq!(parse_statm_content("", 4096), None);
        assert_eq!(parse_statm_content("25000", 4096), None);
    }

    #[test]
    fn parse_status_rss_extracts_vmrss() {
        let status = "Name:\tl_server\nState:\tS (sleeping)\nVmPeak:\t  500000 kB\nVmSize:\t  450000 kB\nVmRSS:\t   81920 kB\nRssAnon:\t   75000 kB\n";
        let rss = parse_status_rss_content(status);
        assert_eq!(rss, Some(81920 * 1024));

        let status_no_rss = "Name:\tkllm\nState:\tS\n";
        assert_eq!(parse_status_rss_content(status_no_rss), None);
    }
}
