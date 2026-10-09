//! Collapse place-index objects to single points for a slim .osm.pbf
//! that the unmodified Navi NameIndex loader can read.
//!
//! Position algorithms mirror Navi@458f65ec:
//! - ways: mean of member node lat/lon (search/mod.rs load_from_pbf)
//! - routes: first node of first way (network_pref.rs load_named_route_entries)
//! - admin L6–8: keep full outer geometry (place_context.rs) — option A

use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::process::{Command, Stdio};

use anyhow::{bail, Context, Result};
use osmpbf::{Element, ElementReader, RelMemberType};

pub struct Stats {
    pub search_nodes: usize,
    pub search_ways: usize,
    pub routes: usize,
    pub admin_rels: usize,
    pub admin_ways: usize,
    pub geom_nodes: usize,
    pub stub_nodes: usize,
}

#[derive(Clone)]
pub struct Tags(Vec<(String, String)>);

impl Tags {
    pub fn from_pairs(pairs: Vec<(String, String)>) -> Self {
        Self(pairs)
    }

    pub fn from_tags<'a>(it: impl Iterator<Item = (&'a str, &'a str)>) -> Self {
        Self(it.map(|(k, v)| (k.to_string(), v.to_string())).collect())
    }
    pub fn get(&self, key: &str) -> Option<&str> {
        self.0
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }
    pub fn map(&self) -> HashMap<String, String> {
        self.0.iter().cloned().collect()
    }
}

pub fn classify_named(tags: &Tags) -> Option<(String, String)> {
    // Exact match of Navi core/src/search/mod.rs classify_named (kind + name only).
    let mut name = None;
    let mut addr_street = None;
    let mut addr_housenumber = None;
    let mut kind = "named".to_string();
    for (k, v) in &tags.0 {
        match k.as_str() {
            "name" => name = Some(v.clone()),
            "addr:street" => addr_street = Some(v.clone()),
            "addr:housenumber" => addr_housenumber = Some(v.clone()),
            "place" => kind = format!("place:{v}"),
            "tourism" => kind = format!("tourism:{v}"),
            "leisure" => kind = format!("leisure:{v}"),
            "natural" if v == "peak" => kind = "natural:peak".into(),
            "highway" => kind = format!("highway:{v}"),
            "amenity" => kind = format!("amenity:{v}"),
            "shop" => kind = format!("shop:{v}"),
            _ => {}
        }
    }
    if name.is_none() {
        if let (Some(street), Some(num)) = (addr_street.as_ref(), addr_housenumber.as_ref()) {
            name = Some(format!("{street} {num}"));
            if kind == "named" {
                kind = "addr:housenumber".into();
            }
        } else if let Some(street) = addr_street {
            name = Some(street);
            if kind == "named" {
                kind = "addr:street".into();
            }
        }
    }
    name.map(|n| (n, kind))
}

fn admin_name_level(tags: &HashMap<String, String>) -> Option<(String, u8)> {
    let boundary = tags.get("boundary").map(|s| s.as_str()).unwrap_or("");
    if boundary != "administrative" {
        return None;
    }
    let name = tags.get("name")?.trim();
    if name.is_empty() {
        return None;
    }
    let level = tags.get("admin_level")?.parse::<u8>().ok()?;
    if !(6..=8).contains(&level) {
        return None;
    }
    Some((name.to_string(), level))
}

fn is_official_route(tags: &HashMap<String, String>, hiking: bool) -> bool {
    if !tags
        .get("type")
        .is_some_and(|v| v.eq_ignore_ascii_case("route"))
    {
        return false;
    }
    let routes: &[&str] = if hiking {
        &["hiking", "foot"]
    } else {
        &["bicycle", "mtb"]
    };
    let networks: &[&str] = if hiking {
        &["iwn", "nwn", "rwn", "lwn"]
    } else {
        &["icn", "ncn", "rcn", "lcn"]
    };
    let route_ok = tags
        .get("route")
        .is_some_and(|v| routes.iter().any(|a| v.eq_ignore_ascii_case(a)));
    let net_ok = tags
        .get("network")
        .is_some_and(|v| networks.iter().any(|a| v.eq_ignore_ascii_case(a)));
    route_ok && net_ok
}

