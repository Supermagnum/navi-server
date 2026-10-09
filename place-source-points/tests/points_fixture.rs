//! Filter + point reduction on the checked-in mini fixture.
//! Compares (osm_id, kind, name) from the full fixture vs the place-source
//! output using the Navi@458f65ec classifier mirrored in this crate.
//! Requires `osmium` on PATH (CI installs osmium-tool).

use std::path::PathBuf;
use std::process::Command;

use place_source_points::{build_points_file, place_index_core_rows};

fn osmium_ok() -> bool {
    Command::new("osmium")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn fixture_pbf() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/place-mini.osm.pbf")
}

#[test]
fn place_source_core_rows_match_full_fixture() {
    if !osmium_ok() {
        eprintln!("skip: osmium not on PATH");
        return;
    }
    let input = fixture_pbf();
    assert!(input.is_file(), "missing fixture {}", input.display());

    let dir = tempfile::tempdir().expect("tempdir");
    let out = dir.path().join("place-mini.navi-place-source.osm.pbf");
    build_points_file(&input, &out).expect("build place-source");
    assert!(out.is_file());
    assert!(out.metadata().unwrap().len() > 0);
    assert!(out.metadata().unwrap().len() < input.metadata().unwrap().len());

    let full = place_index_core_rows(&input).expect("full rows");
    let slim = place_index_core_rows(&out).expect("place-source rows");
    assert_eq!(
        full, slim,
        "core rows (id, kind, name) must match\nfull={full:?}\nslim={slim:?}"
    );

    // Spot-check expected fixture rows (Navi classify_named + route kinds).
    assert!(full
        .iter()
        .any(|(id, k, n)| *id == 1 && k == "place:town" && n == "Test Town"));
    assert!(full
        .iter()
        .any(|(id, k, n)| *id == 2 && k == "amenity:cafe" && n == "Cafe Test"));
    assert!(full
        .iter()
        .any(|(id, k, n)| *id == 3 && k == "highway:bus_stop" && n == "Bus Stop A"));
    assert!(full
        .iter()
        .any(|(id, k, n)| *id == 5 && k == "addr:housenumber" && n == "Sideveien 12"));
    assert!(full
        .iter()
        .any(|(id, k, n)| *id == 100 && k == "named" && n == "Named Hall"));
    assert!(full
        .iter()
        .any(|(id, k, n)| *id == 400 && k == "route:hiking:lwn" && n == "Test Trail"));
    // Named highway ways must not appear.
    assert!(!full.iter().any(|(id, _, _)| *id == 101));
    // Admin relation is context-only (not a search row unless classify_named).
    assert!(!full.iter().any(|(id, _, _)| *id == 300));
}

#[test]
fn classify_named_unit_smoke() {
    use place_source_points::{classify_named, Tags};
    let t = Tags::from_pairs(vec![
        ("name".into(), "X".into()),
        ("shop".into(), "bakery".into()),
    ]);
    let (name, kind) = classify_named(&t).unwrap();
    assert_eq!(name, "X");
    assert_eq!(kind, "shop:bakery");
}
