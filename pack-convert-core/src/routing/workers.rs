//! Documented thread-priority tiers for Navi (host + core workers).
//!
//! | Tier | Role | Priority |
//! |---|---|---|
//! | T0 Sensor | GPS / IMU | Highest |
//! | T1 ECU | Live energy (future) | High |
//! | T2 UI / audio peer | Compose UI, media smoothness | High |
//! | T3 Routing | Graph build, eco-reweight, A* | Medium (below audio) |
//! | T4 DB | SQLite persistence | Lowest |
//!
//! Routing workers must leave headroom for T0–T2 and must not starve audio.

use std::num::NonZeroUsize;
use std::thread::available_parallelism;

/// Fraction of detected cores for routing / server convert work.
const ROUTING_CORE_FRACTION: f64 = 0.70;

/// Documented tablet-safe tile concurrency when physical RAM looks small.
const TABLET_TILE_BUILD_CAP: usize = 2;

/// Detected parallelism and the worker count to use for routing-tier Rayon pools.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkerPoolPlan {
    pub detected_cores: usize,
    pub routing_workers: usize,
    pub reserved_for_ui_audio: usize,
}

impl WorkerPoolPlan {
    /// Autodetect cores and reserve ~30% headroom (use ~70% for routing).
    pub fn detect() -> Self {
        let detected = available_parallelism()
            .map(NonZeroUsize::get)
            .unwrap_or(1)
            .max(1);
        let routing_workers = ((detected as f64) * ROUTING_CORE_FRACTION)
            .round()
            .max(1.0) as usize;
        let routing_workers = routing_workers.clamp(1, detected);
        let reserved = detected.saturating_sub(routing_workers).max(if detected > 1 {
            1
        } else {
            0
        });
        // Recompute so reserved + workers == detected when possible.
        let routing_workers = detected.saturating_sub(reserved).max(1);
        Self {
            detected_cores: detected,
            routing_workers,
            reserved_for_ui_audio: reserved.min(detected),
        }
    }

    /// How many spatial graph tiles to build+pack concurrently.
    ///
    /// - Defaults to [`Self::routing_workers`] (~70% of detected cores).
    /// - Caps at [`TABLET_TILE_BUILD_CAP`] when usable RAM looks ≤ 6 GiB (on-device).
    /// - Override with `NAVI_TILE_BUILD_CONCURRENCY` (positive integer).
    pub fn tile_build_concurrency(&self) -> usize {
        if let Ok(raw) = std::env::var("NAVI_TILE_BUILD_CONCURRENCY") {
            if let Ok(n) = raw.trim().parse::<usize>() {
                return n.max(1);
            }
        }
        let by_cores = self.routing_workers.max(1);
        let mem_gib = usable_ram_gib();
        let by_mem = if mem_gib <= 6.0 {
            TABLET_TILE_BUILD_CAP
        } else {
            // Rough headroom: ~1.5 GiB RSS per concurrent tile on large hosts.
            ((mem_gib / 1.5).floor() as usize).max(TABLET_TILE_BUILD_CAP)
        };
        by_cores.min(by_mem).min(self.detected_cores).max(1)
    }

    /// Install as the global Rayon thread-pool size for routing-tier work.
    pub fn install_rayon_pool(&self) -> Result<(), rayon::ThreadPoolBuildError> {
        rayon::ThreadPoolBuilder::new()
            .num_threads(self.routing_workers)
            .thread_name(|i| format!("navi-routing-{i}"))
            .build_global()
    }

    /// Best-effort lower OS niceness for the current thread (routing tier).
    ///
    /// No-op / ignored on platforms without `libc` nice, or when lacking permission.
    pub fn lower_current_thread_priority() {
        #[cfg(unix)]
        {
            // SAFETY: nice() only affects the calling thread/process priority.
            unsafe {
                let _ = libc::nice(5);
            }
        }
    }
}

fn usable_ram_gib() -> f64 {
    #[cfg(target_os = "linux")]
    {
        if let Ok(text) = std::fs::read_to_string("/proc/meminfo") {
            // Prefer MemAvailable; fall back to MemTotal.
            let mut total_kb = None;
            let mut avail_kb = None;
            for line in text.lines() {
                if let Some(rest) = line.strip_prefix("MemTotal:") {
                    total_kb = rest.split_whitespace().next().and_then(|s| s.parse().ok());
                } else if let Some(rest) = line.strip_prefix("MemAvailable:") {
                    avail_kb = rest.split_whitespace().next().and_then(|s| s.parse().ok());
                }
            }
            let kb = avail_kb.or(total_kb).unwrap_or(0_u64);
            return (kb as f64) / (1024.0 * 1024.0);
        }
    }
    // Unknown platforms: treat as large enough that core fraction decides.
    64.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn leaves_headroom() {
        let plan = WorkerPoolPlan::detect();
        assert!(plan.detected_cores >= 1);
        assert!(plan.routing_workers >= 1);
        assert!(plan.routing_workers <= plan.detected_cores);
        if plan.detected_cores > 1 {
            assert!(plan.routing_workers < plan.detected_cores);
        }
    }

    #[test]
    fn tile_build_concurrency_at_least_one() {
        let plan = WorkerPoolPlan::detect();
        let n = plan.tile_build_concurrency();
        assert!(n >= 1);
        assert!(n <= plan.detected_cores);
    }

    #[test]
    fn routing_workers_near_seventy_percent() {
        let plan = WorkerPoolPlan::detect();
        if plan.detected_cores >= 4 {
            let expected = ((plan.detected_cores as f64) * ROUTING_CORE_FRACTION).round() as usize;
            // Allow ±1 for the reserved-core clamp.
            assert!((plan.routing_workers as i32 - expected as i32).abs() <= 1);
        }
    }
}