const PILGRIM_HINTS: &[&str] = &[
    "pilegrimsled",
    "pilegrim",
    "pilgrim",
    "camino",
    "way of st. james",
    "way of saint james",
    "via francigena",
    "jakobswege",
    "jakobsweg",
    "st olav",
    "st. olav",
    "olavsleden",
];

fn is_pilgrim(tags: &HashMap<String, String>) -> bool {
    if !tags
        .get("type")
        .is_some_and(|v| v.eq_ignore_ascii_case("route"))
    {
        return false;
    }
    if tags
        .get("route")
        .is_some_and(|v| v.eq_ignore_ascii_case("pilgrimage"))
    {
        return true;
    }
    if !tags
        .get("route")
        .is_some_and(|v| v.eq_ignore_ascii_case("hiking") || v.eq_ignore_ascii_case("foot"))
    {
        return false;
    }
    for key in ["name", "name:en", "name:nb", "name:no", "operator", "ref"] {
        if tags
            .get(key)
            .is_some_and(|v| PILGRIM_HINTS.iter().any(|h| v.to_lowercase().contains(h)))
        {
            return true;
        }
    }
    false
}

fn is_superroute(tags: &HashMap<String, String>) -> bool {
    tags.get("type")
        .is_some_and(|v| v.eq_ignore_ascii_case("superroute"))
}

fn interesting_route(tags: &HashMap<String, String>) -> bool {
    is_official_route(tags, true)
        || is_official_route(tags, false)
        || is_pilgrim(tags)
        || is_superroute(tags)
}

struct OutNode {
    id: i64,
    lat: f64,
    lon: f64,
    tags: Vec<(String, String)>,
}

struct OutWay {
    id: i64,
    refs: Vec<i64>,
    tags: Vec<(String, String)>,
}

#[derive(Clone)]
struct OutRel {
    id: i64,
    members: Vec<(char, i64, String)>, // type n/w/r, id, role
    tags: Vec<(String, String)>,
}

