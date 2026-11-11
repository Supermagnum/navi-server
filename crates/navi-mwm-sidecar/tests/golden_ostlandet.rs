//! Golden wetland record counts from spike 2 Østlandet sidecars (flat NVSC).
//! Ignored when spike artifacts are absent so CI stays green.

use std::path::PathBuf;

use navi_mwm_sidecar::read_spike2_flat_wetland_count;

fn spike_dir() -> PathBuf {
    if let Ok(d) = std::env::var("NAVI_MWM_GOLDEN_DIR") {
        return PathBuf::from(d);
    }
    PathBuf::from("/tmp/navi-mwm-spike2/step4/sidecars")
}

const EXPECT: &[(&str, usize)] = &[
    ("Norway_Central", 96778),
    ("Norway_Hedmark", 85800),
    ("Norway_Oppland", 68500),
    ("Norway_Southern", 116792),
];

#[test]
#[ignore = "requires /tmp/navi-mwm-spike2 golden sidecars"]
fn golden_ostlandet_spike2_counts() {
    let dir = spike_dir();
    assert!(
        dir.is_dir(),
        "golden dir missing: {} (set NAVI_MWM_GOLDEN_DIR)",
        dir.display()
    );
    for &(leaf, expect) in EXPECT {
        let mut path = dir.join(format!("{leaf}.navi-sidecar.v1.zst"));
        if !path.is_file() {
            // alternate layout
            path = dir.join(format!("{leaf}.mwm.navi-sidecar.v1.zst"));
        }
        assert!(path.is_file(), "missing golden sidecar {}", path.display());
        let got = read_spike2_flat_wetland_count(&path).expect("decode spike2");
        assert_eq!(got, expect, "leaf {leaf}");
    }
}

#[test]
fn embedded_fixture_roundtrip_counts() {
    use navi_mwm_sidecar::{
        build_sidecar_bytes, read_sidecar, write_sidecar, BoardwalkRecord, WetlandRecord,
        CLASS_SOFT,
    };
    use tempfile::tempdir;

    let wetland: Vec<WetlandRecord> = (0..10)
        .map(|i| WetlandRecord {
            feature_id: i,
            segment_index: 0,
            direction: 0,
            class: CLASS_SOFT,
        })
        .collect();
    let bytes = build_sidecar_bytes(
        "test",
        "abc",
        [1u8; 32],
        99,
        wetland,
        Vec::<BoardwalkRecord>::new(),
        "{}",
    )
    .unwrap();
    let dir = tempdir().unwrap();
    let path = dir.path().join("x.zst");
    write_sidecar(&path, &bytes).unwrap();
    let sc = read_sidecar(&path).unwrap();
    assert_eq!(sc.wetland.len(), 10);
}