pub fn build_points_file(input: &Path, output: &Path) -> Result<Stats> {
    // --- Pass A: relations (admin + routes meta) ---
    let mut admin_rels: Vec<(i64, Tags, Vec<i64>)> = Vec::new(); // id, tags, outer way ids
    let mut needed_admin_ways: HashSet<i64> = HashSet::new();
    let mut route_rels: HashMap<i64, (Tags, Vec<i64>, Vec<i64>)> = HashMap::new(); // tags, way_ids, child_rels
    {
        let file = File::open(input)?;
        let reader = ElementReader::new(file);
        reader.for_each(|element| {
            let Element::Relation(rel) = element else {
                return;
            };
            let tags = Tags::from_tags(rel.tags());
            let map = tags.map();
            if admin_name_level(&map).is_some() {
                let mut outers = Vec::new();
                for m in rel.members() {
                    if m.member_type != RelMemberType::Way {
                        continue;
                    }
                    let role = m.role().unwrap_or("");
                    if role.eq_ignore_ascii_case("inner") {
                        continue;
                    }
                    if role.is_empty()
                        || role.eq_ignore_ascii_case("outer")
                        || role.eq_ignore_ascii_case("part")
                    {
                        outers.push(m.member_id);
                        needed_admin_ways.insert(m.member_id);
                    }
                }
                if !outers.is_empty() {
                    admin_rels.push((rel.id(), tags.clone(), outers));
                }
            }
            if interesting_route(&map) {
                let mut way_ids = Vec::new();
                let mut child_rels = Vec::new();
                for m in rel.members() {
                    match m.member_type {
                        RelMemberType::Way => way_ids.push(m.member_id),
                        RelMemberType::Relation => child_rels.push(m.member_id),
                        RelMemberType::Node => {}
                    }
                }
                route_rels.insert(rel.id(), (tags, way_ids, child_rels));
            }
        })?;
    }

    // Also need child route tags for superroute expand — reload all relations' way lists for children
    // route_rels already only has interesting ones; children may not be "interesting" alone.
    // Re-scan for child relation way membership referenced by interesting superroutes.
    let mut child_need: HashSet<i64> = HashSet::new();
    for (_, _, children) in route_rels.values() {
        for &c in children {
            child_need.insert(c);
        }
    }
    let mut child_ways: HashMap<i64, Vec<i64>> = HashMap::new();
    if !child_need.is_empty() {
        let file = File::open(input)?;
        let reader = ElementReader::new(file);
        reader.for_each(|element| {
            let Element::Relation(rel) = element else {
                return;
            };
            if !child_need.contains(&rel.id()) {
                return;
            }
            let mut way_ids = Vec::new();
            for m in rel.members() {
                if m.member_type == RelMemberType::Way {
                    way_ids.push(m.member_id);
                }
            }
            child_ways.insert(rel.id(), way_ids);
        })?;
    }

    // --- Pass B: ways (search jobs + admin geometry + route first-node) ---
    let mut way_jobs: Vec<(i64, Tags, Vec<i64>)> = Vec::new();
    let mut needed_for_centroid: HashSet<i64> = HashSet::new();
    let mut admin_way_nodes: HashMap<i64, Vec<i64>> = HashMap::new();
    let mut standalone_admin_ways: Vec<(i64, Tags, Vec<i64>)> = Vec::new();
    let mut route_needed_ways: HashSet<i64> = HashSet::new();
    for (id, (_, ways, children)) in &route_rels {
        let _ = id;
        for &w in ways {
            route_needed_ways.insert(w);
        }
        for &c in children {
            if let Some(ws) = child_ways.get(&c) {
                for &w in ws {
                    route_needed_ways.insert(w);
                }
            }
        }
    }
    let mut way_first_node: HashMap<i64, i64> = HashMap::new();
    {
        let file = File::open(input)?;
        let reader = ElementReader::new(file);
        reader.for_each(|element| {
            let Element::Way(way) = element else {
                return;
            };
            let id = way.id();
            let refs: Vec<i64> = way.refs().collect();
            let tags = Tags::from_tags(way.tags());
            let map = tags.map();

            if needed_admin_ways.contains(&id) {
                admin_way_nodes.insert(id, refs.clone());
            }
            if admin_name_level(&map).is_some() && refs.len() >= 3 {
                standalone_admin_ways.push((id, tags.clone(), refs.clone()));
                for n in &refs {
                    needed_for_centroid.insert(*n); // reuse set for geom nodes
                }
            }
            if route_needed_ways.contains(&id) {
                if let Some(&n) = refs.first() {
                    way_first_node.insert(id, n);
                }
            }
            if let Some((_, kind)) = classify_named(&tags) {
                if !kind.starts_with("highway:") && !refs.is_empty() {
                    for n in &refs {
                        needed_for_centroid.insert(*n);
                    }
                    way_jobs.push((id, tags, refs));
                }
            }
        })?;
    }
    for refs in admin_way_nodes.values() {
        for n in refs {
            needed_for_centroid.insert(*n);
        }
    }
    for &nid in way_first_node.values() {
        needed_for_centroid.insert(nid);
    }

    // Ways that must keep full geometry: admin outers + standalone admin
    let mut keep_full_way: HashSet<i64> = needed_admin_ways.clone();
    for (id, _, _) in &standalone_admin_ways {
        keep_full_way.insert(*id);
    }

    // --- Pass C: nodes ---
    let mut node_coords: HashMap<i64, (f64, f64)> = HashMap::new();
    let mut search_nodes: Vec<OutNode> = Vec::new();
    {
        let file = File::open(input)?;
        let reader = ElementReader::new(file);
        reader.for_each(|element| match element {
            Element::Node(node) => {
                let id = node.id();
                let lat = node.lat();
                let lon = node.lon();
                if needed_for_centroid.contains(&id) {
                    node_coords.insert(id, (lat, lon));
                }
                let tags = Tags::from_tags(node.tags());
                if classify_named(&tags).is_some() {
                    search_nodes.push(OutNode {
                        id,
                        lat,
                        lon,
                        tags: tags.0,
                    });
                }
            }
            Element::DenseNode(node) => {
                let id = node.id;
                let lat = node.lat();
                let lon = node.lon();
                if needed_for_centroid.contains(&id) {
                    node_coords.insert(id, (lat, lon));
                }
                let tags = Tags::from_tags(node.tags());
                if classify_named(&tags).is_some() {
                    search_nodes.push(OutNode {
                        id,
                        lat,
                        lon,
                        tags: tags.0,
                    });
                }
            }
            _ => {}
        })?;
    }

    // --- Build output objects ---
    let mut out_nodes: HashMap<i64, OutNode> = HashMap::new();
    let mut out_ways: HashMap<i64, OutWay> = HashMap::new();
    let mut out_rels: Vec<OutRel> = Vec::new();
    let mut next_stub = -1i64;
    let mut stub_nodes = 0usize;

    // Geometry nodes for admin (tagless)
    let mut geom_node_ids: HashSet<i64> = HashSet::new();
    for refs in admin_way_nodes.values() {
        for &nid in refs {
            geom_node_ids.insert(nid);
        }
    }
    for (_, _, refs) in &standalone_admin_ways {
        for &nid in refs {
            geom_node_ids.insert(nid);
        }
    }
    for nid in &geom_node_ids {
        if let Some(&(lat, lon)) = node_coords.get(nid) {
            out_nodes.entry(*nid).or_insert(OutNode {
                id: *nid,
                lat,
                lon,
                tags: Vec::new(),
            });
        }
    }

    // Search nodes (with tags) — may overwrite tagless geom copy with tagged version
    for n in search_nodes {
        out_nodes.insert(n.id, n);
    }

    // Search ways → single-node way at centroid, unless needed for admin geometry
    let mut search_ways_collapsed = 0usize;
    let mut search_ways_full = 0usize;
    for (way_id, tags, refs) in &way_jobs {
        let mut sum_lat = 0.0;
        let mut sum_lon = 0.0;
        let mut n = 0usize;
        for id in refs {
            if let Some((lat, lon)) = node_coords.get(id) {
                sum_lat += lat;
                sum_lon += lon;
                n += 1;
            }
        }
        if n == 0 {
            continue;
        }
        let lat = sum_lat / n as f64;
        let lon = sum_lon / n as f64;

        // One representative point per search way: keep the way id + tags, with
        // a single untagged stub node at the loader centroid. (Do NOT reuse the
        // way id as a node id — that collides with real node ids and corrupts
        // admin / other way geometry.)
        // PLACE_SOURCE_KEEP_VERTICES=1 keeps original member nodes so the stock
        // loader's f64 mean matches the full extract bit-exactly (PBF cannot
        // store an arbitrary averaged centroid in one node without rounding).
        let keep_vertices = std::env::var("PLACE_SOURCE_KEEP_VERTICES")
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(false);
        if keep_full_way.contains(way_id) || keep_vertices {
            for id in refs {
                if let Some(&(la, lo)) = node_coords.get(id) {
                    out_nodes.entry(*id).or_insert(OutNode {
                        id: *id,
                        lat: la,
                        lon: lo,
                        tags: Vec::new(),
                    });
                }
            }
            out_ways.insert(
                *way_id,
                OutWay {
                    id: *way_id,
                    refs: refs.clone(),
                    tags: tags.0.clone(),
                },
            );
            search_ways_full += 1;
        } else {
            let stub_id = next_stub;
            next_stub -= 1;
            stub_nodes += 1;
            out_nodes.insert(
                stub_id,
                OutNode {
                    id: stub_id,
                    lat,
                    lon,
                    tags: Vec::new(),
                },
            );
            out_ways.insert(
                *way_id,
                OutWay {
                    id: *way_id,
                    refs: vec![stub_id],
                    tags: tags.0.clone(),
                },
            );
            search_ways_collapsed += 1;
        }
    }

    // Admin ways (outers) not already emitted as search ways
    for (wid, refs) in &admin_way_nodes {
        if out_ways.contains_key(wid) {
            // Already emitted (possibly with search tags). Ensure full refs for admin.
            if let Some(w) = out_ways.get_mut(wid) {
                if w.refs.len() == 1 && w.refs[0] < 0 {
                    // Was collapsed; restore full geometry and drop stub-only representation.
                    for id in refs {
                        if let Some(&(la, lo)) = node_coords.get(id) {
                            out_nodes.entry(*id).or_insert(OutNode {
                                id: *id,
                                lat: la,
                                lon: lo,
                                tags: Vec::new(),
                            });
                        }
                    }
                    w.refs = refs.clone();
                }
            }
            continue;
        }
        for id in refs {
            if let Some(&(la, lo)) = node_coords.get(id) {
                out_nodes.entry(*id).or_insert(OutNode {
                    id: *id,
                    lat: la,
                    lon: lo,
                    tags: Vec::new(),
                });
            }
        }
        out_ways.insert(
            *wid,
            OutWay {
                id: *wid,
                refs: refs.clone(),
                tags: Vec::new(),
            },
        );
    }

    // Standalone admin ways
    for (wid, tags, refs) in &standalone_admin_ways {
        for id in refs {
            if let Some(&(la, lo)) = node_coords.get(id) {
                out_nodes.entry(*id).or_insert(OutNode {
                    id: *id,
                    lat: la,
                    lon: lo,
                    tags: Vec::new(),
                });
            }
        }
        out_ways
            .entry(*wid)
            .and_modify(|w| {
                w.refs = refs.clone();
                if w.tags.is_empty() {
                    w.tags = tags.0.clone();
                }
            })
            .or_insert(OutWay {
                id: *wid,
                refs: refs.clone(),
                tags: tags.0.clone(),
            });
    }

    // Admin relations
    let admin_rel_count = admin_rels.len();
    for (rid, tags, outers) in admin_rels {
        let members = outers
            .into_iter()
            .map(|wid| ('w', wid, "outer".to_string()))
            .collect();
        out_rels.push(OutRel {
            id: rid,
            members,
            tags: tags.0,
        });
    }

    // Named routes: smallest faithful stub — relation tags + one way member + one node at
    // the same coordinate load_named_route_entries would use.
    let mut routes_out = 0usize;
    for (rid, (tags, way_ids, children)) in &route_rels {
        let map = tags.map();
        let has_name = map.contains_key("name") || map.contains_key("ref");
        if !has_name {
            continue;
        }
        let mut way_id = way_ids.first().copied();
        if way_id.is_none() {
            for &c in children {
                if let Some(ws) = child_ways.get(&c) {
                    if let Some(&w) = ws.first() {
                        way_id = Some(w);
                        break;
                    }
                }
            }
        }
        let Some(orig_wid) = way_id else {
            continue;
        };
        let Some(&orig_nid) = way_first_node.get(&orig_wid) else {
            continue;
        };
        let Some(&(lat, lon)) = node_coords.get(&orig_nid) else {
            continue;
        };

        // Stub node at exact original first-node coordinates (same nano→f64 as source).
        let stub_node = next_stub;
        next_stub -= 1;
        stub_nodes += 1;
        out_nodes.insert(
            stub_node,
            OutNode {
                id: stub_node,
                lat,
                lon,
                tags: Vec::new(),
            },
        );
        let stub_way = next_stub;
        next_stub -= 1;
        out_ways.insert(
            stub_way,
            OutWay {
                id: stub_way,
                refs: vec![stub_node],
                tags: Vec::new(),
            },
        );
        out_rels.push(OutRel {
            id: *rid,
            members: vec![('w', stub_way, String::new())],
            tags: tags.0.clone(),
        });
        routes_out += 1;
    }

    let stats = Stats {
        search_nodes: out_nodes.values().filter(|n| !n.tags.is_empty()).count(),
        search_ways: search_ways_collapsed + search_ways_full,
        routes: routes_out,
        admin_rels: admin_rel_count,
        admin_ways: admin_way_nodes.len() + standalone_admin_ways.len(),
        geom_nodes: geom_node_ids.len(),
        stub_nodes,
    };

    // Write OSM XML then convert with osmium (granularity=1 via header if possible).
    let xml_path = output.with_extension("trialb.osm");
    write_osm_xml(&xml_path, &out_nodes, &out_ways, &out_rels)?;
    convert_xml_to_pbf(&xml_path, output)?;
    let _ = std::fs::remove_file(&xml_path);

    let _ = (search_ways_collapsed, search_ways_full);
    Ok(stats)
}

fn xml_escape(s: &str) -> String {
    // Preserve newlines/tabs in tag values via character refs — XML attribute
    // normalization would otherwise turn them into spaces on read.
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '"' => out.push_str("&quot;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '\n' => out.push_str("&#10;"),
            '\r' => out.push_str("&#13;"),
            '\t' => out.push_str("&#9;"),
            c => out.push(c),
        }
    }
    out
}

fn write_osm_xml(
    path: &Path,
    nodes: &HashMap<i64, OutNode>,
    ways: &HashMap<i64, OutWay>,
    rels: &[OutRel],
) -> Result<()> {
    let f = File::create(path)?;
    let mut w = BufWriter::with_capacity(8 * 1024 * 1024, f);
    writeln!(w, "<?xml version=\"1.0\" encoding=\"UTF-8\"?>")?;
    writeln!(w, "<osm version=\"0.6\" generator=\"navi-place-source\">")?;

    let mut node_ids: Vec<i64> = nodes.keys().copied().collect();
    node_ids.sort_unstable();
    for id in node_ids {
        let n = &nodes[&id];
        // 9 decimal places ↔ nanodegree PBF grid used by osmpbf lat().
        write!(
            w,
            "<node id=\"{}\" version=\"1\" lat=\"{:.9}\" lon=\"{:.9}\"",
            n.id, n.lat, n.lon
        )?;
        if n.tags.is_empty() {
            writeln!(w, "/>")?;
        } else {
            writeln!(w, ">")?;
            for (k, v) in &n.tags {
                writeln!(w, "<tag k=\"{}\" v=\"{}\"/>", xml_escape(k), xml_escape(v))?;
            }
            writeln!(w, "</node>")?;
        }
    }

    let mut way_ids: Vec<i64> = ways.keys().copied().collect();
    way_ids.sort_unstable();
    for id in way_ids {
        let way = &ways[&id];
        writeln!(w, "<way id=\"{}\" version=\"1\">", way.id)?;
        for r in &way.refs {
            writeln!(w, "<nd ref=\"{r}\"/>")?;
        }
        for (k, v) in &way.tags {
            writeln!(w, "<tag k=\"{}\" v=\"{}\"/>", xml_escape(k), xml_escape(v))?;
        }
        writeln!(w, "</way>")?;
    }

    let mut rels_sorted = rels.to_vec();
    rels_sorted.sort_by_key(|r| r.id);
    for rel in rels_sorted {
        writeln!(w, "<relation id=\"{}\" version=\"1\">", rel.id)?;
        for (ty, mid, role) in &rel.members {
            let t = match ty {
                'n' => "node",
                'w' => "way",
                'r' => "relation",
                _ => "way",
            };
            writeln!(
                w,
                "<member type=\"{}\" ref=\"{}\" role=\"{}\"/>",
                t,
                mid,
                xml_escape(role)
            )?;
        }
        for (k, v) in &rel.tags {
            writeln!(w, "<tag k=\"{}\" v=\"{}\"/>", xml_escape(k), xml_escape(v))?;
        }
        writeln!(w, "</relation>")?;
    }
    writeln!(w, "</osm>")?;
    w.flush()?;
    Ok(())
}

fn convert_xml_to_pbf(xml: &Path, pbf: &Path) -> Result<()> {
    // granularity=1 → 1 nanodegree, matching osmpbf's 1e-9 scaling.
    let status = Command::new("osmium")
        .args(["cat", "--output-header=granularity=1", "-o"])
        .arg(pbf)
        .arg("-O")
        .arg(xml)
        .stdin(Stdio::null())
        .status()
        .context("run osmium cat")?;
    if !status.success() {
        bail!("osmium cat failed: {status}");
    }
    Ok(())
}

/// Core place-index row identity used in tests: (osm_id, kind, name).
/// Mirrors Navi@458f65ec NameIndex::load_from_pbf + load_named_route_entries
/// for those three fields only (positions not compared here).
pub fn place_index_core_rows(input: &Path) -> Result<Vec<(i64, String, String)>> {
    let mut rows: Vec<(i64, String, String)> = Vec::new();

    // Nodes + ways (skip highway:* ways), same as load_from_pbf.
    {
        let file = File::open(input)?;
        let reader = ElementReader::new(file);
        reader.for_each(|element| match element {
            Element::Node(node) => {
                let tags = Tags::from_tags(node.tags());
                if let Some((name, kind)) = classify_named(&tags) {
                    rows.push((node.id(), kind, name));
                }
            }
            Element::DenseNode(node) => {
                let tags = Tags::from_tags(node.tags());
                if let Some((name, kind)) = classify_named(&tags) {
                    rows.push((node.id, kind, name));
                }
            }
            Element::Way(way) => {
                let tags = Tags::from_tags(way.tags());
                if let Some((name, kind)) = classify_named(&tags) {
                    if !kind.starts_with("highway:") {
                        rows.push((way.id(), kind, name));
                    }
                }
            }
            _ => {}
        })?;
    }

    // Named routes (relation osm_id). Kind matches network_pref route labeling
    // approximately for test fixtures: route:{hiking|bicycle|pilgrimage|...}.
    {
        let file = File::open(input)?;
        let reader = ElementReader::new(file);
        reader.for_each(|element| {
            let Element::Relation(rel) = element else {
                return;
            };
            let tags = Tags::from_tags(rel.tags());
            let map = tags.map();
            if !interesting_route(&map) {
                return;
            }
            let name = map.get("name").cloned().or_else(|| map.get("ref").cloned());
            let Some(name) = name else {
                return;
            };
            let operator = map.get("operator").cloned();
            let kind = if is_pilgrim(&map) {
                "route:pilgrimage".to_string()
            } else if is_official_route(&map, true) {
                format!(
                    "route:hiking:{}",
                    map.get("network").map(String::as_str).unwrap_or("?")
                )
            } else if is_official_route(&map, false) {
                format!(
                    "route:bicycle:{}",
                    map.get("network").map(String::as_str).unwrap_or("?")
                )
            } else {
                "route:superroute".to_string()
            };
            let search_name = match &operator {
                Some(op) if !name.to_lowercase().contains(&op.to_lowercase()) => {
                    format!("{name} ({op})")
                }
                _ => name,
            };
            rows.push((rel.id(), kind, search_name));
        })?;
    }

    rows.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)).then(a.2.cmp(&b.2)));
    rows.dedup();
    Ok(rows)
}
