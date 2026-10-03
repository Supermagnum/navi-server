# Ferry coverage analysis

Read-only analysis of OSM `route=ferry` (Overpass) against published Navi packs and `data/regions.conf`. Raw Overpass JSON stays under scratch (not committed).

**Bake policy:** there is **no planet-wide bake**. The Monday weekly job (`data/regions.conf`) is the only regular bake. Regions that need ferry boarding links and **fit** the weekly budget should be **added to the weekly list** (exact conf lines below). Over-budget / cut-off leaves: run a **targeted single-region bake now** so ferry-links or v9 fixes land without waiting for (or depending on) a planet run; only promote them onto weekly later if the combined job still fits. Do not edit live config from this PR.

- Scratch: `/tmp/navi-ferry-coverage-20261003`
- Per-ferry CSV: `/tmp/navi-ferry-coverage-20261003/out/ferries_all.csv`
- Region assignment CSV: `/tmp/navi-ferry-coverage-20261003/out/ferries_by_region.csv`
- Overpass `osm_base` (route=ferry): `2026-10-03T03:40:50Z`
- Pack scan estimate: ~2.93 h (328 packs with car-capable OSM ferries)

## 1. Tag inventory

- `route=ferry` objects (ways+relations): **35837**
- by OSM type: `{'way': 34623, 'relation': 4818}`
- ways with `ferry=*` but not `route=ferry`: **3604**

### Classification (`ferry_allowed_for_profile`)

Quoted rule from converter:

```rust
// pack-convert-core/src/routing/graph/builder.rs
pub(crate) fn ferry_allowed_for_profile(...) -> bool {
    match profile {
        RoutingProfile::Car | RoutingProfile::Truck => {
            tags.get("motor_vehicle").is_some_and(|v| access::is_access_yes(v))
                || tags.get("motorcar").is_some_and(|v| access::is_access_yes(v))
        }
        RoutingProfile::Foot | RoutingProfile::Bicycle => {
            !access::tags_forbid_mode(tags, profile.access_mode())
        }
    }
}
// is_access_yes: yes|true|1|designated|permissive|official
// Bare route=ferry without motor_vehicle/motorcar yes-set is NOT car-capable.
```

- Counts (all ingested objects): `{'car-capable': 4732, 'passenger-bicycle-only': 34368, 'unknown': 341}`
- `route=ferry` only: `{'car-capable': 4705, 'passenger-bicycle-only': 30839, 'unknown': 293}`
- Note: Car-capable requires motor_vehicle or motorcar in the access yes-set (yes|true|1|designated|permissive|official). vehicle=yes alone is NOT enough.

#### Unknown tag combinations (top)

- `motor_vehicle=|motorcar=|vehicle=|access=private|foot=|bicycle=`: 101
- `motor_vehicle=|motorcar=|vehicle=|access=no|foot=|bicycle=`: 50
- `motor_vehicle=|motorcar=|vehicle=|access=|foot=no|bicycle=no`: 33
- `motor_vehicle=no|motorcar=|vehicle=|access=|foot=no|bicycle=no`: 25
- `motor_vehicle=no|motorcar=|vehicle=|access=private|foot=|bicycle=`: 16
- `motor_vehicle=no|motorcar=|vehicle=no|access=|foot=customers|bicycle=customers`: 11
- `motor_vehicle=no|motorcar=|vehicle=|access=|foot=customers|bicycle=no`: 7
- `motor_vehicle=no|motorcar=|vehicle=|access=no|foot=|bicycle=`: 6
- `motor_vehicle=no|motorcar=|vehicle=|access=|foot=customers|bicycle=customers`: 4
- `motor_vehicle=|motorcar=|vehicle=no|access=private|foot=|bicycle=`: 4
- `motor_vehicle=|motorcar=no|vehicle=|access=|foot=no|bicycle=no`: 4
- `motor_vehicle=no|motorcar=|vehicle=|access=no|foot=no|bicycle=no`: 4
- `motor_vehicle=|motorcar=|vehicle=|access=no|foot=private|bicycle=`: 4
- `motor_vehicle=no|motorcar=|vehicle=|access=customers|foot=customers|bicycle=customers`: 4
- `motor_vehicle=no|motorcar=no|vehicle=|access=no|foot=no|bicycle=no`: 3
- `motor_vehicle=no|motorcar=|vehicle=|access=private|foot=private|bicycle=`: 3
- `motor_vehicle=no|motorcar=|vehicle=|access=no|foot=no|bicycle=`: 3
- `motor_vehicle=|motorcar=|vehicle=|access=|foot=use_sidepath|bicycle=use_sidepath`: 3
- `motor_vehicle=no|motorcar=|vehicle=|access=private|foot=no|bicycle=no`: 2
- `motor_vehicle=|motorcar=no|vehicle=|access=private|foot=|bicycle=`: 2
- `motor_vehicle=permit|motorcar=|vehicle=|access=permit|foot=permit|bicycle=permit`: 2
- `motor_vehicle=customers|motorcar=|vehicle=|access=|foot=customers|bicycle=customers`: 2
- `motor_vehicle=no|motorcar=|vehicle=|access=permit|foot=no|bicycle=no`: 2
- `motor_vehicle=|motorcar=|vehicle=no|access=no|foot=|bicycle=`: 2
- `motor_vehicle=|motorcar=|vehicle=|access=|foot=customers|bicycle=no`: 2
- `motor_vehicle=no|motorcar=|vehicle=|access=no|foot=|bicycle=no`: 2
- `motor_vehicle=|motorcar=|vehicle=|access=no|foot=customers|bicycle=`: 2
- `motor_vehicle=no|motorcar=|vehicle=|access=|foot=destination|bicycle=destination`: 2
- `motor_vehicle=no|motorcar=|vehicle=|access=customers|foot=customers|bicycle=no`: 2
- `motor_vehicle=|motorcar=|vehicle=no|access=|foot=no|bicycle=no`: 2

### Duration

- duration tag present: 6475
- parseable as `H:MM` / `HH:MM:SS`: 6085
- car-capable `route=ferry` without duration: 2384 (share 0.5067)

### Tag key totals (every key)

- `route`: 35840
- `name`: 20282
- `foot`: 13376
- `motor_vehicle`: 13208
- `operator`: 9163
- `bicycle`: 8764
- `ferry`: 7598
- `duration`: 6475
- `from`: 5708
- `to`: 5695
- `type`: 5059
- `ref`: 4392
- `website`: 4171
- `toll`: 3659
- `public_transport:version`: 3499
- `public_transport`: 3347
- `fee`: 3230
- `network`: 2892
- `motorcar`: 2449
- `source`: 2348
- `name:en`: 2264
- `hgv`: 1987
- `amenity`: 1912
- `opening_hours`: 1900
- `horse`: 1834
- `network:wikidata`: 1811
- `wheelchair`: 1365
- `building`: 1295
- `note`: 1195
- `access`: 1187
- `description`: 1025
- `operator:wikidata`: 993
- `motorcycle`: 922
- `vehicle`: 848
- `name:zh`: 798
- `via`: 785
- `man_made`: 779
- `interval`: 766
- `wikidata`: 713
- `reservation`: 598
- `network:wikipedia`: 579
- `phone`: 557
- `shelter`: 553
- `charge`: 538
- `oneway`: 524
- `int_name`: 523
- `name:el`: 511
- `wikipedia`: 510
- `area`: 507
- `seamark:type`: 501
- `bench`: 492
- `roundtrip`: 485
- `seasonal`: 471
- `surface`: 465
- `lit`: 420
- `bin`: 406
- `waterway`: 396
- `colour`: 391
- `ferry:cable`: 382
- `check_date`: 362
- `tactile_paving`: 344
- `alt_name`: 338
- `seamark:ferry_route:category`: 332
- `addr:city`: 325
- `dog`: 322
- `payment:cash`: 320
- `addr:street`: 315
- `layer`: 305
- `maxweight`: 296
- `highway`: 293
- `name:zh-Hans`: 271
- `operator:wikipedia`: 264
- `network:short`: 262
- `addr:postcode`: 244
- `cargo`: 241
- `operator:type`: 234
- `name:fr`: 233
- `name:zh-Hant`: 230
- `fixme`: 227
- `url`: 211
- `floating`: 210
- `operator:short`: 204
- `addr:housenumber`: 203
- `name:ru`: 194
- `boat`: 194
- `name:ar`: 186
- `name:ko`: 183
- `operator:website`: 177
- `name:sv`: 177
- `name:fi`: 175
- `contact:website`: 171
- `name:ja`: 167
- `gtfs:feed`: 163
- `building:levels`: 163
- `name:es`: 162
- `name:br`: 161
- `gtfs:route_id`: 157
- `name:uk`: 153
- `email`: 151
- `capacity`: 148
- `name:it`: 134
- `name:th`: 134
- `start_date`: 131
- `netex:journeypattern:id:SE-SL`: 127
- `contact:phone`: 123
- `wikimedia_commons`: 121
- `gtfs:trip_id:sample`: 121
- `canoe`: 119
- `name:de`: 119
- `mooring`: 118
- `opening_hours:url`: 117
- `charge:motorcar`: 114
- `charge:motorcycle`: 114
- `moped`: 114
- `covered`: 113
- `charge:electric_vehicle`: 109
- `note:interval`: 109
- `weather:rain`: 108
- `name:ms`: 106
- `departures_board`: 104
- `motorboat`: 103
- `seasonal:disruption`: 103
- `internet_access`: 100
- `ref_trips`: 98
- `tourism`: 94
- `gtfs:trip_id:like`: 94
- `maxspeed`: 90
- `goods`: 89
- `image`: 86
- `source:date`: 86
- `mofa`: 85
- `ferry:type`: 85
- `brand`: 84
- `gtfs:release_date`: 84
- `int_ref`: 83
- `bus`: 83
- `contact:instagram`: 81
- `attraction`: 79
- `name:nl`: 77
- `ice_road`: 72
- `name:mr`: 71
- `access:conditional`: 69
- `colour:text`: 66
- `route_ref`: 66
- `contact:email`: 65
- `operator:en`: 63
- `owner`: 62
- `height`: 62
- `bridge`: 61
- `name:ku`: 61
- `tiger:county`: 60
- `name:kn`: 60
- `name:hi`: 60
- `addr:province`: 60
- `maxaxleload`: 59
- `tiger:cfcc`: 59
- `capacity:persons`: 58
- `name:te`: 58
- `hazmat`: 55
- `from:en`: 55
- `CEMT`: 55
- `service_times`: 54
- `netex:quay:id:SE-SL`: 54
- `to:en`: 53
- `psv`: 50
- `maxheight`: 50
- `loc_name`: 50
- `payment:clipper`: 50
- `opening_hours:backward`: 50
- `opening_hours:forward`: 50
- `name:mk`: 48
- `tidal`: 48
- `line`: 48
- `maxlength`: 47
- `interval:conditional`: 47
- `disused:route`: 47
- `payment:prepaid_ticket`: 47
- `distance`: 46
- `name:oc`: 46
- `addr:state`: 45
- `name:id`: 45
- `source:device`: 45
- `roof:shape`: 45
- `intermittent`: 44
- `foot:conditional`: 43
- `description:en`: 42
- `source:name:oc`: 42
- `service`: 41
- `description:payment`: 41
- `note:via`: 41
- `railway`: 40
- `name:pt`: 40
- `payment:app`: 40
- `old_name`: 38
- `description:payment:en`: 38
- `ele`: 36
- `network:en`: 36
- `not:network:wikidata`: 35
- `disused`: 35
- `toilets:wheelchair`: 35
- `noname`: 35
- `ford`: 35
- `contact:facebook`: 34
- `check_date:opening_hours`: 34
- `payment:contactless`: 34
- `train`: 34
- `internet_access:fee`: 34
- `network:zh`: 34
- `mapillary`: 34
- `smoking`: 33
- `vessel`: 33
- `charge:url`: 32
- `addr:country`: 32
- `name:ja-Latn`: 32
- `ship`: 32
- `emergency`: 31
- `carriage`: 31
- `official_name`: 31
- `tourist_bus`: 31
- `comment`: 31
- `operator:addr:country`: 31
- `self_service`: 31
- `addr:housename`: 31
- `toilets`: 30
- `capacity:bicycle`: 30
- `addr:district`: 30
- `air_conditioning`: 30
- `name:gd`: 29
- `operator:zh`: 29
- `old_ref`: 29
- `name:be`: 29
- `source:name`: 29
- `from:zh`: 28
- `passenger`: 28
- `to:zh`: 28
- `tiger:reviewed`: 28
- `name:ka`: 28
- `tiger:name_base`: 27
- `trailer`: 27
- `direction`: 27
- `name:tr`: 27
- `ferry:name`: 26
- `short_name`: 26
- `old_operator`: 26
- `name:ja-Hira`: 26
- `name:ca`: 26
- `name:sr`: 26
- `network:guid`: 26
- `payment:credit_cards`: 25
- `ref:vayla`: 25
- `seamark:recommended_track:minimum_depth`: 25
- `lanes`: 25
- `payment:debit_cards`: 24
- `addr:suburb`: 24
- `source:name:br`: 24
- `id`: 24
- `seamark:recommended_track:orientation`: 24
- `informal`: 24
- `landuse`: 24
- `winter_road`: 23
- `payment:bank_transfer`: 23
- `local_ref`: 23
- `passenger_information_display`: 23
- `agricultural`: 22
- `name:pl`: 22
- `source:duration`: 21
- `bicycle:conditional`: 21
- `survey:date`: 21
- `alt_name:en`: 21
- `payment:octopus`: 20
- `abandoned`: 20
- `tunnel`: 20
- `level`: 20
- `TMC:cid_58:tabcd_1:Class`: 19
- `TMC:cid_58:tabcd_1:LCLversion`: 19
- `TMC:cid_58:tabcd_1:LocationCode`: 19
- `maxweight:signed`: 19
- `taxi`: 19
- `fax`: 19
- `caravan`: 19
- `width`: 19
- `lock`: 19
- `name:vi`: 19
- `roof:levels`: 19
- `uic_ref`: 19
- `wheelchair:description`: 18
- `stroller`: 18
- `network:website`: 18
- `bdouble`: 18
- `smoothness`: 18
- `payment:oyster`: 18
- `check_date:shelter`: 18
- `pier`: 18
- `monorail`: 18
- `tram`: 18
- `trolleybus`: 18
- `uic_name`: 18
- `sign`: 18
- `ramp`: 18
- `ownership`: 17
- `description:no`: 17
- `text_colour`: 17
- `parking`: 17
- `subway`: 17
- `TMC:cid_58:tabcd_1:NextLocationCode`: 16
- `operation`: 16
- `source_ref`: 16
- `end_date`: 16
- `source:geometry`: 16
- `operator:official_name:de`: 16
- `operator:official_name:fr`: 16
- `addr:housename:en`: 16
- `addr:housename:zh`: 16
- `maxwidth`: 15
- `TMC:cid_58:tabcd_1:PrevLocationCode`: 15
- `brand:en`: 15
- `brand:zh`: 15
- `operator:official_name`: 15
- `name:lt`: 15
- `contact:mobile`: 15
- `from:es`: 15
- `source_ref:url`: 15
- `seamark:recommended_track:category`: 15
- `name:nan`: 15
- `access:ferry`: 15
- `addr:street:en`: 15
- `addr:street:zh`: 15
- `addr:unit`: 15
- `loc_ref`: 15
- `public_transport:ferry`: 15
- `light_rail`: 15
- `owner:en`: 14
- `owner:zh`: 14
- `contact:fax`: 14
- `note:de`: 14
- `payment:maestro`: 14
- `name:gl`: 14
- `name:bg`: 14
- `name:et`: 14
- `from:ja`: 14
- `to:ja`: 14
- `route_master`: 14
- `name:nan-Latn-pehoeji`: 14
- `by_night`: 14
- `addr`: 14
- `addr:unit:en`: 14
- `addr:unit:zh`: 14
- `capacity:motorcar`: 13
- `payment:cards`: 13
- `lock_name`: 13
- `draft`: 13
- `source:draft`: 13
- `seamark:shoreline_construction:category`: 13
- `addr:place`: 13
- `toll:bicycle`: 12
- `brand:wikidata`: 12
- `attribution`: 12
- `note:en`: 12
- `name:cs`: 12
- `opening_hours:covid19`: 12
- `old_website`: 12
- `to:es`: 12
- `source_date`: 12
- `source_type`: 12
- `name:yue`: 12
- `name:nan-Latn-tailo`: 12
- `bridge:structure`: 12
- `transport:zone`: 12
- `lane_markings`: 12
- `addr:subdistrict`: 12
- `description:de`: 11
- `name:az`: 11
- `name:da`: 11
- `name:fa`: 11
- `name:ml`: 11
- `from:ko`: 11
- `to:ko`: 11
- `fee_zone`: 11
- `payment:troika`: 11
- `addr:neighbourhood`: 11
- `maxspeed:type`: 11
- `zone:traffic`: 11
- `industrial`: 11
- `toll:foot`: 10
- `owner:wikidata`: 10
- `fee:conditional`: 10
- `capacity:car`: 10
- `highway:category:pl`: 10
- `ID`: 10
- `was:route`: 10
- `name:sq`: 10
- `facebook`: 10
- `name:sr-Latn`: 10
- `from:ja-Latn`: 10
- `to:ja-Latn`: 10
- `name:ur`: 10
- `capacity:electric_bicycle`: 10
- `state`: 10
- `gtfs:shape_id`: 10
- `indoor`: 10
- `historic`: 9
- `reg_ref`: 9
- `payment:mastercard`: 9
- `payment:visa`: 9
- `lcn`: 9
- `travel_time`: 9
- `motorhome`: 9
- `atv`: 9
- `name:hu`: 9
- `IBGE:CD_ADMINIS`: 9
- `disabled`: 9
- `name:lv`: 9
- `motorcycle:conditional`: 9
- `addr:full`: 9
- `payment:cowry_card`: 9
- `whatsapp`: 9
- `roof:colour`: 9
- `seamark:harbour:category`: 9
- `location`: 9
- `ref:bag`: 9
- `seamark:shoreline_construction:restriction`: 9
- `addr:town`: 9
- `payment:coins`: 8
- `payment:alipay`: 8
- `TMC:cid_58:tabcd_1:Direction`: 8
- `interval:night`: 8
- `payment:card`: 8
- `frequency`: 8
- `open_water`: 8
- `rapids`: 8
- `contact:twitter`: 8
- `name:hr`: 8
- `time`: 8
- `operator:ar`: 8
- `name:nan-Hant`: 8
- `disused:roller_coaster`: 8
- `frequence`: 8
- `note:opening_hours`: 7
- `payment:others`: 7
- `tiger:name_type`: 7
- `created_by`: 7
- `mobile`: 7
- `charge:foot`: 7
- `service_times:backward`: 7
- `service_times:forward`: 7
- `fee:bicycle`: 7
- `seasonal:summer`: 7
- `opening_date`: 7
- `source:bicycle`: 7
- `material`: 7
- `on_demand`: 7
- `description:fi`: 7
- `name:ko-Latn`: 7
- `note:fr`: 7
- `name:ro`: 7
- `from:ar`: 7
- `to:ar`: 7
- `ref:GB:uprn`: 7
- `abutters`: 7
- `ele:egm96`: 7
- `ele:regional`: 7
- `ele:wgs84`: 7
- `retail`: 7
- `building:material`: 7
- `harbour`: 7
- `check_date:bench`: 7
- `addr:block_number`: 7
- `shelter_type`: 7
- `name:kw`: 6
- `source:note`: 6
- `destination`: 6
- `charge:bicycle`: 6
- `tiger:name_base_1`: 6
- `panoramax`: 6
- `tiger:source`: 6
- `tiger:tlid`: 6
- `tiger:upload_uuid`: 6
- `maxweight:hgv_articulated`: 6
- `destination:backward`: 6
- `destination:forward`: 6
- `length`: 6
- `operator:fr`: 6
- `hgv_articulated`: 6
- `alt_name:es`: 6
- `alt_name:ja`: 6
- `seasonal:winter`: 6
- `name:fur`: 6
- `name:ab`: 6
- `admin_level`: 6
- `hov`: 6
- `description:nl`: 6
- `boat:name`: 6
- `name:signed`: 6
- `gnis:feature_id`: 6
- `name:bn`: 6
- `gvr:code`: 6
- `name:cv`: 6
- `name:he`: 6
- `name:no`: 6
- `name:sk`: 6
- `railway:traffic_mode`: 6
- `cargo:passengers`: 6
- `boundary`: 6
- `from:ja-Hira`: 6
- `to:ja-Hira`: 6
- `source:url`: 6
- `name:am`: 6
- `seamark:name`: 6
- `playground`: 6
- `name:nan-Hans`: 6
- `alt_name:zh`: 6
- `from:oc`: 6
- `to:oc`: 6
- `gtfs:trip_id`: 6
- `building:part`: 6
- `ref:bygningsnr`: 6
- `short_name:zh`: 6
- `access:roof`: 6
- `backup_generator`: 6
- `building:condition`: 6
- `building:floor`: 6
- `building:walls`: 6
- `shop`: 6
- `addr:city:ar`: 6
- `nohousenumber`: 6
- `baby_feeding`: 6
- `charge:conditional`: 5
- `seamark:information`: 5
- `waterway:sign`: 5
- `price`: 5
- `rcn_ref`: 5
- `bicycle:trailer`: 5
- `reference`: 5
- `source:maxweight`: 5
- `barrier`: 5
- `timetable`: 5
- `operator:ja-Latn`: 5
- `class:bicycle:touring`: 5
- `source:class:bicycle:touring`: 5
- `track`: 5
- `ship:name`: 5
- `interval:backward:conditional`: 5
- `interval:forward:conditional`: 5
- `operator:ownership`: 5
- `destination:country:backward`: 5
- `destination:country:forward`: 5
- `addr:city:es`: 5
- `transport`: 5
- `operational_status`: 5
- `name:af`: 5
- `name:cy`: 5
- `name:eo`: 5
- `name:eu`: 5
- `name:fy`: 5
- `name:hy`: 5
- `name:la`: 5
- `name:nn`: 5
- `name:os`: 5
- `name:su`: 5
- `name:sw`: 5
- `name:tt`: 5
- `name:uz`: 5
- `foot:backward:conditional`: 5
- `foot:forward:conditional`: 5
- `operator:abbr`: 5
- `source:maxlength`: 5
- `info`: 5
- `description:zh`: 5
- `from:ru`: 5
- `to:ru`: 5
- `fishing`: 5
- `note:boat`: 5
- `note:fee`: 5
- `note:payment`: 5
- `note:stops`: 5
- `note:weather`: 5
- `operational:notes`: 5
- `operational_category`: 5
- `weekend`: 5
- `colour:infill`: 5
- `affiliation`: 5
- `timestamp`: 5
- `railway:ref:DB`: 5
- `short_name:en`: 5
- `building:roof`: 5
- `internet_access:ssid`: 5
- `gtfs:stop_id`: 5
- `building:structure`: 5
- `addr:quarter`: 5
- `wheelchair:description:en`: 5
- `wheelchair:description:zh`: 5
- `defibrillator:location`: 4
- `canvec:CODE`: 4
- `payment:girocard`: 4
- `description:sv`: 4
- `departures:backward`: 4
- `departures:forward`: 4
- `bridge:movable`: 4
- `heritage`: 4
- `wikipedia:de`: 4
- `website:en`: 4
- `usage`: 4
- `website:de`: 4
- `url:timetable`: 4
- `duration:backward`: 4
- `duration:forward`: 4
- `fee:foot`: 4
- `mobility_scooter`: 4
- `opening_hours:description`: 4
- `via:en`: 4
- `via:zh`: 4
- `hiking`: 4
- `name:sl`: 4
- `tracktype`: 4
- `source:route`: 4
- `rgc:code`: 4
- `rgc:bdbac`: 4
- `operator:ja`: 4
- `flood_prone`: 4
- `reservation:website`: 4
- `electrified`: 4
- `date`: 4
- `name:ja_rm`: 4
- `surf`: 4
- `source:old_name`: 4
- `is_in:country`: 4
- `from:fr`: 4
- `fee:date`: 4
- `natural`: 4
- `check_date:service_times`: 4
- `vhf`: 4
- `motor_vehicle:conditional`: 4
- `by_demand`: 4
- `supervised`: 4
- `contact:foursquare`: 4
- `contact:yelp`: 4
- `phone:AU`: 4
- `zone:HSL`: 4
- `gtfs_id`: 4
- `fare_gates`: 4
- `source:addr`: 4
- `disused:public_transport`: 4
- `addr:county`: 4
- `segregated`: 4
- `official_name:DB`: 4
- `roof:height`: 4
- `building:colour`: 4
- `ref:LOCODE`: 4
- `area:highway`: 4
- `payment:tamartag`: 3
- `toll:motorcar:backward`: 3
- `toll:motorcar:forward`: 3
- `toll:motorcycle:backward`: 3
- `toll:motorcycle:forward`: 3
- `surveillance`: 3
- `toll:dog`: 3
- `fee:url`: 3
- `vehicle:conditional`: 3
- `name_1`: 3
- `departures:check_date`: 3
- `abandoned:route`: 3
- `name:ga`: 3
- `cost`: 3
- `caravans`: 3
- `payment:notes`: 3
- `pets`: 3
- `heritage:operator`: 3
- `restriction`: 3
- `source:opening_hours`: 3
- `restrictions`: 3
- `hgv:signed`: 3
- `nat_ref`: 3
- `operator:phone`: 3
- `depth`: 3
- `minibus`: 3
- `dog:url`: 3
- `foot:url`: 3
- `motor_vehicle:url`: 3
- `trailer:url`: 3
- `designation`: 3
- `sac_scale`: 3
- `website:GB`: 3
- `website:NL`: 3
- `information`: 3
- `changing_table`: 3
- `charge:goods`: 3
- `charge:hgv`: 3
- `rgc:type`: 3
- `payment`: 3
- `ferry:interval`: 3
- `small_electric_vehicle`: 3
- `4wd_only`: 3
- `days`: 3
- `name:my`: 3
- `ref:CCC`: 3
- `maxgcweight`: 3
- `gauge`: 3
- `name:km`: 3
- `alt_name:zh-Hans`: 3
- `alt_name:zh-Hant`: 3
- `water_slide`: 3
- `source:id`: 3
- `source:imagery`: 3
- `claimed_by`: 3
- `controlled_by`: 3
- `capacity:disabled`: 3
- `seamark:source`: 3
- `auto_rickshaw`: 3
- `loc_name:zh`: 3
- `source:alt_name`: 3
- `is_in:municipality`: 3
- `is_in:state`: 3
- `import`: 3
- `addr:union`: 3
- `addr:ward`: 3
- `damage`: 3
- `name_disaster`: 3
- `currency:others`: 3
- `minspeed`: 3
- `proposed:route`: 3
- `source:website`: 3
- `tracks`: 3
- `whitewater`: 3
- `contact:telegram`: 3
- `currency`: 3
- `day`: 3
- `complete`: 3
- `int_ref:colour`: 3
- `building:min_level`: 3
- `disused:amenity`: 3
- `cargo:vehicle`: 3
- `seamark:building:function`: 3
- `nycdoitt:bin`: 3
- `gtfs_zone_id`: 3
- `government`: 3
- `office`: 3
- `alt_name:ko`: 3
- `embankment`: 3
- `source:toll:url`: 2
- `waterway:ukw_channel`: 2
- `opening_hours:conditional`: 2
- `hour_off`: 2
- `hour_on`: 2
- `name:boat`: 2
- `payment:cheque`: 2
- `disused:duration`: 2
- `disused:fee`: 2
- `disused:toll`: 2
- `access:disabled`: 2
- `note:bicycle`: 2
- `operator:es`: 2
- `headlight`: 2
- `toll:psv`: 2
- `vessel:mmsi`: 2
- `wikipedia:en`: 2
- `ref:VRN`: 2
- `alt_name:1`: 2
- `name:language`: 2
- `ship:type`: 2
- `opening_hours:signed`: 2
- `de:strassenschluessel_exists`: 2
- `payment:american_express`: 2
- `rcn`: 2
- `fee:person`: 2
- `contributor`: 2
- `nrn:ferrySegmentId`: 2
- `animal`: 2
- `cargo_bike`: 2
- `payment:visa_debit`: 2
- `maxweightrating`: 2
- `geobase:acquisitionTechnique`: 2
- `was:operator`: 2
- `bicycle:url`: 2
- `alt_name:ja-Hira`: 2
- `alt_name:ja-Latn`: 2
- `vending_machine`: 2
- `status`: 2
- `trail_visibility`: 2
- `ferry_infrastructure`: 2
- `payment:unionpay`: 2
- `catmp-RoadID`: 2
- `phone:mobile`: 2
- `maxbogieweight`: 2
- `note:ja`: 2
- `payment:cryptocurrencies`: 2
- `payment:electronic_purses`: 2
- `headway`: 2
- `maxaxles`: 2
- `opening_hours:note`: 2
- `river:waterway_distance`: 2
- `maxlength:variable`: 2
- `charge:trailer`: 2
- `capacity:foot`: 2
- `capacity:wheelchair`: 2
- `url:0`: 2
- `ref:KVV`: 2
- `cycleway`: 2
- `scooter`: 2
- `oversize`: 2
- `note_1`: 2
- `payment:cash:CZK`: 2
- `payment:cash:EUR`: 2
- `was:website`: 2
- `motorcar:conditional`: 2
- `ref:DK:cvr`: 2
- `footway`: 2
- `source:location`: 2
- `name:mt`: 2
- `vessel:en`: 2
- `to:fr`: 2
- `reservation:website:2`: 2
- `website_1`: 2
- `note_2`: 2
- `disused:ferry`: 2
- `cycle_rickshaw`: 2
- `electric_bicycle`: 2
- `date_off`: 2
- `date_on`: 2
- `loc_name:en`: 2
- `name:ln`: 2
- `NHS`: 2
- `name:mi`: 2
- `winter_service`: 2
- `from:zh-Hans`: 2
- `from:zh-Hant`: 2
- `to:zh-Hans`: 2
- `to:zh-Hant`: 2
- `min_height`: 2
- `fee:dog`: 2
- `name:fil`: 2
- `name:sju`: 2
- `image:access_sign`: 2
- `postal_code`: 2
- `imagery_used`: 2
- `note:duration`: 2
- `from_station_code`: 2
- `to_station_code`: 2
- `min_age`: 2
- `loc_name:zh-Hans`: 2
- `loc_name:zh-Hant`: 2
- `construction`: 2
- `duration:max`: 2
- `duration:min`: 2
- `contact:youtube`: 2
- `manufacturer`: 2
- `tunnel:name`: 2
- `feet`: 2
- `circular`: 2
- `old_name:en`: 2
- `old_name:ja`: 2
- `old_name:ja-Hira`: 2
- `old_name:ja-Latn`: 2
- `website:map`: 2
- `ferry:propulsion`: 2
- `maxspeed:navigation`: 2
- `loc_name:sv`: 2
- `bicycle:foldable`: 2
- `ferry_name`: 2
- `FIXME`: 2
- `osmc:symbol`: 2
- `symbol`: 2
- `currency:EUR`: 2
- `operator:email`: 2
- `ref:muoversi_in_Lombardia`: 2
- `gtfs:shape_id:SE-UL`: 2
- `operator:de`: 2
- `transit`: 2
- `passenger_information_display:speech_output`: 2
- `maaamet:ETAK`: 2
- `ref:crs`: 2
- `ref:national_rail`: 2
- `ref:CH-GE:CODE_VOIE`: 2
- `roof:material`: 2
- `addr:locality`: 2
- `architect`: 2
- `lacounty:ain`: 2
- `lacounty:bld_id`: 2
- `wall`: 2
- `name:zh-Latn-pinyin`: 2
- `building:levels:underground`: 2
- `seamark:restricted_area:restriction`: 2
- `ref:IFOPT`: 2
- `share_taxi`: 2
- `stilts`: 2
- `sanitary_dump_station`: 2
- `pier:ref`: 2
- `path`: 2
- `leisure`: 2
- `addr:region`: 2
- `name:tk`: 2
- `building:use`: 2
- `contact:email:visitor`: 2
- `contact:phone:ferry`: 2
- `contact:phone:visitor`: 2
- `harbour:information`: 2
- `mooring:max_length`: 2
- `seaway`: 2
- `station`: 2
- `cargo:bicycle`: 2
- `aeroway`: 2
- `official_name:en`: 2
- `official_name:zh`: 2
- `short_name:ko`: 2
- `roof:direction`: 2
- `opening_hours:lastcheck`: 1
- `toll:motor_vehicle`: 1
- `bb365:preferred`: 1
- `contact:webcam`: 1
- `ft_link`: 1
- `toilet`: 1
- `class:bicycle:roadcycling`: 1
- `charge:adult`: 1
- `charge:child`: 1
- `payment:apple_pay`: 1
- `payment:google_pay`: 1
- `toll:backward`: 1
- `capacity:motor_vehicle`: 1
- `capacity:cars`: 1
- `disused:ferry:cable`: 1
- `disused:ferry:type`: 1
- `disused:maxweight`: 1
- `name:frc`: 1
- `tiger:zip_left`: 1
- `tiger:zip_right`: 1
- `abandoned:electrified`: 1
- `abandoned:gauge`: 1
- `abandoned:railway`: 1
- `coach`: 1
- `seamark:cable_submarine:category`: 1
- `traffic_sign`: 1
- `maxdoubleaxleload`: 1
- `operating_hours`: 1
- `general`: 1
- `cables`: 1
- `source:operator`: 1
- `note:maxheight`: 1
- `note:maxweight`: 1
- `mhs:inscription_date`: 1
- `ref:FR:CAIRN`: 1
- `ref:mhs`: 1
- `source:heritage`: 1
- `fee:note`: 1
- `electricity`: 1
- `seats`: 1
- `maxweight:hgv`: 1
- `payment:swish`: 1
- `name:is`: 1
- `heritage:ref`: 1
- `heritage:since`: 1
- `ref:nrhp`: 1
- `tricycle`: 1
- `nat_name`: 1
- `vessel:name`: 1
- `description:hu`: 1
- `twitter`: 1
- `was:end_date`: 1
- `winter`: 1
- `email:brittany_ferries`: 1
- `website:brittany_ferries`: 1
- `website:stena_line`: 1
- `phone:DE`: 1
- `phone:PL`: 1
- `toll:moped`: 1
- `source:vehicle:conditional`: 1
- `toll:conditional`: 1
- `was:name`: 1
- `alt_name:ar`: 1
- `int_ref:ar`: 1
- `note:capacity`: 1
- `region`: 1
- `lines`: 1
- `amount`: 1
- `payment:discover_card`: 1
- `trailblazed`: 1
- `toll:amount`: 1
- `source:fee`: 1
- `schedule`: 1
- `was:brand:en`: 1
- `was:name:en`: 1
- `was:operator:en`: 1
- `was:operator:zh`: 1
- `was:owner`: 1
- `was:owner:en`: 1
- `was:owner:zh`: 1
- `to:yue`: 1
- `to:yue-Hant`: 1
- `to:yue-Latn`: 1
- `to:yue-Latn-jyutping`: 1
- `via:yue`: 1
- `via:yue-Hant`: 1
- `via:yue-Latn`: 1
- `via:yue-Latn-jyutping`: 1
- `toilets:disabled`: 1
- `class:bicycle`: 1
- `source:topology`: 1
- `canvec:UUID`: 1
- `website:opening_hours`: 1
- `fee:return`: 1
- `is_in:city`: 1
- `phone:IE`: 1
- `phone:UK`: 1
- `summer:opening_hours`: 1
- `open`: 1
- `note:no`: 1
- `route_1`: 1
- `headway:peak`: 1
- `route:cable`: 1
- `max_draft`: 1
- `fare`: 1
- `name:bs`: 1
- `source:int_ref`: 1
- `website:hgv`: 1
- `route:preserved`: 1
- `ref:old`: 1
- `official_name:fi`: 1
- `official_name:sv`: 1
- `toilets:handwashing`: 1
- `noref`: 1
- `source:capacity`: 1
- `note:highway`: 1
- `marittimo`: 1
- `charge:coach`: 1
- `charge:minibus`: 1
- `toll:foot:backward`: 1
- `payment:v_pay`: 1
- `charge:motor_vehicle`: 1
- `charge:motor_vehicle:conditional`: 1
- `internet_1`: 1
- `internet_2`: 1
- `bdbac:ref`: 1
- `motor_vehicle:backward:conditional`: 1
- `private`: 1
- `obstacle`: 1
- `ncn`: 1
- `historic:route`: 1
- `country`: 1
- `charge:goods:conditional`: 1
- `charge:trailer:conditional`: 1
- `check_date:fee`: 1
- `source:maxaxleload:url`: 1
- `capacity:vehicle`: 1
- `seamark:period_end`: 1
- `seamark:period_start`: 1
- `interval:backward`: 1
- `interval:forward`: 1
- `source:motor_vehicle`: 1
- `ferry:operator`: 1
- `alt_name:pt`: 1
- `unsigned_ref`: 1
- `alt_name:de`: 1
- `alt_name:pl`: 1
- `alt_ref`: 1
- `structure`: 1
- `check_date:charge`: 1
- `cutting`: 1
- `trail`: 1
- `name:ckb`: 1
- `food`: 1
- `name:ber`: 1
- `name:kab`: 1
- `toll:SG_plate_vehicle`: 1
- `toll:motorcycle`: 1
- `temporary`: 1
- `website:1`: 1
- `website:2`: 1
- `phone:NZ`: 1
- `check:date`: 1
- `website:ja`: 1
- `removed:ferry:type`: 1
- `sidewalk`: 1
- `name:ko-Hani`: 1
- `rail`: 1
- `min_pers`: 1
- `mechanical`: 1
- `description:fr`: 1
- `source:hgv`: 1
- `charge:auto_rickshaw`: 1
- `charge:cycle_rickshaw`: 1
- `charge:electric_bicycle`: 1
- `charge:moped`: 1
- `hgv_1`: 1
- `operator2`: 1
- `strapline`: 1
- `strapline:zh`: 1
- `recognised_by`: 1
- `description:charge`: 1
- `pet`: 1
- `contact:phone1`: 1
- `route:conditional`: 1
- `reservation:foot`: 1
- `operator:zh-Hans`: 1
- `operator:zh-Hant`: 1
- `cow`: 1
- `note:es`: 1
- `alt_name:full`: 1
- `to:ja-Jpan`: 1
- `class:bicycle:commute`: 1
- `comment:de`: 1
- `comment:en`: 1
- `remark`: 1
- `charge:agricultural`: 1
- `charge:excavator`: 1
- `snowmobile`: 1
- `drnpu:criteria`: 1
- `source:ref`: 1
- `importance`: 1
- `survey_date`: 1
- `name:rmy`: 1
- `source:CEMT`: 1
- `has_riverbank`: 1
- `currency:DOP`: 1
- `name:or`: 1
- `duration:note`: 1
- `day_off`: 1
- `day_on`: 1
- `from:pt`: 1
- `to:pt`: 1
- `motorbike`: 1
- `time_off`: 1
- `time_on`: 1
- `name:kk`: 1
- `incline`: 1
- `construction_date`: 1
- `nickname`: 1
- `note:dog`: 1
- `freight`: 1
- `bike_ride`: 1
- `canal`: 1
- `isced:level`: 1
- `school:type_idn`: 1
- `note:2022`: 1
- `description:es`: 1
- `access:temporary`: 1
- `bicycle:signed`: 1
- `booking`: 1
- `project`: 1
- `network:type`: 1
- `planned:ferry`: 1
- `planned:motor_vehicle`: 1
- `operator:official_name:en`: 1
- `operator:official_name:zh`: 1
- `operator:ref`: 1
- `operator:short:en`: 1
- `operator:short:zh`: 1
- `operator:short_name`: 1
- `operator:short_name:en`: 1
- `operator:short_name:zh`: 1
- `roller_coaster:track`: 1
- `description:it`: 1
- `tunnel:name:en`: 1
- `operator:addr:city`: 1
- `operator:addr:housenumber`: 1
- `operator:addr:postcode`: 1
- `operator:addr:street`: 1
- `fee:disabled`: 1
- `capacity:motorcycle`: 1
- `capacity:rickshaw`: 1
- `charge:vehicle:conditional`: 1
- `rickshaw`: 1
- `old_official_name`: 1
- `gnis:ST_alpha`: 1
- `gnis:county_name`: 1
- `gnis:created`: 1
- `gnis:feature_type`: 1
- `link`: 1
- `name:en-Dsrt`: 1
- `note:reservation`: 1
- `dog:conditional`: 1
- `pets:conditional`: 1
- `website:zh`: 1
- `propulsion`: 1
- `locked`: 1
- `seamark:notice:category`: 1
- `seamark:notice:function`: 1
- `seamark:notice:information`: 1
- `seamark:notice:system`: 1
- `crossing`: 1
- `toll:description`: 1
- `cat`: 1
- `related:wikipedia`: 1
- `boat:tours`: 1
- `subject:website`: 1
- `microcar`: 1
- `currency:CZK`: 1
- `condition`: 1
- `operator:ferry`: 1
- `operator:route`: 1
- `not:network`: 1
- `proposed:type`: 1
- `payment:account_cards`: 1
- `section`: 1
- `from:da`: 1
- `from:sv`: 1
- `note:1`: 1
- `name:etymology:wikidata`: 1
- `group_only`: 1
- `to:da`: 1
- `to:sv`: 1
- `route:type`: 1
- `hand_cart`: 1
- `payment:pix`: 1
- `truck`: 1
- `note_3`: 1
- `contact:address`: 1
- `ref:network`: 1
- `name:gcf`: 1
- `contact:vk`: 1
- `access:note`: 1
- `toilets:conditional`: 1
- `wheelchair:conditional`: 1
- `addr:floor`: 1
- `cycleway:both`: 1
- `ref:nz:heritage`: 1
- `IPP:CodEdificio`: 1
- `IPP:CodLogradouro`: 1
- `capacity:weight`: 1
- `goods:lanes`: 1
- `hgv:lanes`: 1
- `motorcar:lanes`: 1
- `fhrs:id`: 1
- `disused:seamark:type`: 1
- `left:province`: 1
- `right:province`: 1
- `ref:LV:addr`: 1
- `architect:wikidata`: 1
- `vending`: 1
- `platform`: 1
- `name:cnr`: 1
- `name:cnr-Latn`: 1
- `cargo:hgv`: 1
- `addr2:street`: 1
- `reg_name`: 1
- `manufacturer:wikidata`: 1
- `addr:suburb:en`: 1
- `addr:suburb:zh`: 1
- `cruise_ship`: 1
- `internet_access:website`: 1
- `kerb`: 1
- `kerb:approach_aid`: 1
- `oneway:bicycle`: 1
- `permit`: 1
- `roof:orientation`: 1
- `check_date:bin`: 1
- `naptan:AtcoCode`: 1
- `routing:motor_vehicle`: 1
- `pole`: 1
- `semaphore`: 1
- `subject:wikidata`: 1
- `subject:wikipedia`: 1
- `gtfs_stop_code`: 1
- `inscription`: 1
- `overtaking`: 1
- `language:ar`: 1
- `gtfs:stop_code`: 1
- `ref:linz:address_id`: 1
- `latitude`: 1
- `longitude`: 1
- `source:position`: 1
- `pmfsefin:idedif`: 1
- `was:amenity`: 1
- `architect:wikipedia`: 1
- `railway:name:DB`: 1
- `power_supply`: 1
- `sorting_name`: 1
- `sorting_name:ar`: 1
- `sorting_name:es`: 1
- `dock`: 1
- `public_transit`: 1
- `ref:findr`: 1
- `fence_type`: 1
- `full_name`: 1
- `internet_access:operator`: 1
- `source:height`: 1
- `port_of_entry`: 1
- `name:ta`: 1
- `province`: 1
- `iata`: 1
- `short_name:zh-Hans`: 1
- `short_name:zh-Hant`: 1
- `port`: 1
- `old_operator:wikidata`: 1
- `old_operator:wikipedia`: 1
- `seamark:berth:name`: 1
- `addr:barangay`: 1
- `addr:city:simc`: 1
- `building:facade:colour`: 1
- `golf`: 1
- `narrow`: 1
- `opendata:type`: 1
- `indoor_seating`: 1
- `outdoor_seating`: 1
- `contact:whatsapp`: 1
- `levels`: 1
- `canal_boat`: 1
- `roof`: 1
- `camera:mount`: 1
- `camera:type`: 1
- `surveillance:type`: 1
- `harbour_master`: 1
- `boat:conditional`: 1
- `ferry:conditional`: 1
- `reservation:boat`: 1
- `reservation:ship`: 1
- `ship:conditional`: 1
- `waypoint`: 1
- `proposed`: 1

### Value distributions (selected keys)

#### `motor_vehicle`

- `no`: 8801
- `yes`: 4307
- `private`: 31
- `permissive`: 22
- `designated`: 15
- `motorcycle`: 11
- `permit`: 7
- `destination`: 6
- `customers`: 3
- `delivery`: 2
- `unknown`: 2
- `agricultural`: 1

#### `motorcar`

- `no`: 1294
- `yes`: 1140
- `permissive`: 8
- `private`: 5
- `delivery`: 1
- `designated`: 1

#### `vehicle`

- `no`: 563
- `yes`: 270
- `customers`: 10
- `private`: 2
- `destination`: 2
- `permissive`: 1

#### `hgv`

- `no`: 1502
- `yes`: 475
- `private`: 4
- `permissive`: 3
- `discouraged`: 1
- `designated`: 1
- `unknown`: 1

#### `access`

- `yes`: 306
- `no`: 294
- `customers`: 221
- `private`: 184
- `permissive`: 86
- `permit`: 51
- `destination`: 24
- `unknown`: 12
- `agricultural`: 4
- `forestry`: 2
- `residents`: 1
- `foot`: 1
- … +1 more distinct values

#### `foot`

- `yes`: 12887
- `no`: 245
- `designated`: 120
- `customers`: 47
- `permissive`: 32
- `private`: 26
- `permit`: 7
- `destination`: 5
- `use_sidepath`: 3
- `official`: 2
- `only`: 1
- `unknown`: 1

#### `bicycle`

- `yes`: 7267
- `no`: 1004
- `dismount`: 189
- `permissive`: 132
- `customers`: 52
- `limited`: 42
- `designated`: 28
- `permit`: 24
- `private`: 10
- `unknown`: 8
- `use_sidepath`: 4
- `destination`: 3
- … +1 more distinct values

#### `ferry`

- `yes`: 3664
- `tertiary`: 757
- `footway`: 620
- `unclassified`: 617
- `secondary`: 591
- `primary`: 382
- `path`: 221
- `trunk`: 197
- `tourist`: 118
- `local`: 75
- `service`: 42
- `track`: 39
- … +46 more distinct values

#### `duration`

- `00:10`: 358
- `00:05`: 355
- `00:15`: 333
- `00:20`: 331
- `00:30`: 306
- `01:00`: 242
- `00:45`: 181
- `00:25`: 165
- `01:30`: 144
- `00:40`: 133
- `02:00`: 105
- `00:35`: 98
- … +514 more distinct values

#### `interval`

- `01:00`: 65
- `30`: 50
- `00:15`: 43
- `00:30`: 42
- `00:10`: 37
- `00:20`: 35
- `24:00`: 35
- `02:00`: 30
- `20`: 27
- `60`: 27
- `15`: 19
- `01:00:00`: 15
- … +113 more distinct values

#### `opening_hours`

- `24/7`: 157
- `may 18-oct 06`: 57
- `Apr-Oct`: 56
- `oct 07-nov 03;mar 08-mar 31`: 26
- `09:00-23:00`: 16
- `Jan-Dec 00:00-24:00`: 14
- `Jul-Sep`: 14
- `Oct-May off`: 12
- `May-Sep`: 11
- `Apr 26-Oct 06: "season"; Mar 29-Apr 25: "off-season"; Oct 07-27: "off-season"`: 10
- `Mo-Fr`: 9
- `sunrise-sunset`: 8
- … +1202 more distinct values

#### `seasonal`

- `yes`: 277
- `summer`: 110
- `spring;summer;autumn`: 47
- `no`: 8
- `wet_season`: 5
- `April-October`: 4
- `summer/autumn`: 4
- `winter`: 4
- `dry_season`: 3
- `summer;autumn`: 2
- `Apr-Oct`: 2
- `Mar-Oct`: 1
- … +4 more distinct values

#### `toll`

- `yes`: 3072
- `no`: 586
- `200`: 1

#### `fee`

- `yes`: 2651
- `no`: 517
- `motor vehicles`: 3
- `25 HTG`: 3
- `donation`: 2
- `250 THB`: 2
- `12000 UGX`: 2
- `0.50 euro`: 2
- `3`: 2
- `20000 UGX`: 2
- `Free (Epcot park admission required) `: 2
- `adult:3.00 €;children: 2.00 €;bicycle: 2.00 €`: 2
- … +40 more distinct values

#### `maxspeed`

- `30`: 21
- `40 mph`: 18
- `5`: 16
- `80`: 11
- `20`: 7
- `50`: 3
- `10`: 3
- `30 mph`: 3
- `12`: 2
- `5 mph`: 1
- `10 mph`: 1
- `2`: 1
- … +3 more distinct values

#### `operator`

- `ASDP`: 174
- `Compagnie générale de navigation sur le lac Léman (CGN)`: 140
- `Pelni`: 104
- `BC Ferries`: 102
- `NLG`: 99
- `Transdev Sydney Ferries`: 92
- `RiverCity Ferries`: 83
- `Actv`: 81
- `SGV`: 78
- `Caledonian MacBrayne`: 72
- `SNL`: 72
- `Boreal Sjø AS`: 70
- … +2629 more distinct values

#### `name`

- `Congo`: 58
- `Byøyene`: 41
- `Färja 4: Stockholm - Vaxholm - Ramsösund - Rindö`: 36
- `Waxholmsbolaget`: 35
- `Willem Barentszkade`: 34
- `เรือด่วนเจ้าพระยา`: 32
- `Hurtigruten`: 29
- `Vía fluvial del Río Magdalena`: 25
- `Houtskär rutt`: 25
- `Vía fluvial del Rio Magdalena`: 22
- `Bydgoski Tramwaj Wodny`: 20
- `Södra Linjen`: 18
- … +17071 more distinct values

## 2. Per-region ferry table

Shapes: 541 with `.poly`, 5 bbox-only, 0 missing geometry. Ferries in no region: **4861** (scratch `out/ferries_no_region.json`).

| bake_id | in weekly | composite | route=ferry | car | pax/bike | unknown | pack ferry edges | missing OSM car | no_road | tiny | wrong pax |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| north_america_us |  |  | 21134 | 2500 | 18413 | 221 |  |  |  |  |  |
| asia_sea |  | Y | 9230 | 559 | 8650 | 21 |  |  |  |  |  |
| europe_norway_vestlandet | Y |  | 765 | 221 | 543 | 1 | 487 | 2 | 63 | 11 | 6 |
| europe_dach |  | Y | 1722 | 190 | 1500 | 32 |  |  |  |  |  |
| europe_greece |  |  | 575 | 145 | 401 | 29 | 286 | 2 | 20 | 2 | 0 |
| south_america_brazil_norte |  |  | 321 | 139 | 182 | 0 | 580 | 5 | 92 | 21 | 0 |
| europe_britain_and_ireland |  | Y | 521 | 131 | 379 | 11 |  |  |  |  |  |
| europe_norway_nord_norge | Y |  | 678 | 123 | 553 | 2 | 260 | 0 | 13 | 11 | 2 |
| asia_philippines |  |  | 1131 | 118 | 1010 | 3 | 244 | 2 | 17 | 3 | 0 |
| europe_great_britain |  | Y | 427 | 113 | 303 | 11 |  |  |  |  |  |
| south_america_brazil_sul |  |  | 272 | 104 | 161 | 7 | 194 | 7 | 5 | 3 | 0 |
| asia_indonesia_sulawesi |  |  | 199 | 92 | 105 | 2 | 162 | 11 | 22 | 2 | 0 |
| europe_finland |  |  | 622 | 92 | 526 | 4 | 194 | 4 | 25 | 4 | 0 |
| asia_south_korea |  |  | 358 | 80 | 278 | 0 | 158 | 3 | 19 | 29 | 0 |
| europe_denmark |  |  | 163 | 77 | 81 | 5 | 180 | 1 | 44 | 2 | 2 |
| south_america_brazil_sudeste |  |  | 228 | 70 | 153 | 5 | 130 | 5 | 0 | 8 | 0 |
| asia_indonesia_maluku |  |  | 142 | 67 | 75 | 0 | 134 | 0 | 18 | 1 | 0 |
| north_america_us_west |  |  | 192 | 61 | 126 | 5 |  |  |  |  |  |
| south_america_chile |  |  | 378 | 60 | 314 | 4 | 124 | 0 | 6 | 10 | 0 |
| russia_siberian_fed_district |  |  | 213 | 59 | 153 | 1 |  |  |  |  |  |
| europe_united_kingdom_scotland |  |  | 138 | 58 | 79 | 1 | 114 | 1 | 4 | 1 | 0 |
| asia_indonesia_nusa_tenggara |  |  | 148 | 55 | 93 | 0 | 104 | 4 | 8 | 2 | 0 |
| asia_indonesia_kalimantan |  |  | 242 | 53 | 187 | 2 | 104 | 1 | 18 | 1 | 0 |
| asia_vietnam | Y |  | 447 | 51 | 388 | 8 | 102 | 0 | 0 | 6 | 0 |
| north_america_us_washington |  |  | 87 | 51 | 34 | 2 | 106 | 0 | 15 | 9 | 0 |
| asia_indonesia_sumatra |  |  | 357 | 50 | 307 | 0 | 72 | 14 | 14 | 5 | 0 |
| north_america_canada_quebec |  |  | 102 | 45 | 45 | 12 | 82 | 7 | 10 | 3 | 0 |
| north_america_us_south |  |  | 238 | 45 | 183 | 10 |  |  |  |  |  |
| asia_indonesia_papua |  |  | 111 | 44 | 67 | 0 | 88 | 0 | 12 | 6 | 0 |
| europe_germany_niedersachsen |  |  | 189 | 44 | 143 | 2 | 92 | 1 | 22 | 3 | 6 |
| europe_turkey |  |  | 234 | 44 | 184 | 6 | 96 | 2 | 27 | 1 | 0 |
| north_america_us_northeast |  |  | 301 | 42 | 256 | 3 |  |  |  |  |  |
| europe_alps |  | Y | 943 | 41 | 901 | 1 |  |  |  |  |  |
| north_america_canada_british_columbia_island_admreg |  |  | 81 | 39 | 40 | 2 | 78 | 0 | 12 | 2 | 0 |
| south_america_brazil_centro_oeste |  |  | 85 | 39 | 45 | 1 | 76 | 1 | 2 | 7 | 0 |
| asia_japan_chugoku |  |  | 203 | 38 | 165 | 0 | 76 | 1 | 12 | 2 | 2 |
| europe_germany_schleswig_holstein |  |  | 117 | 38 | 73 | 6 | 82 | 3 | 11 | 1 | 0 |
| europe_croatia |  |  | 197 | 37 | 159 | 1 | 90 | 1 | 14 | 4 | 0 |
| south_america_brazil_nordeste |  |  | 200 | 37 | 161 | 2 | 74 | 1 | 0 | 1 | 0 |
| asia_japan_shikoku |  |  | 164 | 36 | 128 | 0 | 74 | 0 | 14 | 9 | 0 |
| asia_indonesia_java |  |  | 175 | 33 | 141 | 1 | 54 | 7 | 14 | 0 | 0 |
| africa_egypt | Y |  | 81 | 32 | 49 | 0 | 64 | 0 | 5 | 2 | 0 |
| europe_italy_isole |  |  | 101 | 31 | 69 | 1 | 58 | 2 | 20 | 0 | 0 |
| asia_india_southern_zone |  |  | 335 | 29 | 306 | 0 | 64 | 1 | 18 | 5 | 2 |
| europe_sweden_stockholm |  |  | 442 | 29 | 413 | 0 | 58 | 0 | 16 | 0 | 0 |
| asia_china_anhui |  |  | 101 | 28 | 73 | 0 | 55 | 0 | 0 | 3 | 0 |
| europe_ireland_and_northern_ireland |  | Y | 100 | 28 | 70 | 2 |  |  |  |  |  |
| north_america_us_midwest |  |  | 115 | 28 | 82 | 5 |  |  |  |  |  |
| russia_volga_fed_district |  |  | 131 | 27 | 102 | 2 |  |  |  |  |  |
| europe_albania |  |  | 36 | 26 | 10 | 0 | 56 | 0 | 22 | 0 | 0 |
| australia_oceania_australia_queensland |  |  | 131 | 25 | 106 | 0 | 50 | 0 | 6 | 2 | 0 |
| north_america_canada_british_columbia_southcoast_admreg |  |  | 55 | 25 | 29 | 1 | 54 | 0 | 18 | 1 | 0 |
| australia_oceania_new_zealand |  |  | 152 | 24 | 127 | 1 | 52 | 0 | 10 | 2 | 0 |
| europe_france_corse |  |  | 36 | 24 | 12 | 0 | 48 | 0 | 17 | 0 | 0 |
| europe_hungary |  |  | 85 | 24 | 60 | 1 | 50 | 0 | 1 | 0 | 0 |
| europe_italy_centro |  |  | 69 | 24 | 45 | 0 | 36 | 6 | 13 | 0 | 0 |
| asia_thailand | Y |  | 411 | 21 | 389 | 1 | 42 | 0 | 2 | 1 | 0 |
| europe_italy_nord_ovest |  |  | 336 | 21 | 314 | 1 | 60 | 0 | 17 | 0 | 6 |
| europe_netherlands_gelderland |  |  | 75 | 21 | 53 | 1 | 42 | 0 | 3 | 12 | 0 |
| russia_ural_fed_district |  |  | 76 | 21 | 55 | 0 |  |  |  |  |  |
| south_america_argentina |  |  | 133 | 21 | 108 | 4 | 42 | 0 | 5 | 6 | 0 |
| north_america_canada_newfoundland_and_labrador |  |  | 35 | 20 | 15 | 0 | 48 | 0 | 6 | 3 | 0 |
| north_america_canada_ontario |  |  | 46 | 20 | 26 | 0 | 32 | 4 | 3 | 5 | 0 |
| europe_germany_rheinland_pfalz |  |  | 75 | 19 | 55 | 1 | 38 | 0 | 1 | 7 | 0 |
| europe_sweden_vastra_gotaland |  |  | 151 | 19 | 130 | 2 | 42 | 0 | 5 | 0 | 2 |
| north_america_us_alaska |  |  | 65 | 19 | 45 | 1 | 38 | 0 | 1 | 3 | 0 |
| north_america_us_pacific |  |  | 67 | 19 | 47 | 1 |  |  |  |  |  |
| europe_estonia |  |  | 41 | 18 | 21 | 2 | 42 | 0 | 12 | 0 | 0 |
| europe_france_provence_alpes_cote_d_azur |  |  | 90 | 18 | 72 | 0 | 34 | 1 | 11 | 0 | 0 |
| europe_germany_sachsen_anhalt |  |  | 31 | 17 | 13 | 1 | 34 | 0 | 0 | 0 | 0 |
| europe_norway_trondelag | Y |  | 60 | 17 | 43 | 0 | 36 | 1 | 6 | 1 | 0 |
| north_america_us_maine |  |  | 47 | 17 | 30 | 0 | 40 | 0 | 6 | 3 | 2 |
| australia_oceania_australia_new_south_wales |  |  | 184 | 16 | 166 | 2 | 32 | 0 | 0 | 3 | 0 |
| europe_germany_mecklenburg_vorpommern |  |  | 129 | 16 | 108 | 5 | 32 | 0 | 17 | 0 | 0 |
| russia_central_fed_district |  |  | 98 | 16 | 80 | 2 |  |  |  |  |  |
| africa_congo_democratic_republic |  |  | 270 | 15 | 252 | 3 | 30 | 0 | 3 | 1 | 0 |
| asia_japan_hokkaido |  |  | 24 | 15 | 9 | 0 | 78 | 0 | 30 | 0 | 0 |
| europe_italy_sud |  |  | 90 | 15 | 73 | 2 | 44 | 0 | 19 | 0 | 0 |
| europe_portugal |  |  | 70 | 15 | 54 | 1 | 30 | 0 | 2 | 0 | 0 |
| asia_malaysia_singapore_brunei | Y |  | 221 | 14 | 205 | 2 | 30 | 0 | 8 | 0 | 0 |
| central_america_bahamas |  |  | 30 | 14 | 16 | 0 | 28 | 0 | 0 | 1 | 0 |
| south_america_colombia |  |  | 278 | 14 | 263 | 1 | 32 | 0 | 2 | 2 | 2 |
| africa_madagascar |  |  | 558 | 13 | 545 | 0 | 26 | 0 | 1 | 4 | 0 |
| africa_morocco |  |  | 23 | 13 | 10 | 0 | 34 | 2 | 12 | 0 | 0 |
| africa_senegal_and_gambia |  |  | 181 | 13 | 168 | 0 | 26 | 0 | 0 | 4 | 0 |
| australia_oceania_australia_south_australia |  |  | 13 | 13 | 0 | 0 | 26 | 0 | 0 | 0 | 0 |
| europe_germany_hessen |  |  | 28 | 13 | 15 | 0 | 22 | 2 | 0 | 2 | 0 |
| europe_netherlands_noord_brabant |  |  | 59 | 13 | 45 | 1 | 26 | 0 | 4 | 7 | 0 |
| europe_romania |  |  | 46 | 13 | 33 | 0 | 24 | 1 | 1 | 1 | 0 |
| north_america_canada_british_columbia_okanagan_admreg |  |  | 21 | 13 | 7 | 1 | 26 | 0 | 10 | 0 | 0 |
| north_america_canada_new_brunswick |  |  | 17 | 13 | 3 | 1 | 26 | 0 | 1 | 1 | 0 |
| north_america_canada_saskatchewan |  |  | 16 | 13 | 3 | 0 | 26 | 0 | 0 | 0 | 0 |
| north_america_us_new_york |  |  | 149 | 13 | 133 | 3 | 24 | 1 | 6 | 5 | 0 |
| south_america_bolivia |  |  | 115 | 13 | 102 | 0 | 22 | 2 | 3 | 1 | 0 |
| asia_gcc_states |  |  | 56 | 12 | 43 | 1 | 20 | 2 | 3 | 0 | 0 |
| asia_japan_tohoku |  |  | 38 | 12 | 26 | 0 | 32 | 1 | 15 | 0 | 0 |
| europe_netherlands_noord_holland |  |  | 110 | 12 | 90 | 8 | 24 | 0 | 1 | 2 | 0 |
| north_america_canada_nova_scotia |  |  | 20 | 12 | 8 | 0 | 22 | 1 | 7 | 0 | 0 |
| north_america_us_michigan |  |  | 43 | 12 | 30 | 1 | 24 | 0 | 4 | 0 | 0 |
| europe_france_bretagne |  |  | 79 | 11 | 66 | 2 | 18 | 2 | 5 | 0 | 0 |
| europe_france_nord_pas_de_calais |  |  | 23 | 11 | 12 | 0 | 62 | 0 | 24 | 0 | 14 |
| europe_netherlands_zuid_holland |  |  | 150 | 11 | 130 | 9 | 22 | 0 | 5 | 0 | 0 |
| europe_norway_ostlandet | Y |  | 166 | 11 | 155 | 0 | 24 | 0 | 5 | 1 | 0 |
| europe_spain_andalucia |  |  | 36 | 11 | 25 | 0 | 34 | 4 | 12 | 0 | 0 |
| europe_spain_islas_baleares |  |  | 35 | 11 | 24 | 0 | 22 | 0 | 7 | 0 | 0 |
| europe_sweden_skane |  |  | 29 | 11 | 17 | 1 | 26 | 0 | 10 | 0 | 2 |
| europe_united_kingdom_england_cornwall |  |  | 47 | 11 | 36 | 0 | 30 | 0 | 13 | 0 | 0 |
| north_america_us_north_carolina |  |  | 26 | 11 | 15 | 0 | 32 | 0 | 4 | 0 | 0 |
| south_america_paraguay |  |  | 40 | 11 | 29 | 0 | 22 | 0 | 0 | 1 | 0 |
| asia_china_guangdong |  |  | 342 | 10 | 327 | 5 | 24 | 1 | 4 | 1 | 0 |
| europe_france_haute_normandie |  |  | 12 | 10 | 2 | 0 | 20 | 0 | 1 | 0 | 0 |
| europe_italy_nord_est |  |  | 253 | 10 | 242 | 1 | 20 | 0 | 8 | 0 | 0 |
| europe_poland_zachodniopomorskie |  |  | 29 | 10 | 16 | 3 | 22 | 1 | 6 | 1 | 0 |
| asia_china_hunan |  |  | 83 | 9 | 74 | 0 | 18 | 0 | 0 | 0 | 0 |
| europe_austria |  |  | 157 | 9 | 148 | 0 | 16 | 1 | 1 | 1 | 0 |
| europe_faroe_islands |  |  | 11 | 9 | 2 | 0 | 18 | 0 | 2 | 1 | 0 |
| europe_germany_baden_wuerttemberg_karlsruhe_regbez |  |  | 11 | 9 | 1 | 1 | 18 | 0 | 1 | 1 | 0 |
| europe_germany_brandenburg |  |  | 66 | 9 | 57 | 0 | 16 | 1 | 1 | 2 | 0 |
| europe_netherlands_limburg |  |  | 23 | 9 | 13 | 1 | 18 | 0 | 0 | 1 | 0 |
| europe_united_kingdom_england_hampshire |  |  | 22 | 9 | 13 | 0 | 18 | 0 | 9 | 0 | 0 |
| north_america_canada_british_columbia_interior_admreg |  |  | 16 | 9 | 7 | 0 | 18 | 0 | 3 | 0 | 0 |
| asia_india_western_zone |  |  | 67 | 8 | 59 | 0 | 16 | 0 | 1 | 1 | 0 |
| europe_azores |  |  | 11 | 8 | 3 | 0 | 16 | 0 | 0 | 0 | 0 |
| europe_belarus |  |  | 39 | 8 | 29 | 2 | 16 | 0 | 0 | 0 | 0 |
| europe_sweden_blekinge |  |  | 28 | 8 | 20 | 0 | 20 | 1 | 6 | 0 | 0 |
| europe_ukraine |  |  | 113 | 8 | 101 | 4 | 18 | 0 | 3 | 0 | 0 |
| north_america_canada_british_columbia_north_admreg |  |  | 23 | 8 | 14 | 1 | 16 | 0 | 2 | 2 | 0 |
| north_america_us_florida | Y |  | 90 | 8 | 80 | 2 | 20 | 0 | 8 | 0 | 0 |
| asia_china_gansu |  |  | 22 | 7 | 15 | 0 | 14 | 0 | 0 | 3 | 0 |
| asia_china_guangxi |  |  | 134 | 7 | 127 | 0 | 14 | 0 | 1 | 2 | 0 |
| asia_china_hainan |  |  | 34 | 7 | 27 | 0 | 26 | 0 | 7 | 0 | 0 |
| asia_china_shanghai |  |  | 37 | 7 | 30 | 0 | 14 | 0 | 2 | 0 | 0 |
| asia_japan_chubu |  |  | 53 | 7 | 46 | 0 | 16 | 0 | 8 | 0 | 0 |
| asia_japan_kyushu |  |  | 227 | 7 | 219 | 1 | 22 | 0 | 5 | 0 | 4 |
| asia_laos |  |  | 130 | 7 | 120 | 3 | 14 | 0 | 0 | 0 | 0 |
| asia_taiwan |  |  | 92 | 7 | 82 | 3 | 11 | 1 | 1 | 0 | 0 |
| europe_germany_bayern_niederbayern |  |  | 27 | 7 | 20 | 0 | 14 | 1 | 1 | 1 | 0 |
| europe_germany_bayern_unterfranken |  |  | 11 | 7 | 4 | 0 | 14 | 0 | 0 | 2 | 0 |
| europe_guernsey_jersey |  |  | 17 | 7 | 10 | 0 | 16 | 0 | 4 | 0 | 0 |
| europe_lithuania |  |  | 23 | 7 | 14 | 2 | 16 | 0 | 5 | 0 | 0 |
| europe_netherlands_utrecht |  |  | 47 | 7 | 39 | 1 | 14 | 0 | 2 | 3 | 0 |
| europe_spain_cataluna |  |  | 56 | 7 | 44 | 5 | 14 | 0 | 8 | 0 | 0 |
| europe_united_kingdom_england_devon |  |  | 50 | 7 | 43 | 0 | 14 | 0 | 5 | 0 | 0 |
| europe_united_kingdom_england_kent |  |  | 12 | 7 | 5 | 0 | 20 | 0 | 7 | 0 | 0 |
| south_america_venezuela |  |  | 67 | 7 | 58 | 2 | 13 | 0 | 3 | 0 | 0 |
| asia_cambodia | Y |  | 113 | 6 | 107 | 0 | 12 | 0 | 0 | 0 | 0 |
| asia_china_chongqing |  |  | 53 | 6 | 45 | 2 | 12 | 0 | 1 | 0 | 0 |
| asia_china_sichuan |  |  | 86 | 6 | 80 | 0 | 12 | 0 | 0 | 0 | 0 |
| asia_japan_kansai |  |  | 78 | 6 | 72 | 0 | 14 | 0 | 10 | 0 | 0 |
| europe_iceland |  |  | 28 | 6 | 22 | 0 | 14 | 0 | 3 | 1 | 0 |
| north_america_canada_alberta |  |  | 13 | 6 | 7 | 0 | 12 | 0 | 0 | 0 | 0 |
| north_america_us_connecticut |  |  | 16 | 6 | 10 | 0 | 14 | 0 | 4 | 0 | 0 |
| north_america_us_ohio |  |  | 18 | 6 | 12 | 0 | 12 | 0 | 2 | 0 | 0 |
| north_america_us_virginia |  |  | 25 | 6 | 18 | 1 | 10 | 0 | 2 | 0 | 0 |
| north_america_us_wisconsin |  |  | 26 | 6 | 20 | 0 | 10 | 1 | 2 | 1 | 0 |
| asia_china_jiangsu |  |  | 75 | 5 | 69 | 1 | 10 | 0 | 1 | 0 | 0 |
| asia_china_shandong | Y |  | 70 | 5 | 63 | 2 | 10 | 0 | 1 | 0 | 0 |
| australia_oceania_australia_tasmania |  |  | 21 | 5 | 16 | 0 | 10 | 0 | 1 | 0 | 0 |
| australia_oceania_australia_victoria |  |  | 16 | 5 | 11 | 0 | 10 | 0 | 1 | 2 | 0 |
| europe_france_basse_normandie |  |  | 15 | 5 | 10 | 0 | 10 | 0 | 5 | 2 | 0 |
| europe_france_pays_de_la_loire |  |  | 51 | 5 | 44 | 2 | 10 | 0 | 0 | 0 | 0 |
| europe_germany_nordrhein_westfalen_koeln_regbez |  |  | 14 | 5 | 8 | 1 | 10 | 0 | 1 | 0 | 0 |
| europe_germany_sachsen |  |  | 60 | 5 | 54 | 1 | 10 | 0 | 3 | 0 | 0 |
| europe_isle_of_man |  |  | 5 | 5 | 0 | 0 | 8 | 1 | 4 | 0 | 0 |
| europe_netherlands_friesland |  |  | 53 | 5 | 42 | 6 | 10 | 0 | 1 | 2 | 0 |
| europe_norway_sorlandet | Y |  | 32 | 5 | 27 | 0 | 6 | 2 | 2 | 0 | 0 |
| europe_poland_wielkopolskie |  |  | 32 | 5 | 27 | 0 | 10 | 0 | 1 | 0 | 0 |
| europe_slovakia |  |  | 21 | 5 | 15 | 1 | 8 | 1 | 1 | 1 | 0 |
| europe_switzerland |  |  | 560 | 5 | 554 | 1 | 10 | 0 | 3 | 0 | 0 |
| europe_united_kingdom_england_isle_of_wight |  |  | 14 | 5 | 9 | 0 | 16 | 0 | 8 | 0 | 0 |
| europe_united_kingdom_wales |  |  | 14 | 5 | 9 | 0 | 12 | 0 | 6 | 0 | 0 |
| north_america_canada_british_columbia_kootenay_admreg |  |  | 5 | 5 | 0 | 0 | 10 | 0 | 0 | 0 | 0 |
| north_america_canada_manitoba |  |  | 5 | 5 | 0 | 0 | 10 | 0 | 0 | 1 | 0 |
| north_america_canada_northwest_territories | Y |  | 6 | 5 | 1 | 0 | 10 | 0 | 0 | 1 | 0 |
| north_america_mexico | Y |  | 99 | 5 | 93 | 1 | 10 | 0 | 1 | 0 | 0 |
| north_america_us_texas |  |  | 8 | 5 | 3 | 0 | 10 | 0 | 0 | 0 | 0 |
| russia_south_fed_district |  |  | 111 | 5 | 106 | 0 |  |  |  |  |  |
| south_america_guyana |  |  | 24 | 5 | 18 | 1 | 16 | 0 | 1 | 3 | 0 |
| south_america_peru |  |  | 146 | 5 | 141 | 0 | 8 | 1 | 1 | 0 | 0 |
| africa_cameroon |  |  | 61 | 4 | 56 | 1 | 4 | 2 | 0 | 0 | 0 |
| asia_china_hubei |  |  | 107 | 4 | 103 | 0 | 8 | 0 | 0 | 0 | 0 |
| asia_china_zhejiang |  |  | 214 | 4 | 210 | 0 | 6 | 1 | 0 | 1 | 0 |
| europe_bulgaria |  |  | 13 | 4 | 9 | 0 | 8 | 0 | 2 | 1 | 0 |
| europe_latvia |  |  | 9 | 4 | 4 | 1 | 6 | 1 | 2 | 0 | 0 |
| europe_poland_malopolskie |  |  | 13 | 4 | 9 | 0 | 8 | 0 | 0 | 0 | 0 |
| europe_poland_mazowieckie |  |  | 16 | 4 | 11 | 1 | 6 | 1 | 0 | 0 | 0 |
| europe_poland_podlaskie |  |  | 12 | 4 | 8 | 0 | 8 | 0 | 0 | 1 | 0 |
| europe_spain_valencia |  |  | 23 | 4 | 19 | 0 | 8 | 0 | 4 | 0 | 0 |
| europe_sweden_gotland |  |  | 9 | 4 | 5 | 0 | 16 | 0 | 8 | 0 | 0 |
| europe_sweden_jonkoping |  |  | 5 | 4 | 1 | 0 | 8 | 0 | 0 | 0 | 0 |
| europe_sweden_norrbotten |  |  | 31 | 4 | 27 | 0 | 6 | 1 | 0 | 0 | 0 |
| europe_sweden_ostergotland |  |  | 10 | 4 | 6 | 0 | 8 | 0 | 0 | 1 | 0 |
| europe_united_kingdom_england_dorset |  |  | 9 | 4 | 5 | 0 | 8 | 0 | 3 | 0 | 0 |
| north_america_us_massachusetts |  |  | 60 | 4 | 56 | 0 | 10 | 0 | 2 | 0 | 0 |
| africa_ghana |  |  | 24 | 3 | 21 | 0 | 6 | 0 | 0 | 0 | 0 |
| africa_ivory_coast |  |  | 54 | 3 | 51 | 0 | 6 | 0 | 0 | 0 | 0 |
| africa_mali |  |  | 112 | 3 | 109 | 0 | 6 | 0 | 0 | 0 | 0 |
| asia_china_guizhou |  |  | 28 | 3 | 25 | 0 | 6 | 0 | 0 | 1 | 0 |
| asia_china_shaanxi |  |  | 15 | 3 | 12 | 0 | 6 | 0 | 0 | 0 | 0 |
| asia_myanmar | Y |  | 5615 | 3 | 5612 | 0 | 6 | 0 | 0 | 0 | 0 |
| australia_oceania_samoa |  |  | 7 | 3 | 4 | 0 | 6 | 0 | 2 | 0 | 0 |
| central_america_belize |  |  | 50 | 3 | 47 | 0 | 6 | 0 | 0 | 1 | 0 |
| central_america_costa_rica |  |  | 38 | 3 | 35 | 0 | 6 | 0 | 0 | 0 | 0 |
| central_america_panama |  |  | 62 | 3 | 58 | 1 | 6 | 0 | 3 | 0 | 0 |
| europe_belgium |  |  | 74 | 3 | 70 | 1 | 6 | 0 | 0 | 1 | 0 |
| europe_cyprus |  |  | 6 | 3 | 3 | 0 | 6 | 0 | 3 | 0 | 0 |
| europe_czech_republic_jihocesky |  |  | 22 | 3 | 19 | 0 | 6 | 0 | 0 | 0 | 0 |
| europe_france_alsace |  |  | 6 | 3 | 3 | 0 | 6 | 0 | 0 | 1 | 0 |
| europe_france_languedoc_roussillon |  |  | 15 | 3 | 12 | 0 | 6 | 0 | 3 | 0 | 0 |
| europe_germany_baden_wuerttemberg_freiburg_regbez |  |  | 95 | 3 | 92 | 0 | 6 | 0 | 2 | 0 | 0 |
| europe_germany_baden_wuerttemberg_tuebingen_regbez |  |  | 49 | 3 | 46 | 0 | 6 | 0 | 3 | 0 | 0 |
| europe_germany_berlin |  |  | 21 | 3 | 18 | 0 | 6 | 0 | 1 | 2 | 0 |
| europe_germany_nordrhein_westfalen_duesseldorf_regbez |  |  | 23 | 3 | 20 | 0 | 6 | 0 | 1 | 0 | 0 |
| europe_malta |  |  | 22 | 3 | 19 | 0 | 6 | 0 | 2 | 0 | 0 |
| europe_montenegro |  |  | 7 | 3 | 4 | 0 | 6 | 0 | 0 | 0 | 0 |
| europe_netherlands_overijssel |  |  | 30 | 3 | 27 | 0 | 6 | 0 | 1 | 0 | 0 |
| europe_poland_pomorskie |  |  | 30 | 3 | 25 | 2 | 6 | 0 | 2 | 0 | 0 |
| europe_serbia |  |  | 27 | 3 | 23 | 1 | 6 | 0 | 1 | 0 | 0 |
| europe_slovenia |  |  | 20 | 3 | 15 | 2 | 4 | 1 | 2 | 0 | 0 |
| europe_spain_melilla |  |  | 6 | 3 | 3 | 0 | 6 | 0 | 3 | 0 | 0 |
| europe_sweden_kalmar |  |  | 14 | 3 | 10 | 1 | 10 | 1 | 2 | 0 | 0 |
| europe_sweden_sodermanland |  |  | 5 | 3 | 2 | 0 | 6 | 0 | 0 | 3 | 0 |
| europe_united_kingdom_england_merseyside |  |  | 4 | 3 | 1 | 0 | 6 | 0 | 3 | 1 | 0 |
| north_america_us_illinois |  |  | 21 | 3 | 16 | 2 | 6 | 0 | 1 | 1 | 0 |
| north_america_us_missouri |  |  | 7 | 3 | 3 | 1 | 4 | 1 | 0 | 1 | 0 |
| north_america_us_montana |  |  | 11 | 3 | 8 | 0 | 6 | 0 | 0 | 0 | 0 |
| north_america_us_oregon |  |  | 14 | 3 | 9 | 2 | 6 | 0 | 0 | 0 | 0 |
| north_america_us_puerto_rico |  |  | 8 | 3 | 5 | 0 | 6 | 0 | 2 | 1 | 0 |
| north_america_us_vermont |  |  | 6 | 3 | 2 | 1 | 6 | 0 | 3 | 0 | 0 |
| russia_crimean_fed_district |  |  | 18 | 3 | 15 | 0 | 6 | 1 | 3 | 0 | 0 |
| south_america_ecuador |  |  | 39 | 3 | 35 | 1 | 6 | 0 | 0 | 0 | 0 |
| south_america_suriname |  |  | 12 | 3 | 9 | 0 | 6 | 0 | 2 | 0 | 0 |
| africa_canary_islands |  |  | 30 | 2 | 28 | 0 | 4 | 0 | 0 | 0 | 0 |
| africa_chad |  |  | 25 | 2 | 23 | 0 | 4 | 0 | 0 | 0 | 0 |
| africa_congo_brazzaville |  |  | 29 | 2 | 27 | 0 | 4 | 0 | 0 | 1 | 0 |
| africa_guinea |  |  | 103 | 2 | 101 | 0 | 2 | 1 | 0 | 0 | 0 |
| africa_kenya |  |  | 24 | 2 | 21 | 1 | 4 | 0 | 0 | 0 | 0 |
| africa_tanzania |  |  | 34 | 2 | 32 | 0 | 4 | 0 | 0 | 0 | 0 |
| africa_uganda |  |  | 33 | 2 | 31 | 0 | 4 | 0 | 0 | 0 | 0 |
| asia_china_henan | Y |  | 27 | 2 | 25 | 0 | 4 | 0 | 1 | 1 | 0 |
| asia_china_jiangxi |  |  | 20 | 2 | 18 | 0 | 2 | 1 | 0 | 1 | 0 |
| asia_india_eastern_zone | Y |  | 139 | 2 | 137 | 0 | 4 | 0 | 2 | 0 | 0 |
| asia_jordan |  |  | 3 | 2 | 1 | 0 | 4 | 0 | 1 | 0 | 0 |
| asia_uzbekistan |  |  | 22 | 2 | 20 | 0 | 4 | 0 | 0 | 0 | 0 |
| central_america_guatemala |  |  | 48 | 2 | 46 | 0 | 4 | 0 | 0 | 0 | 0 |
| europe_czech_republic_plzensky |  |  | 4 | 2 | 2 | 0 | 4 | 0 | 0 | 0 | 0 |
| europe_czech_republic_ustecky |  |  | 16 | 2 | 14 | 0 | 4 | 0 | 0 | 0 | 0 |
| europe_france_aquitaine |  |  | 30 | 2 | 26 | 2 | 4 | 0 | 1 | 0 | 0 |
| europe_france_martinique |  |  | 16 | 2 | 14 | 0 | 4 | 0 | 2 | 0 | 0 |
| europe_france_poitou_charentes |  |  | 38 | 2 | 35 | 1 | 4 | 0 | 1 | 0 | 0 |
| europe_germany_bayern_oberbayern |  |  | 80 | 2 | 78 | 0 | 4 | 0 | 0 | 1 | 0 |
| europe_germany_bayern_oberpfalz |  |  | 11 | 2 | 9 | 0 | 4 | 0 | 0 | 0 | 0 |
| europe_germany_bayern_schwaben |  |  | 24 | 2 | 22 | 0 | 4 | 0 | 2 | 0 | 0 |
| europe_germany_bremen |  |  | 3 | 2 | 1 | 0 | 16 | 0 | 9 | 0 | 0 |
| europe_moldova |  |  | 8 | 2 | 6 | 0 | 4 | 0 | 0 | 0 | 0 |
| europe_netherlands_groningen |  |  | 27 | 2 | 23 | 2 | 4 | 0 | 2 | 0 | 0 |
| europe_poland_kujawsko_pomorskie |  |  | 5 | 2 | 2 | 1 | 4 | 0 | 0 | 0 | 0 |
| europe_poland_lubuskie |  |  | 6 | 2 | 3 | 1 | 4 | 0 | 0 | 0 | 0 |
| europe_poland_podkarpackie |  |  | 12 | 2 | 10 | 0 | 4 | 0 | 0 | 2 | 0 |
| europe_poland_warminsko_mazurskie |  |  | 49 | 2 | 47 | 0 | 4 | 0 | 0 | 1 | 0 |
| europe_spain_cantabria |  |  | 9 | 2 | 7 | 0 | 4 | 0 | 2 | 0 | 0 |
| europe_spain_murcia |  |  | 3 | 2 | 1 | 0 | 0 | 2 | 0 | 0 | 0 |
| europe_sweden_uppsala |  |  | 7 | 2 | 5 | 0 | 4 | 0 | 0 | 2 | 0 |
| europe_sweden_varmland |  |  | 12 | 2 | 10 | 0 | 4 | 0 | 0 | 1 | 0 |
| europe_sweden_vasterbotten |  |  | 14 | 2 | 12 | 0 | 4 | 0 | 2 | 0 | 0 |
| europe_sweden_vasternorrland |  |  | 6 | 2 | 4 | 0 | 6 | 0 | 0 | 1 | 0 |
| europe_united_kingdom_england_east_yorkshire_with_hull |  |  | 4 | 2 | 1 | 1 | 4 | 0 | 3 | 0 | 0 |
| europe_united_kingdom_england_essex |  |  | 8 | 2 | 6 | 0 | 6 | 0 | 2 | 0 | 0 |
| europe_united_kingdom_england_lincolnshire |  |  | 7 | 2 | 4 | 1 | 4 | 0 | 3 | 1 | 0 |
| north_america_canada_prince_edward_island |  |  | 2 | 2 | 0 | 0 | 4 | 0 | 2 | 0 | 0 |
| north_america_canada_yukon |  |  | 2 | 2 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| north_america_us_alabama |  |  | 3 | 2 | 0 | 1 | 4 | 0 | 0 | 0 | 0 |
| north_america_us_california_norcal |  |  | 39 | 2 | 37 | 0 | 4 | 0 | 0 | 0 | 0 |
| north_america_us_delaware |  |  | 5 | 2 | 3 | 0 | 4 | 0 | 1 | 0 | 0 |
| north_america_us_iowa |  |  | 7 | 2 | 5 | 0 | 4 | 0 | 0 | 2 | 0 |
| north_america_us_louisiana | Y |  | 16 | 2 | 12 | 2 | 4 | 0 | 0 | 0 | 0 |
| north_america_us_maryland |  |  | 18 | 2 | 14 | 2 | 4 | 0 | 0 | 0 | 0 |
| north_america_us_new_jersey |  |  | 39 | 2 | 36 | 1 | 6 | 0 | 2 | 0 | 0 |
| north_america_us_rhode_island |  |  | 14 | 2 | 12 | 0 | 4 | 0 | 0 | 0 | 0 |
| north_america_us_tennessee |  |  | 10 | 2 | 7 | 1 | 4 | 0 | 0 | 0 | 0 |
| russia_kaliningrad |  |  | 3 | 2 | 1 | 0 | 4 | 0 | 1 | 0 | 0 |
| russia_northwestern_fed_district |  |  | 3 | 2 | 1 | 0 |  |  |  |  |  |
| africa_botswana |  |  | 5 | 1 | 3 | 1 | 2 | 0 | 0 | 0 | 0 |
| africa_burkina_faso |  |  | 22 | 1 | 21 | 0 | 2 | 0 | 0 | 2 | 0 |
| africa_central_african_republic |  |  | 62 | 1 | 61 | 0 | 2 | 0 | 0 | 0 | 0 |
| africa_guinea_bissau | Y |  | 22 | 1 | 21 | 0 | 2 | 0 | 0 | 0 | 0 |
| africa_mauritania |  |  | 84 | 1 | 83 | 0 | 2 | 0 | 0 | 1 | 0 |
| africa_nigeria | Y |  | 731 | 1 | 730 | 0 | 2 | 0 | 0 | 0 | 0 |
| africa_rwanda |  |  | 31 | 1 | 29 | 1 | 2 | 0 | 1 | 0 | 0 |
| africa_sao_tome_and_principe |  |  | 3 | 1 | 2 | 0 | 2 | 0 | 2 | 0 | 0 |
| africa_sierra_leone |  |  | 40 | 1 | 39 | 0 | 2 | 0 | 0 | 0 | 0 |
| africa_south_africa |  |  | 9 | 1 | 8 | 0 | 2 | 0 | 0 | 0 | 0 |
| africa_south_africa_and_lesotho |  |  | 9 | 1 | 8 | 0 | 2 | 0 | 0 | 0 | 0 |
| africa_sudan |  |  | 21 | 1 | 20 | 0 | 2 | 0 | 1 | 0 | 0 |
| asia_azerbaijan | Y |  | 7 | 1 | 6 | 0 | 2 | 0 | 2 | 0 | 0 |
| asia_bangladesh | Y |  | 311 | 1 | 310 | 0 | 2 | 0 | 0 | 0 | 0 |
| asia_china_hebei | Y |  | 45 | 1 | 43 | 1 | 2 | 0 | 1 | 0 | 0 |
| asia_china_liaoning |  |  | 38 | 1 | 37 | 0 | 2 | 0 | 1 | 0 | 0 |
| asia_china_ningxia |  |  | 5 | 1 | 4 | 0 | 2 | 0 | 0 | 2 | 0 |
| asia_china_shanxi |  |  | 11 | 1 | 10 | 0 | 2 | 0 | 0 | 1 | 0 |
| asia_east_timor |  |  | 4 | 1 | 3 | 0 | 6 | 0 | 6 | 0 | 0 |
| asia_india_central_zone |  |  | 15 | 1 | 14 | 0 | 2 | 0 | 0 | 0 | 0 |
| asia_maldives |  |  | 110 | 1 | 109 | 0 | 2 | 0 | 2 | 0 | 0 |
| asia_pakistan | Y |  | 21 | 1 | 20 | 0 | 2 | 0 | 0 | 0 | 0 |
| asia_turkmenistan |  |  | 8 | 1 | 7 | 0 | 2 | 0 | 2 | 0 | 0 |
| australia_oceania_american_oceania |  |  | 4 | 1 | 3 | 0 | 2 | 0 | 1 | 0 | 0 |
| australia_oceania_australia_northern_territory |  |  | 8 | 1 | 7 | 0 | 6 | 0 | 0 | 0 | 0 |
| australia_oceania_australia_western_australia |  |  | 22 | 1 | 19 | 2 | 0 | 1 | 0 | 0 | 0 |
| australia_oceania_new_caledonia |  |  | 4 | 1 | 3 | 0 | 2 | 0 | 0 | 0 | 0 |
| australia_oceania_polynesie_francaise |  |  | 30 | 1 | 28 | 1 | 2 | 0 | 0 | 0 | 0 |
| australia_oceania_tokelau |  |  | 1 | 1 | 0 | 0 | 10 | 0 | 2 | 2 | 0 |
| central_america_haiti_and_domrep |  |  | 28 | 1 | 27 | 0 | 2 | 0 | 1 | 0 | 0 |
| central_america_nicaragua |  |  | 75 | 1 | 74 | 0 | 2 | 0 | 1 | 0 | 0 |
| europe_bosnia_herzegovina |  |  | 6 | 1 | 4 | 1 | 2 | 0 | 0 | 0 | 0 |
| europe_france_guadeloupe |  |  | 33 | 1 | 32 | 0 | 2 | 0 | 1 | 0 | 0 |
| europe_france_guyane |  |  | 6 | 1 | 5 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_georgia |  |  | 16 | 1 | 14 | 1 | 2 | 0 | 0 | 0 | 0 |
| europe_germany_baden_wuerttemberg_stuttgart_regbez |  |  | 9 | 1 | 8 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_germany_bayern_oberfranken |  |  | 4 | 1 | 3 | 0 | 2 | 0 | 0 | 1 | 0 |
| europe_germany_hamburg |  |  | 61 | 1 | 48 | 12 | 2 | 0 | 0 | 0 | 0 |
| europe_germany_thueringen |  |  | 6 | 1 | 5 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_luxembourg |  |  | 1 | 1 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_poland_lubelskie |  |  | 2 | 1 | 1 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_poland_opolskie |  |  | 1 | 1 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_poland_swietokrzyskie |  |  | 2 | 1 | 1 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_spain_ceuta |  |  | 1 | 1 | 0 | 0 | 2 | 0 | 1 | 0 | 0 |
| europe_spain_galicia |  |  | 14 | 1 | 13 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_spain_pais_vasco |  |  | 15 | 1 | 14 | 0 | 2 | 0 | 1 | 0 | 0 |
| europe_sweden_kronoberg |  |  | 2 | 1 | 1 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_sweden_vastmanland |  |  | 7 | 1 | 6 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_united_kingdom_england_cumbria |  |  | 22 | 1 | 21 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_united_kingdom_england_east_sussex |  |  | 1 | 1 | 0 | 0 | 4 | 0 | 3 | 0 | 0 |
| europe_united_kingdom_england_greater_london | Y |  | 35 | 1 | 34 | 0 | 2 | 0 | 0 | 0 | 0 |
| europe_united_kingdom_england_lancashire |  |  | 5 | 1 | 2 | 2 | 2 | 0 | 1 | 0 | 0 |
| europe_united_kingdom_england_norfolk |  |  | 8 | 1 | 6 | 1 | 2 | 0 | 0 | 0 | 0 |
| europe_united_kingdom_england_suffolk |  |  | 10 | 1 | 9 | 0 | 2 | 0 | 2 | 0 | 0 |
| europe_united_kingdom_england_tyne_and_wear |  |  | 2 | 1 | 1 | 0 | 2 | 0 | 1 | 0 | 0 |
| europe_united_kingdom_falklands |  |  | 1 | 1 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| north_america_greenland |  |  | 37 | 1 | 36 | 0 | 2 | 0 | 1 | 0 | 0 |
| north_america_us_arkansas |  |  | 7 | 1 | 6 | 0 | 2 | 0 | 0 | 0 | 0 |
| north_america_us_california_socal |  |  | 35 | 1 | 34 | 0 | 2 | 0 | 0 | 0 | 0 |
| north_america_us_indiana |  |  | 2 | 1 | 0 | 1 | 2 | 0 | 0 | 0 | 0 |
| north_america_us_pennsylvania |  |  | 6 | 1 | 5 | 0 | 2 | 0 | 0 | 0 | 0 |
| north_america_us_us_virgin_islands |  |  | 14 | 1 | 13 | 0 | 2 | 0 | 0 | 0 | 0 |
| north_america_us_utah |  |  | 1 | 1 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| south_america_uruguay |  |  | 9 | 1 | 8 | 0 | 2 | 0 | 1 | 0 | 0 |
| africa_algeria |  |  | 21 | 0 | 21 | 0 |  |  |  |  |  |
| africa_angola |  |  | 43 | 0 | 43 | 0 |  |  |  |  |  |
| africa_benin |  |  | 67 | 0 | 67 | 0 |  |  |  |  |  |
| africa_burundi |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| africa_cape_verde |  |  | 15 | 0 | 15 | 0 |  |  |  |  |  |
| africa_comores |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| africa_djibouti |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| africa_equatorial_guinea |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| africa_eritrea |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| africa_ethiopia |  |  | 17 | 0 | 17 | 0 |  |  |  |  |  |
| africa_gabon |  |  | 21 | 0 | 21 | 0 |  |  |  |  |  |
| africa_liberia |  |  | 29 | 0 | 29 | 0 |  |  |  |  |  |
| africa_libya |  |  | 6 | 0 | 6 | 0 |  |  |  |  |  |
| africa_malawi |  |  | 13 | 0 | 13 | 0 |  |  |  |  |  |
| africa_mauritius |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| africa_mozambique |  |  | 26 | 0 | 26 | 0 |  |  |  |  |  |
| africa_namibia |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| africa_niger |  |  | 9 | 0 | 9 | 0 |  |  |  |  |  |
| africa_saint_helena_ascension_and_tristan_da_cunha |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| africa_seychelles |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| africa_somalia |  |  | 5 | 0 | 5 | 0 |  |  |  |  |  |
| africa_south_sudan |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| africa_togo |  |  | 18 | 0 | 18 | 0 |  |  |  |  |  |
| africa_tunisia |  |  | 29 | 0 | 29 | 0 |  |  |  |  |  |
| africa_zambia |  |  | 31 | 0 | 31 | 0 |  |  |  |  |  |
| africa_zimbabwe |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| antarctica | Y | Y | 3 | 0 | 3 | 0 |  |  |  |  |  |
| asia_afghanistan |  |  | 48 | 0 | 48 | 0 |  |  |  |  |  |
| asia_bhutan |  |  | 1 | 0 | 0 | 1 |  |  |  |  |  |
| asia_china_beijing |  |  | 24 | 0 | 24 | 0 |  |  |  |  |  |
| asia_china_fujian | Y |  | 73 | 0 | 71 | 2 |  |  |  |  |  |
| asia_china_heilongjiang |  |  | 16 | 0 | 16 | 0 |  |  |  |  |  |
| asia_china_hong_kong |  |  | 112 | 0 | 109 | 3 |  |  |  |  |  |
| asia_china_inner_mongolia |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| asia_china_jilin |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| asia_china_macau |  |  | 15 | 0 | 15 | 0 |  |  |  |  |  |
| asia_china_qinghai |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| asia_china_tianjin | Y |  | 15 | 0 | 14 | 1 |  |  |  |  |  |
| asia_china_tibet |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| asia_china_xinjiang |  |  | 5 | 0 | 5 | 0 |  |  |  |  |  |
| asia_china_yunnan |  |  | 41 | 0 | 41 | 0 |  |  |  |  |  |
| asia_india_northern_zone | Y |  | 8 | 0 | 8 | 0 |  |  |  |  |  |
| asia_iran | Y |  | 22 | 0 | 22 | 0 |  |  |  |  |  |
| asia_iraq |  |  | 26 | 0 | 26 | 0 |  |  |  |  |  |
| asia_kazakhstan |  |  | 25 | 0 | 25 | 0 |  |  |  |  |  |
| asia_kyrgyzstan |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| asia_mongolia |  |  | 16 | 0 | 16 | 0 |  |  |  |  |  |
| asia_nepal |  |  | 13 | 0 | 13 | 0 |  |  |  |  |  |
| asia_north_korea |  |  | 26 | 0 | 26 | 0 |  |  |  |  |  |
| asia_sri_lanka |  |  | 18 | 0 | 17 | 1 |  |  |  |  |  |
| asia_syria |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| asia_tajikistan |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| asia_yemen |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| australia_oceania_australia_cocos_islands |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| australia_oceania_cook_islands |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| australia_oceania_fiji |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| australia_oceania_marshall_islands |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| australia_oceania_micronesia |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| australia_oceania_palau |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| australia_oceania_papua_new_guinea |  |  | 22 | 0 | 22 | 0 |  |  |  |  |  |
| australia_oceania_pitcairn_islands |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| australia_oceania_solomon_islands |  |  | 9 | 0 | 9 | 0 |  |  |  |  |  |
| australia_oceania_tonga |  |  | 12 | 0 | 12 | 0 |  |  |  |  |  |
| australia_oceania_vanuatu |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| central_america_cuba |  |  | 39 | 0 | 39 | 0 |  |  |  |  |  |
| central_america_el_salvador |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| central_america_honduras |  |  | 20 | 0 | 20 | 0 |  |  |  |  |  |
| central_america_jamaica |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_czech_republic_jihomoravsky |  |  | 8 | 0 | 8 | 0 |  |  |  |  |  |
| europe_czech_republic_karlovarsky |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_czech_republic_kralovehradecky |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| europe_czech_republic_liberecky |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_czech_republic_moravskoslezky |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_czech_republic_olomoucky |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_czech_republic_praha |  |  | 10 | 0 | 10 | 0 |  |  |  |  |  |
| europe_czech_republic_stredocesky |  |  | 25 | 0 | 25 | 0 |  |  |  |  |  |
| europe_czech_republic_vysocina |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| europe_czech_republic_zlinsky |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_france_auvergne |  |  | 5 | 0 | 5 | 0 |  |  |  |  |  |
| europe_france_centre |  |  | 8 | 0 | 8 | 0 |  |  |  |  |  |
| europe_france_champagne_ardenne |  |  | 5 | 0 | 5 | 0 |  |  |  |  |  |
| europe_france_franche_comte |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| europe_france_ile_de_france |  |  | 28 | 0 | 24 | 4 |  |  |  |  |  |
| europe_france_limousin |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| europe_france_lorraine |  |  | 7 | 0 | 6 | 1 |  |  |  |  |  |
| europe_france_mayotte |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_france_midi_pyrenees |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| europe_france_rhone_alpes |  |  | 28 | 0 | 28 | 0 |  |  |  |  |  |
| europe_germany_bayern_mittelfranken |  |  | 11 | 0 | 10 | 1 |  |  |  |  |  |
| europe_germany_nordrhein_westfalen_arnsberg_regbez |  |  | 25 | 0 | 25 | 0 |  |  |  |  |  |
| europe_germany_nordrhein_westfalen_detmold_regbez |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| europe_germany_nordrhein_westfalen_muenster_regbez |  |  | 9 | 0 | 8 | 1 |  |  |  |  |  |
| europe_germany_saarland |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| europe_macedonia |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_monaco |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_netherlands_drenthe |  |  | 18 | 0 | 17 | 1 |  |  |  |  |  |
| europe_netherlands_flevoland |  |  | 16 | 0 | 16 | 0 |  |  |  |  |  |
| europe_netherlands_zeeland |  |  | 38 | 0 | 38 | 0 |  |  |  |  |  |
| europe_norway_svalbard_janmayen | Y |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| europe_poland_dolnoslaskie |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_poland_lodzkie |  |  | 5 | 0 | 5 | 0 |  |  |  |  |  |
| europe_poland_slaskie |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_spain_aragon |  |  | 6 | 0 | 6 | 0 |  |  |  |  |  |
| europe_spain_castilla_la_mancha |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_spain_extremadura |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_sweden_gavleborg |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_sweden_halland |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_sweden_orebro |  |  | 5 | 0 | 4 | 1 |  |  |  |  |  |
| europe_united_kingdom_bermuda |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_berkshire |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_bristol |  |  | 9 | 0 | 9 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_cambridgeshire |  |  | 2 | 0 | 0 | 2 |  |  |  |  |  |
| europe_united_kingdom_england_cheshire |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_durham |  |  | 3 | 0 | 0 | 3 |  |  |  |  |  |
| europe_united_kingdom_england_gloucestershire |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_hertfordshire |  |  | 1 | 0 | 0 | 1 |  |  |  |  |  |
| europe_united_kingdom_england_north_yorkshire |  |  | 3 | 0 | 0 | 3 |  |  |  |  |  |
| europe_united_kingdom_england_northumberland |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_nottinghamshire |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_oxfordshire |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_somerset |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_staffordshire |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_surrey |  |  | 4 | 0 | 4 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_warwickshire |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_west_midlands |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_west_sussex |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_west_yorkshire |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_wiltshire |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| europe_united_kingdom_england_worcestershire |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| hedmark | Y |  | 18 | 0 | 18 | 0 |  |  |  |  |  |
| north_america_canada_nunavut_qikiqtaaluk | Y |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| north_america_us_arizona |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| north_america_us_colorado |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| north_america_us_district_of_columbia |  |  | 7 | 0 | 7 | 0 |  |  |  |  |  |
| north_america_us_georgia |  |  | 6 | 0 | 6 | 0 |  |  |  |  |  |
| north_america_us_hawaii |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| north_america_us_idaho |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| north_america_us_minnesota |  |  | 4 | 0 | 3 | 1 |  |  |  |  |  |
| north_america_us_mississippi |  |  | 3 | 0 | 3 | 0 |  |  |  |  |  |
| north_america_us_nebraska |  |  | 1 | 0 | 1 | 0 |  |  |  |  |  |
| north_america_us_nevada |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| north_america_us_new_hampshire |  |  | 6 | 0 | 6 | 0 |  |  |  |  |  |
| north_america_us_oklahoma |  |  | 5 | 0 | 5 | 0 |  |  |  |  |  |
| north_america_us_south_carolina |  |  | 12 | 0 | 12 | 0 |  |  |  |  |  |
| north_america_us_west_virginia |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| north_america_us_wyoming |  |  | 2 | 0 | 1 | 1 |  |  |  |  |  |
| russia_far_eastern_fed_district |  |  | 6 | 0 | 6 | 0 |  |  |  |  |  |
| russia_north_caucasus_fed_district |  |  | 2 | 0 | 2 | 0 |  |  |  |  |  |
| us_west_virginia | Y |  | 2 | 0 | 2 | 0 |  |  |  |  |  |

## 3. `NAVI_FERRY_LINKS_REGIONS` + weekly `regions.conf` adds

Candidates need boarding-island fixes (`no_road` / `tiny` from `ferry_terminal_audit` / `ferry_pack_scan`). Regions **not** already in `data/regions.conf` are recommended as **weekly additions** (not a separate planet track). Composites / mega-blobs that exceed the 16 GiB weekly RAM budget are deferred.

### Weekly host budget

- Target: **8 cores / 16.0 GiB RAM / 512.0 GiB disk**
- Peak RSS ceiling used here: **14336.0 MiB** (leave ~2 GiB for OS/fetch/validate)
- Max added serial convert time for new weekly regions: **6.0 h**

### Current weekly baseline (`regions.conf`)

- Regions: 42
- Serial convert sum (from planet-leaves PASS logs): **1.55 h** (5581.7 s)
- Max peak RSS among weekly: **24001.6 MiB**
- Sum published pack size: **85.84 GiB**
- Known DEM road cells (sum of caches): 2304
- Already over ~14 GiB peak RSS on weekly list (host budget stress): `north_america_mexico` (24001.6 MiB), `asia_vietnam` (15165.7 MiB)
  Prefer a one-off `run-weekly.sh --region …` with `NAVI_FERRY_LINKS_REGIONS=<id>` when the host is free, rather than assuming Monday can absorb more peak RSS.

### Exact env line

```bash
NAVI_FERRY_LINKS_REGIONS=europe_norway_vestlandet,asia_south_korea,europe_denmark,asia_japan_hokkaido,europe_finland,europe_turkey,europe_germany_niedersachsen,asia_indonesia_sulawesi,europe_norway_nord_norge,north_america_us_washington,europe_france_nord_pas_de_calais,asia_japan_shikoku,europe_greece,europe_albania,asia_philippines,europe_italy_isole,asia_indonesia_sumatra,asia_indonesia_kalimantan,asia_indonesia_maluku,north_america_canada_british_columbia_southcoast_admreg,europe_italy_sud,europe_croatia,asia_indonesia_papua,europe_france_corse,europe_italy_nord_ovest,europe_germany_mecklenburg_vorpommern,europe_sweden_stockholm,asia_japan_tohoku,europe_netherlands_gelderland,asia_japan_chugoku,north_america_canada_british_columbia_island_admreg,europe_italy_centro,europe_united_kingdom_england_cornwall,europe_spain_andalucia,europe_germany_schleswig_holstein,europe_estonia,europe_france_provence_alpes_cote_d_azur,north_america_us_new_york,europe_netherlands_noord_brabant,europe_united_kingdom_england_hampshire,asia_malaysia_singapore_brunei,north_america_us_florida,europe_norway_trondelag,africa_egypt,europe_norway_ostlandet,asia_thailand,europe_spain_melilla,europe_norway_sorlandet,asia_china_henan,asia_india_eastern_zone,asia_azerbaijan,asia_china_shandong,north_america_canada_northwest_territories,asia_china_hebei
```

### Exact `data/regions.conf` lines to add

Do **not** edit the live config from this analysis agent. Copy manually:

```
asia_south_korea	geofabrik:asia/south-korea
europe_denmark	geofabrik:europe/denmark
asia_japan_hokkaido	geofabrik:asia/japan/hokkaido
europe_finland	geofabrik:europe/finland
europe_turkey	geofabrik:europe/turkey
europe_germany_niedersachsen	geofabrik:europe/germany/niedersachsen
asia_indonesia_sulawesi	geofabrik:asia/indonesia/sulawesi
north_america_us_washington	geofabrik:north-america/us/washington
europe_france_nord_pas_de_calais	geofabrik:europe/france/nord-pas-de-calais
asia_japan_shikoku	geofabrik:asia/japan/shikoku
europe_greece	geofabrik:europe/greece
europe_albania	geofabrik:europe/albania
asia_philippines	geofabrik:asia/philippines
europe_italy_isole	geofabrik:europe/italy/isole
asia_indonesia_sumatra	geofabrik:asia/indonesia/sumatra
asia_indonesia_kalimantan	geofabrik:asia/indonesia/kalimantan
asia_indonesia_maluku	geofabrik:asia/indonesia/maluku
north_america_canada_british_columbia_southcoast_admreg	geofabrik:north-america/canada/british-columbia/southcoast-admreg
europe_italy_sud	geofabrik:europe/italy/sud
europe_croatia	geofabrik:europe/croatia
asia_indonesia_papua	geofabrik:asia/indonesia/papua
europe_france_corse	geofabrik:europe/france/corse
europe_italy_nord_ovest	geofabrik:europe/italy/nord-ovest
europe_germany_mecklenburg_vorpommern	geofabrik:europe/germany/mecklenburg-vorpommern
europe_sweden_stockholm	url:https://download.openstreetmap.fr/extracts/europe/sweden/stockholm-latest.osm.pbf
asia_japan_tohoku	geofabrik:asia/japan/tohoku
europe_netherlands_gelderland	geofabrik:europe/netherlands/gelderland
asia_japan_chugoku	geofabrik:asia/japan/chugoku
north_america_canada_british_columbia_island_admreg	geofabrik:north-america/canada/british-columbia/island-admreg
europe_italy_centro	geofabrik:europe/italy/centro
europe_united_kingdom_england_cornwall	geofabrik:europe/united-kingdom/england/cornwall
europe_spain_andalucia	geofabrik:europe/spain/andalucia
europe_germany_schleswig_holstein	geofabrik:europe/germany/schleswig-holstein
europe_estonia	geofabrik:europe/estonia
europe_france_provence_alpes_cote_d_azur	geofabrik:europe/france/provence-alpes-cote-d-azur
north_america_us_new_york	geofabrik:north-america/us/new-york
europe_netherlands_noord_brabant	geofabrik:europe/netherlands/noord-brabant
europe_united_kingdom_england_hampshire	geofabrik:europe/united-kingdom/england/hampshire
europe_spain_melilla	geofabrik:europe/spain/melilla
```

### Selected candidates (with resource columns)

| bake_id | already weekly | convert | peak RSS MiB | pack GiB | DEM cells (source) | DEM missing | DEM add MiB est | no_road | tiny | action |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| europe_norway_vestlandet | Y | 95.5 | 3874.7 | 1.12 | 32 (road_cells_cache) | 0 | 0.0 | 63 | 11 | enable_NAVI_FERRY_LINKS_REGIONS |
| asia_south_korea |  | 186.2 | 11974.8 | 4.3 | 63 (poly_bbox_upper_bound) | 57 | 1596.0 | 19 | 29 | add_to_regions.conf_and_enable_ferry_links |
| europe_denmark |  | 166.4 | 8140.9 | 2.73 | 45 (poly_bbox_upper_bound) | 28 | 784.0 | 44 | 2 | add_to_regions.conf_and_enable_ferry_links |
| asia_japan_hokkaido |  | 82.0 | 5367.9 | 1.26 | 50 (poly_bbox_upper_bound) | 50 | 1400.0 | 30 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_finland |  | 368.0 | 9654.0 | 4.93 | 156 (poly_bbox_upper_bound) | 65 | 1820.0 | 25 | 4 | add_to_regions.conf_and_enable_ferry_links |
| europe_turkey |  | 507.9 | 12885.7 | 7.87 | 180 (poly_bbox_upper_bound) | 175 | 4900.0 | 27 | 1 | add_to_regions.conf_and_enable_ferry_links |
| europe_germany_niedersachsen |  | 168.3 | 8894.4 | 2.77 | 24 (poly_bbox_upper_bound) | 23 | 644.0 | 22 | 3 | add_to_regions.conf_and_enable_ferry_links |
| asia_indonesia_sulawesi |  | 100.7 | 5207.8 | 1.18 | 180 (poly_bbox_upper_bound) | 170 | 4760.0 | 22 | 2 | add_to_regions.conf_and_enable_ferry_links |
| europe_norway_nord_norge | Y | 130.3 | 3087.5 | 0.82 | 72 (road_cells_cache) | 0 | 0.0 | 13 | 11 | enable_NAVI_FERRY_LINKS_REGIONS |
| north_america_us_washington |  | 187.9 | 9681.4 | 2.82 | 55 (poly_bbox_upper_bound) | 55 | 1540.0 | 15 | 9 | add_to_regions.conf_and_enable_ferry_links |
| europe_france_nord_pas_de_calais |  | 73.9 | 2712.9 | 0.86 | 12 (poly_bbox_upper_bound) | 12 | 336.0 | 24 | 0 | add_to_regions.conf_and_enable_ferry_links |
| asia_japan_shikoku |  | 50.7 | 3598.9 | 0.99 | 15 (poly_bbox_upper_bound) | 15 | 420.0 | 14 | 9 | add_to_regions.conf_and_enable_ferry_links |
| europe_greece |  | 235.4 | 9584.2 | 4.06 | 96 (poly_bbox_upper_bound) | 96 | 2688.0 | 20 | 2 | add_to_regions.conf_and_enable_ferry_links |
| europe_albania |  | 31.1 | 2316.1 | 0.49 | 16 (poly_bbox_upper_bound) | 16 | 448.0 | 22 | 0 | add_to_regions.conf_and_enable_ferry_links |
| asia_philippines |  | 339.7 | 9688.2 | 3.83 | 288 (poly_bbox_upper_bound) | 264 | 7392.0 | 17 | 3 | add_to_regions.conf_and_enable_ferry_links |
| europe_italy_isole |  | 105.3 | 5853.5 | 1.74 | 63 (poly_bbox_upper_bound) | 63 | 1764.0 | 20 | 0 | add_to_regions.conf_and_enable_ferry_links |
| asia_indonesia_sumatra |  | 215.4 | 7818.6 | 3.11 | 270 (poly_bbox_upper_bound) | 240 | 6720.0 | 14 | 5 | add_to_regions.conf_and_enable_ferry_links |
| asia_indonesia_kalimantan |  | 140.6 | 7318.9 | 1.66 | 176 (poly_bbox_upper_bound) | 146 | 4088.0 | 18 | 1 | add_to_regions.conf_and_enable_ferry_links |
| asia_indonesia_maluku |  | 33.8 | 3540.3 | 0.17 | 182 (poly_bbox_upper_bound) | 182 | 5096.0 | 18 | 1 | add_to_regions.conf_and_enable_ferry_links |
| north_america_canada_british_columbia_southcoast_admreg |  | 45.6 | 3234.3 | 0.6 | 20 (poly_bbox_upper_bound) | 20 | 560.0 | 18 | 1 | add_to_regions.conf_and_enable_ferry_links |
| europe_italy_sud |  | 178.4 | 8424.9 | 2.7 | 42 (poly_bbox_upper_bound) | 42 | 1176.0 | 19 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_croatia |  | 82.8 | 5433.1 | 1.26 | 35 (poly_bbox_upper_bound) | 35 | 980.0 | 14 | 4 | add_to_regions.conf_and_enable_ferry_links |
| asia_indonesia_papua |  | 47.7 | 4130.5 | 0.24 | 182 (poly_bbox_upper_bound) | 182 | 5096.0 | 12 | 6 | add_to_regions.conf_and_enable_ferry_links |
| europe_france_corse |  | 13.5 | 915.3 | 0.16 | 6 (poly_bbox_upper_bound) | 6 | 168.0 | 17 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_italy_nord_ovest |  | 236.4 | 11061.1 | 3.59 | 24 (poly_bbox_upper_bound) | 24 | 672.0 | 17 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_germany_mecklenburg_vorpommern |  | 38.5 | 2413.8 | 0.61 | 10 (poly_bbox_upper_bound) | 9 | 252.0 | 17 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_sweden_stockholm |  | 30.1 | 2448.3 | 0.55 | 12 (poly_bbox_upper_bound) | 12 | 336.0 | 16 | 0 | add_to_regions.conf_and_enable_ferry_links |
| asia_japan_tohoku |  | 133.9 | 7675.4 | 2.45 | 30 (poly_bbox_upper_bound) | 30 | 840.0 | 15 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_netherlands_gelderland |  | 51.2 | 2309.4 | 0.53 | 6 (poly_bbox_upper_bound) | 6 | 168.0 | 3 | 12 | add_to_regions.conf_and_enable_ferry_links |
| asia_japan_chugoku |  | 98.0 | 5845.4 | 1.8 | 24 (poly_bbox_upper_bound) | 24 | 672.0 | 12 | 2 | add_to_regions.conf_and_enable_ferry_links |
| north_america_canada_british_columbia_island_admreg |  | 32.3 | 2524.7 | 0.36 | 40 (poly_bbox_upper_bound) | 40 | 1120.0 | 12 | 2 | add_to_regions.conf_and_enable_ferry_links |
| europe_italy_centro |  | 164.1 | 7830.6 | 2.61 | 30 (poly_bbox_upper_bound) | 30 | 840.0 | 13 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_united_kingdom_england_cornwall |  | 12.6 | 1101.2 | 0.23 | 6 (poly_bbox_upper_bound) | 6 | 168.0 | 13 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_spain_andalucia |  | 97.7 | 6445.8 | 1.76 | 28 (poly_bbox_upper_bound) | 28 | 784.0 | 12 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_germany_schleswig_holstein |  | 49.2 | 2978.9 | 0.79 | 15 (poly_bbox_upper_bound) | 12 | 336.0 | 11 | 1 | add_to_regions.conf_and_enable_ferry_links |
| europe_estonia |  | 48.5 | 3413.6 | 0.76 | 27 (poly_bbox_upper_bound) | 27 | 756.0 | 12 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_france_provence_alpes_cote_d_azur |  | 132.4 | 5621.5 | 1.82 | 16 (poly_bbox_upper_bound) | 16 | 448.0 | 11 | 0 | add_to_regions.conf_and_enable_ferry_links |
| north_america_us_new_york |  | 219.3 | 11587.1 | 3.55 | 54 (poly_bbox_upper_bound) | 54 | 1512.0 | 6 | 5 | add_to_regions.conf_and_enable_ferry_links |
| europe_netherlands_noord_brabant |  | 52.6 | 1874.3 | 0.51 | 3 (poly_bbox_upper_bound) | 3 | 84.0 | 4 | 7 | add_to_regions.conf_and_enable_ferry_links |
| europe_united_kingdom_england_hampshire |  | 43.7 | 1451.3 | 0.37 | 4 (poly_bbox_upper_bound) | 3 | 84.0 | 9 | 0 | add_to_regions.conf_and_enable_ferry_links |
| asia_malaysia_singapore_brunei | Y | 211.4 | 11002.1 | 4.12 | 71 (road_cells_cache) | 1 | 28.0 | 8 | 0 | enable_NAVI_FERRY_LINKS_REGIONS |
| north_america_us_florida | Y | 370.3 | 14024.3 | 5.4 | 34 (road_cells_cache) | 0 | 0.0 | 8 | 0 | enable_NAVI_FERRY_LINKS_REGIONS |
| europe_norway_trondelag | Y | 64.0 | 2200.3 | 0.6 | 22 (road_cells_cache) | 0 | 0.0 | 6 | 1 | enable_NAVI_FERRY_LINKS_REGIONS |
| africa_egypt | Y | 196.3 | 13633.9 | 3.96 | 128 (road_cells_cache) | 0 | 0.0 | 5 | 2 | enable_NAVI_FERRY_LINKS_REGIONS |
| europe_norway_ostlandet | Y | 198.8 | 7109.0 | 2.44 | 40 (road_cells_cache) | 0 | 0.0 | 5 | 1 | enable_NAVI_FERRY_LINKS_REGIONS |
| asia_thailand | Y | 334.5 | 12639.4 | 7.14 | 81 (road_cells_cache) | 0 | 0.0 | 2 | 1 | enable_NAVI_FERRY_LINKS_REGIONS |
| europe_spain_melilla |  | 1.2 | 124.5 | 0.01 | 1 (poly_bbox_upper_bound) | 1 | 28.0 | 3 | 0 | add_to_regions.conf_and_enable_ferry_links |
| europe_norway_sorlandet | Y | 26.4 | 1185.8 | 0.3 | 18 (road_cells_cache) | 0 | 0.0 | 2 | 0 | enable_NAVI_FERRY_LINKS_REGIONS |
| asia_china_henan | Y | 51.7 | 4137.2 | 0.97 | 34 (road_cells_cache) | 0 | 0.0 | 1 | 1 | enable_NAVI_FERRY_LINKS_REGIONS |
| asia_india_eastern_zone | Y | 266.8 | 10741.9 | 4.88 | 66 (road_cells_cache) | 1 | 28.0 | 2 | 0 | enable_NAVI_FERRY_LINKS_REGIONS |
| asia_azerbaijan | Y | 31.0 | 3107.1 | 1.02 | 41 (road_cells_cache) | 21 | 588.0 | 2 | 0 | enable_NAVI_FERRY_LINKS_REGIONS |
| asia_china_shandong | Y | 66.3 | 5215.3 | 1.54 | 44 (road_cells_cache) | 5 | 140.0 | 1 | 0 | enable_NAVI_FERRY_LINKS_REGIONS |
| north_america_canada_northwest_territories | Y | 86.4 | 3512.9 | 0.11 | 127 (road_cells_cache) | 0 | 0.0 | 0 | 1 | enable_NAVI_FERRY_LINKS_REGIONS |
| asia_china_hebei | Y | 93.2 | 6890.6 | 2.0 | 48 (road_cells_cache) | 2 | 56.0 | 1 | 0 | enable_NAVI_FERRY_LINKS_REGIONS |

### Total effect of selected adds

- New weekly regions: **39** (plus ferry-links enable for already-weekly selected)
- Added serial convert: **1.33 h** (4803.0 s)
- Weekly serial convert after: **2.88 h**
- Weekly peak RSS after (max): **24001.6 MiB**
- Added DEM cache (missing stems × ~28.0 MiB): **61.99 GiB** (63476.0 MiB)
- Published pack size of new regions (reference): **72.03 GiB**

### Deferred / cut-off → single-region bake now

These leaves need ferry links (or are still scan-pending) but **do not** fit adding to weekly in one shot. Recommendation: **targeted single-region bake now** (never a planet run). After a successful one-off convert, re-measure RSS/DEM/disk before promoting onto `regions.conf`.

| bake_id | convert s | peak RSS | pack GiB | DEM cells | cut reason | action |
|---|---:|---:|---:|---:|---|---|
| south_america_brazil_norte | 278.6 | 18309.6 | 1.5 | 660 | peak_rss_mb>14336 | single_region_bake_now |
| asia_india_southern_zone | 500.4 | 14986.3 | 9.62 | 390 | peak_rss_mb>14336 | single_region_bake_now |
| south_america_chile | 265.8 | 11771.9 | 2.29 | 2058 | added_disk_delta_too_large | single_region_bake_now |
| asia_indonesia_java | 554.7 | 17110.0 | 7.63 | 91 | peak_rss_mb>14336 | single_region_bake_now |
| north_america_canada_quebec | 308.1 | 11189.3 | 1.92 | 437 | added_disk_delta_too_large | single_region_bake_now |
| africa_morocco | 220.9 | 9699.0 | 3.13 | 323 | added_disk_delta_too_large | single_region_bake_now |
| australia_oceania_new_zealand | 143.8 | 3731.0 | 1.69 | 10440 | added_disk_delta_too_large | single_region_bake_now |
| south_america_argentina | 409.7 | 21335.5 | 4.12 | 735 | peak_rss_mb>14336 | single_region_bake_now |
| asia_indonesia_nusa_tenggara | 92.1 | 4507.7 | 1.0 | 90 | added_disk_delta_too_large | single_region_bake_now |
| north_america_canada_british_columbia_okanagan_admreg | 19.5 | 1461.5 | 0.2 | 30 | added_disk_delta_too_large | single_region_bake_now |
| europe_sweden_skane | 23.0 | 1845.7 | 0.45 | 6 | added_disk_delta_too_large | single_region_bake_now |
| asia_japan_kansai | 162.6 | 9158.3 | 3.04 | 20 | added_disk_delta_too_large | single_region_bake_now |
| south_america_brazil_centro_oeste | 211.0 | 11322.8 | 2.07 | 306 | added_disk_delta_too_large | single_region_bake_now |
| north_america_canada_newfoundland_and_labrador | 65.1 | 3461.9 | 0.24 | 408 | added_disk_delta_too_large | single_region_bake_now |
| north_america_us_maine | 46.4 | 3413.6 | 0.72 | 36 | added_disk_delta_too_large | single_region_bake_now |
| europe_germany_bremen | 11.1 | 548.4 | 0.08 | 1 | added_disk_delta_too_large | single_region_bake_now |
| south_america_brazil_sul | 275.3 | 9156.9 | 4.23 | 182 | added_disk_delta_too_large | single_region_bake_now |
| south_america_brazil_sudeste | 554.8 | 14812.1 | 7.67 | 312 | peak_rss_mb>14336 | single_region_bake_now |
| north_america_canada_ontario | 379.3 | 12852.8 | 3.15 | 374 | added_disk_delta_too_large | single_region_bake_now |
| australia_oceania_australia_queensland | 182.9 | 12148.5 | 1.22 | 396 | added_disk_delta_too_large | single_region_bake_now |
| europe_germany_rheinland_pfalz | 99.9 | 5454.7 | 1.69 | 9 | added_disk_delta_too_large | single_region_bake_now |
| europe_italy_nord_est | 231.3 | 9737.5 | 3.24 | 25 | added_disk_delta_too_large | single_region_bake_now |
| asia_japan_chubu | 242.8 | 13073.2 | 4.38 | 25 | added_disk_delta_too_large | single_region_bake_now |
| europe_spain_cataluna | 131.2 | 6903.6 | 2.22 | 15 | added_disk_delta_too_large | single_region_bake_now |
| europe_united_kingdom_england_isle_of_wight | 4.8 | 355.1 | 0.03 | 1 | added_disk_delta_too_large | single_region_bake_now |
| europe_sweden_gotland | 4.4 | 513.5 | 0.05 | 9 | added_disk_delta_too_large | single_region_bake_now |
| north_america_canada_nova_scotia | 37.4 | 2831.7 | 0.42 | 60 | added_disk_delta_too_large | single_region_bake_now |
| europe_poland_zachodniopomorskie | 33.8 | 2213.1 | 0.54 | 16 | added_disk_delta_too_large | single_region_bake_now |
| europe_spain_islas_baleares | 17.6 | 1453.5 | 0.26 | 15 | added_disk_delta_too_large | single_region_bake_now |
| asia_china_hainan | 10.6 | 1317.2 | 0.15 | 77 | added_disk_delta_too_large | single_region_bake_now |
| europe_united_kingdom_england_kent | 20.8 | 1287.6 | 0.34 | 4 | added_disk_delta_too_large | single_region_bake_now |
| europe_france_basse_normandie | 57.8 | 2373.7 | 0.73 | 8 | added_disk_delta_too_large | single_region_bake_now |
| europe_sweden_blekinge | 6.6 | 595.4 | 0.08 | 6 | added_disk_delta_too_large | single_region_bake_now |
| asia_vietnam | 369.8 | 15165.7 | 8.08 | 70 | peak_rss_mb>14336 | single_region_bake_now |
| europe_united_kingdom_wales | 54.8 | 3044.3 | 0.84 | 15 | added_disk_delta_too_large | single_region_bake_now |
| asia_east_timor | 9.7 | 820.1 | 0.08 | 10 | added_disk_delta_too_large | single_region_bake_now |
| europe_france_bretagne | 110.7 | 4709.5 | 1.54 | 15 | added_disk_delta_too_large | single_region_bake_now |
| europe_united_kingdom_scotland | 117.0 | 6487.8 | 1.81 | 128 | added_disk_delta_too_large | single_region_bake_now |
| asia_china_guangdong | 106.1 | 7173.7 | 1.95 | 54 | added_disk_delta_too_large | single_region_bake_now |
| europe_sweden_vastra_gotaland | 53.6 | 3548.5 | 0.91 | 15 | added_disk_delta_too_large | single_region_bake_now |
| africa_madagascar | 273.4 | 9463.2 | 2.82 | 160 | added_disk_delta_too_large | single_region_bake_now |
| europe_netherlands_zuid_holland | 101.0 | 2074.4 | 0.53 | 6 | added_disk_delta_too_large | single_region_bake_now |
| asia_japan_kyushu | 167.8 | 9233.6 | 3.15 | 165 | added_disk_delta_too_large | single_region_bake_now |
| europe_lithuania | 84.6 | 4423.6 | 1.25 | 28 | added_disk_delta_too_large | single_region_bake_now |
| europe_netherlands_utrecht | 45.2 | 1153.2 | 0.22 | 4 | added_disk_delta_too_large | single_region_bake_now |
| europe_united_kingdom_england_devon | 22.8 | 1604.9 | 0.39 | 6 | added_disk_delta_too_large | single_region_bake_now |
| south_america_bolivia | 185.2 | 9550.3 | 2.09 | 182 | added_disk_delta_too_large | single_region_bake_now |
| europe_isle_of_man | 4.4 | 393.3 | 0.03 | 6 | added_disk_delta_too_large | single_region_bake_now |
| north_america_us_alaska | 35.9 | 1088.5 | 0.38 | 8664 | added_disk_delta_too_large | single_region_bake_now |
| africa_congo_democratic_republic | 355.4 | 14475.9 | 2.14 | 420 | peak_rss_mb>14336 | single_region_bake_now |
| south_america_colombia | 263.7 | 10620.3 | 3.11 | 396 | added_disk_delta_too_large | single_region_bake_now |
| africa_senegal_and_gambia | 80.6 | 4894.5 | 1.46 | 45 | added_disk_delta_too_large | single_region_bake_now |
| north_america_us_michigan | 196.9 | 12086.5 | 3.9 | 72 | added_disk_delta_too_large | single_region_bake_now |
| north_america_us_north_carolina | 236.3 | 10991.3 | 4.03 | 48 | added_disk_delta_too_large | single_region_bake_now |
| north_america_canada_british_columbia_north_admreg | 122.2 | 4537.3 | 0.26 | 250 | added_disk_delta_too_large | single_region_bake_now |
| europe_guernsey_jersey | 3.2 | 385.4 | 0.03 | 6 | added_disk_delta_too_large | single_region_bake_now |
| europe_iceland | 29.5 | 1998.1 | 0.3 | 84 | added_disk_delta_too_large | single_region_bake_now |
| north_america_us_connecticut | 75.6 | 3806.1 | 1.12 | 9 | added_disk_delta_too_large | single_region_bake_now |
| south_america_guyana | 20.0 | 1642.1 | 0.07 | 54 | added_disk_delta_too_large | single_region_bake_now |
| europe_spain_valencia | 67.4 | 4099.3 | 1.24 | 16 | added_disk_delta_too_large | single_region_bake_now |
| … | | | | | | +125 more |

#### One-off bake outline (per cut-off leaf)

```bash
cd /media/navi/navi-server
set -a; source data/config.env; set +a
export NAVI_FERRY_LINKS_REGIONS=<bake_id>
./scripts/run-weekly.sh --region <bake_id>
```

Examples from this cut-off list:

- `south_america_brazil_norte` — reason `peak_rss_mb>14336`:
  ```bash
  export NAVI_FERRY_LINKS_REGIONS=south_america_brazil_norte
  ./scripts/run-weekly.sh --region south_america_brazil_norte
  ```
- `asia_india_southern_zone` — reason `peak_rss_mb>14336`:
  ```bash
  export NAVI_FERRY_LINKS_REGIONS=asia_india_southern_zone
  ./scripts/run-weekly.sh --region asia_india_southern_zone
  ```
- `south_america_chile` — reason `added_disk_delta_too_large`:
  ```bash
  export NAVI_FERRY_LINKS_REGIONS=south_america_chile
  ./scripts/run-weekly.sh --region south_america_chile
  ```
- `asia_indonesia_java` — reason `peak_rss_mb>14336`:
  ```bash
  export NAVI_FERRY_LINKS_REGIONS=asia_indonesia_java
  ./scripts/run-weekly.sh --region asia_indonesia_java
  ```
- `north_america_canada_quebec` — reason `added_disk_delta_too_large`:
  ```bash
  export NAVI_FERRY_LINKS_REGIONS=north_america_canada_quebec
  ./scripts/run-weekly.sh --region north_america_canada_quebec
  ```
- `africa_morocco` — reason `added_disk_delta_too_large`:
  ```bash
  export NAVI_FERRY_LINKS_REGIONS=africa_morocco
  ./scripts/run-weekly.sh --region africa_morocco
  ```
- `australia_oceania_new_zealand` — reason `added_disk_delta_too_large`:
  ```bash
  export NAVI_FERRY_LINKS_REGIONS=australia_oceania_new_zealand
  ./scripts/run-weekly.sh --region australia_oceania_new_zealand
  ```
- `south_america_argentina` — reason `peak_rss_mb>14336`:
  ```bash
  export NAVI_FERRY_LINKS_REGIONS=south_america_argentina
  ./scripts/run-weekly.sh --region south_america_argentina
  ```


_No planet-wide bake. Weekly (`data/regions.conf`) is the only regular bake. Leaf regions that need ferry links and fit the budget: add to weekly + set NAVI_FERRY_LINKS_REGIONS. Over-budget / cut-off leaves: run a targeted single-region bake now (`./scripts/run-weekly.sh --region <id>` with NAVI_FERRY_LINKS_REGIONS=<id>) so ferry-links/v9 land without a planet run; reconsider weekly membership only after they fit. Do not edit live config from this analysis._

## 4. Converter problem candidates (do not fix here)

OSM ids / pack edges where packs disagree with `ferry_allowed_for_profile` or expected car set. Published packs do **not** retain OSM way ids in edge ids (rebuilt as `src-tgt-i` on load); matching uses endpoint proximity. Investigation only — no converter changes in this PR.

- `africa_cameroon` missing_from_pack way/313541314 name='' class=None
- `africa_cameroon` missing_from_pack way/319680517 name='' class=None
- `africa_guinea` missing_from_pack way/264853058 name='' class=None
- `africa_morocco` missing_from_pack way/1385371951 name='Gibraltar (GBZ) - Tanger Med (MA) / طنجة المتوسط - الجزيرة الخضراء' class=None
- `africa_morocco` missing_from_pack way/1458553353 name='Tanger Med (MA) - Gibraltar (GBZ) / الجزيرة الخضراء - طنجة المتوسط' class=None
- `asia_china_guangdong` missing_from_pack way/725726898 name='海安新港 - 秀英港' class=None
- `asia_china_jiangxi` missing_from_pack way/439324987 name='' class=None
- `asia_china_zhejiang` missing_from_pack way/925836430 name='五龙 - 黄龙' class=None
- `asia_gcc_states` missing_from_pack way/162803301 name='نويبع - العقبة' class=None
- `asia_gcc_states` missing_from_pack way/1156952057 name='نويبع - العقبة' class=None
- `asia_india_southern_zone` wrongly_admitted_passenger way/1465620148 name='Vada Canal' class=passenger-bicycle-only
- `asia_india_southern_zone` wrongly_admitted_passenger way/1465620148 name='Vada Canal' class=passenger-bicycle-only
- `asia_india_southern_zone` missing_from_pack way/123410296 name='' class=None
- `asia_indonesia_java` missing_from_pack way/49567223 name='Bakauheni - Merak' class=None
- `asia_indonesia_java` missing_from_pack way/542838202 name='Bakauheni - Merak' class=None
- `asia_indonesia_java` missing_from_pack way/542838203 name='Bakauheni - Merak' class=None
- `asia_indonesia_java` missing_from_pack way/542838204 name='Bakauheni - Merak' class=None
- `asia_indonesia_java` missing_from_pack way/1062758574 name='' class=None
- `asia_indonesia_java` missing_from_pack way/1062758575 name='Merak - Bakauheni' class=None
- `asia_indonesia_java` missing_from_pack way/1062758576 name='Bakauheni - Merak' class=None
- `asia_indonesia_kalimantan` missing_from_pack way/543985211 name='Tanjung Kalian (Muntok) - Tanjung Api-Api' class=None
- `asia_indonesia_nusa_tenggara` missing_from_pack way/12720894 name='Ketapang - Gilimanuk' class=None
- `asia_indonesia_nusa_tenggara` missing_from_pack way/38654027 name='Ketapang - Gilimanuk' class=None
- `asia_indonesia_nusa_tenggara` missing_from_pack way/113797215 name='Ketapang - Gilimanuk' class=None
- `asia_indonesia_nusa_tenggara` missing_from_pack way/1043152894 name='Rote - Ndao' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1040349307 name='Jampea - Bonerate' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1040363976 name='Jampea - Kalaotoa' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1040681427 name='Kayuadi - Bonerate' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1040889969 name='Kayuadi - Jampea' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1040910116 name='Kayuadi - Kalaotoa' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1041174648 name='Pattumbukan - Kayuadi' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1041185766 name='Pattumbukan - Kalaotoa' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1042766822 name='Lirung - Melonguane' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1043074984 name='Labuan bajo - Jampea' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1135074935 name='Jampea - Maropokot' class=None
- `asia_indonesia_sulawesi` missing_from_pack way/1136163998 name='Bonerate—Kalaotoa' class=None
- `asia_indonesia_sumatra` missing_from_pack way/49567223 name='Bakauheni - Merak' class=None
- `asia_indonesia_sumatra` missing_from_pack way/542828094 name='' class=None
- `asia_indonesia_sumatra` missing_from_pack way/542828095 name='' class=None
- `asia_indonesia_sumatra` missing_from_pack way/542828096 name='' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1062758575 name='Merak - Bakauheni' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1389223599 name='Matak-Midai' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1389223601 name='Midai-Penagi' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1389223603 name='Penagi-Subi' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1389223604 name='Subi-Serasan' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1389223606 name='Subi-Serasan' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1389223607 name='Serasan-Sintete' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1389227664 name='Tambelan-Sintete' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1389776416 name='Serasan-Sintete' class=None
- `asia_indonesia_sumatra` missing_from_pack way/1416294248 name='Subi-Serasan' class=None
- `asia_japan_chugoku` wrongly_admitted_passenger way/860085891 name='マルト汽船(三原－小佐木－向田－沢－瀬戸田)' class=passenger-bicycle-only
- `asia_japan_chugoku` wrongly_admitted_passenger way/860085891 name='マルト汽船(三原－小佐木－向田－沢－瀬戸田)' class=passenger-bicycle-only
- `asia_japan_chugoku` missing_from_pack way/439823187 name='平郡航路(柳井-平郡西-平郡東)' class=None
- `asia_japan_kyushu` wrongly_admitted_passenger way/174078910 name='太古' class=passenger-bicycle-only
- `asia_japan_kyushu` wrongly_admitted_passenger way/174078910 name='太古' class=passenger-bicycle-only
- `asia_japan_kyushu` wrongly_admitted_passenger way/424709716 name='太古' class=passenger-bicycle-only
- `asia_japan_kyushu` wrongly_admitted_passenger way/424709716 name='太古' class=passenger-bicycle-only
- `asia_japan_tohoku` missing_from_pack way/371187975 name='ノスタルジック航路' class=None
- `asia_philippines` missing_from_pack way/164190578 name='San Agustin-Romblon-Ambulong (Sibuyan)' class=None
- `asia_philippines` missing_from_pack way/1083169364 name='Romblon-Ambulong (Sibuyan)' class=None
- `asia_south_korea` missing_from_pack way/591331580 name='도초도 - 우이도' class=None
- `asia_south_korea` missing_from_pack way/1136342531 name='하모니플라워호 (인천-소청-대청-백령 / Incheon-Socheong-Daecheong-Baengnyeong)' class=None
- `asia_south_korea` missing_from_pack way/1136342532 name='하모니플라워호 (인천-소청-대청-백령 / Incheon-Socheong-Daecheong-Baengnyeong)' class=None
- `asia_taiwan` missing_from_pack way/435002819 name='水頭 - 九宮' class=None
- `australia_oceania_australia_western_australia` missing_from_pack way/616191300 name='Dirk Hartog Island 4WD barge transfer' class=None
- `europe_austria` missing_from_pack way/291426240 name='' class=None
- `europe_croatia` missing_from_pack way/229050289 name='Split - Rogač (Šolta) - Stomorska (Šolta) - Milna (Brač)' class=None
- `europe_denmark` wrongly_admitted_passenger way/690694736 name='Rostock (D) - Trelleborg (SE)' class=passenger-bicycle-only
- `europe_denmark` wrongly_admitted_passenger way/690694736 name='Rostock (D) - Trelleborg (SE)' class=passenger-bicycle-only
- `europe_denmark` missing_from_pack way/1349018822 name='Rostock (D) – Gedser (S)' class=None
- `europe_finland` missing_from_pack way/212119427 name='Paldiski (EST) - Kapellskär (S)' class=None
- `europe_finland` missing_from_pack way/296301127 name='Grisslehamn-Eckerö' class=None
- `europe_finland` missing_from_pack way/342899626 name='Eckerö - Grisslehamn' class=None
- `europe_finland` missing_from_pack way/939184694 name='' class=None
- `europe_france_bretagne` missing_from_pack way/201337669 name='Portsmouth - Santander' class=None
- `europe_france_bretagne` missing_from_pack way/913690987 name='Rosslare (IRL) - Bilbao (E)' class=None
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_nord_pas_de_calais` wrongly_admitted_passenger way/1426305721 name='Dover - Dunkerque' class=passenger-bicycle-only
- `europe_france_provence_alpes_cote_d_azur` missing_from_pack way/34138560 name='Bac du Sauvage' class=None
- `europe_germany_bayern_niederbayern` missing_from_pack way/291426240 name='' class=None
- `europe_germany_brandenburg` missing_from_pack way/23240727 name='Pevestorf-Lenzen' class=None
- `europe_germany_hessen` missing_from_pack way/30093549 name='Fähre Neckarhausen - Neckarhäuserhof' class=None
- `europe_germany_hessen` missing_from_pack way/131749708 name='Fähre Niederheimbach - Lorch' class=None
- `europe_germany_niedersachsen` wrongly_admitted_passenger way/640417643 name='Anleger Borkumfähre' class=passenger-bicycle-only
- `europe_germany_niedersachsen` wrongly_admitted_passenger way/640417643 name='Anleger Borkumfähre' class=passenger-bicycle-only
- `europe_germany_niedersachsen` wrongly_admitted_passenger way/640417643 name='Anleger Borkumfähre' class=passenger-bicycle-only
- `europe_germany_niedersachsen` wrongly_admitted_passenger way/640417643 name='Anleger Borkumfähre' class=passenger-bicycle-only
- `europe_germany_niedersachsen` wrongly_admitted_passenger way/640417643 name='Anleger Borkumfähre' class=passenger-bicycle-only
- `europe_germany_niedersachsen` wrongly_admitted_passenger way/640417643 name='Anleger Borkumfähre' class=passenger-bicycle-only
- `europe_germany_niedersachsen` missing_from_pack way/23240727 name='Pevestorf-Lenzen' class=None
- `europe_germany_schleswig_holstein` missing_from_pack way/34142186 name='Sylt - Rømø (List - Havneby)' class=None
- `europe_germany_schleswig_holstein` missing_from_pack way/47681503 name='Rødby (DK) - Puttgarden (D)' class=None
- `europe_germany_schleswig_holstein` missing_from_pack way/1280194263 name='Rødby (DK) - Puttgarden (D)' class=None
- `europe_greece` missing_from_pack way/52782960 name='Ρόδος - Καστελόριζο' class=None
- `europe_greece` missing_from_pack way/398541588 name='Μarmaris - Ρόδος' class=None
- `europe_isle_of_man` missing_from_pack way/146224644 name='Belfast - Liverpool (Birkenhead)' class=None
- `europe_italy_centro` missing_from_pack way/174347426 name='Marseille - Bastia' class=None
- `europe_italy_centro` missing_from_pack way/179468003 name='Toulon - Bastia' class=None
- `europe_italy_centro` missing_from_pack way/179580212 name='Nice - Bastia' class=None
- `europe_italy_centro` missing_from_pack way/961937671 name='Savona - Golfo Aranci' class=None
- `europe_italy_centro` missing_from_pack way/1340185482 name='Savona - Bastia' class=None
- `europe_italy_centro` missing_from_pack way/1340185485 name='Nizza - Porto Vecchio' class=None
- `europe_italy_isole` missing_from_pack way/174347425 name='Marseille - Porto Vecchio' class=None
- `europe_italy_isole` missing_from_pack way/879849972 name='Porto Vecchio - Toulon' class=None
- `europe_italy_nord_ovest` wrongly_admitted_passenger way/39264221 name=None class=passenger-bicycle-only
- `europe_italy_nord_ovest` wrongly_admitted_passenger way/39264221 name=None class=passenger-bicycle-only
- `europe_italy_nord_ovest` wrongly_admitted_passenger way/39264221 name=None class=passenger-bicycle-only
- `europe_italy_nord_ovest` wrongly_admitted_passenger way/39264221 name=None class=passenger-bicycle-only
- `europe_italy_nord_ovest` wrongly_admitted_passenger way/39264221 name=None class=passenger-bicycle-only
- `europe_italy_nord_ovest` wrongly_admitted_passenger way/39264221 name=None class=passenger-bicycle-only
- `europe_latvia` missing_from_pack way/1037378259 name='Балтийск — Усть-Луга' class=None
- `europe_norway_nord_norge` wrongly_admitted_passenger way/649227197 name='Skoleruta i Rognsundet' class=passenger-bicycle-only
- `europe_norway_nord_norge` wrongly_admitted_passenger way/649227197 name='Skoleruta i Rognsundet' class=passenger-bicycle-only
- `europe_norway_sorlandet` missing_from_pack way/447886765 name='Hirtshals - Tórshavn' class=None
- `europe_norway_sorlandet` missing_from_pack way/539425960 name='Hirtshals - Stavanger' class=None
- `europe_norway_trondelag` missing_from_pack way/23112418 name='Halsa - Kanestraum' class=None
- `europe_norway_vestlandet` wrongly_admitted_passenger way/1331450454 name='Eidssund - Nord-Hidle' class=passenger-bicycle-only
- `europe_norway_vestlandet` wrongly_admitted_passenger way/1331450454 name='Eidssund - Nord-Hidle' class=passenger-bicycle-only
- `europe_norway_vestlandet` wrongly_admitted_passenger way/1419154531 name=None class=passenger-bicycle-only
- `europe_norway_vestlandet` wrongly_admitted_passenger way/1419154531 name=None class=passenger-bicycle-only
- `europe_norway_vestlandet` wrongly_admitted_passenger way/1419147149 name=None class=passenger-bicycle-only
- `europe_norway_vestlandet` wrongly_admitted_passenger way/1419147149 name=None class=passenger-bicycle-only
- `europe_norway_vestlandet` missing_from_pack way/447886765 name='Hirtshals - Tórshavn' class=None
- `europe_norway_vestlandet` missing_from_pack way/898471802 name='' class=None
- `europe_poland_mazowieckie` missing_from_pack way/188897291 name='Sezonowa przeprawa promowa Mielnik Przedmieście — Zabuże' class=None
- `europe_poland_zachodniopomorskie` missing_from_pack way/138471072 name='Sassnitz - Rønne' class=None
- `europe_romania` missing_from_pack way/436245677 name='Svishtov - Zimnichea' class=None
- `europe_slovakia` missing_from_pack way/27877592 name='Vysoká pri Morave - Angern' class=None
- `europe_slovenia` missing_from_pack way/387310966 name='Skela Križovec' class=None
- `europe_spain_andalucia` missing_from_pack way/362003444 name='Tanger - Tarifa / طنجة - طريفة' class=None
- `europe_spain_andalucia` missing_from_pack way/685602674 name='Tanger - Tarifa / طنجة - طريفة' class=None
- `europe_spain_andalucia` missing_from_pack way/1385371951 name='Gibraltar (GBZ) - Tanger Med (MA) / طنجة المتوسط - الجزيرة الخضراء' class=None
- `europe_spain_andalucia` missing_from_pack way/1458553353 name='Tanger Med (MA) - Gibraltar (GBZ) / الجزيرة الخضراء - طنجة المتوسط' class=None
- `europe_spain_murcia` missing_from_pack way/309885392 name='Sète – Tanger Med / طنجة – سيت' class=None
- `europe_spain_murcia` missing_from_pack way/1239419340 name='Marseille - Tánger Med' class=None
- `europe_sweden_blekinge` missing_from_pack way/1550075468 name='Gdańsk (PL) - Karlshamn (SE)' class=None
- `europe_sweden_kalmar` missing_from_pack way/707334351 name='Travemünde (D) – Helsinki (FIN)' class=None
- `europe_sweden_norrbotten` missing_from_pack way/224796102 name='Röduppleden' class=None
- `europe_sweden_skane` wrongly_admitted_passenger way/1094196406 name='Świnoujście - Ystad' class=passenger-bicycle-only
- `europe_sweden_skane` wrongly_admitted_passenger way/1094196406 name='Świnoujście - Ystad' class=passenger-bicycle-only
- `europe_sweden_vastra_gotaland` wrongly_admitted_passenger way/1101012358 name='Frederikshavn - Göteborg' class=passenger-bicycle-only
- `europe_sweden_vastra_gotaland` wrongly_admitted_passenger way/1101012358 name='Frederikshavn - Göteborg' class=passenger-bicycle-only
- `europe_turkey` missing_from_pack way/240812666 name='Geyikli - Bozcaada' class=None
- `europe_turkey` missing_from_pack way/300407598 name='Πειραιάς - Χίος' class=None
- `europe_united_kingdom_scotland` missing_from_pack way/447886765 name='Hirtshals - Tórshavn' class=None
- `north_america_canada_nova_scotia` missing_from_pack way/169052486 name='Grand Passage Ferry' class=None
- `north_america_canada_ontario` missing_from_pack way/116731176 name='' class=None
- `north_america_canada_ontario` missing_from_pack way/127581178 name='Sandusky - Pelee Island' class=None
- `north_america_canada_ontario` missing_from_pack way/179816200 name='Kingsville - Pelee Island' class=None
- `north_america_canada_ontario` missing_from_pack way/717004443 name='Leamington - Pelee Island' class=None
- `north_america_canada_quebec` missing_from_pack way/38110057 name='Blanc-Sablon – Sainte-Barbe' class=None
- `north_america_canada_quebec` missing_from_pack way/320741014 name='Saint-Augustin/Blanc-Sablon' class=None
- `north_america_canada_quebec` missing_from_pack way/330995736 name='Saint-Augustin/Blanc-Sablon' class=None
- `north_america_canada_quebec` missing_from_pack way/331320177 name='Saint-Augustin/Blanc-Sablon' class=None
- `north_america_canada_quebec` missing_from_pack way/334796057 name='La Tabatière/Saint-Augustin' class=None
- `north_america_canada_quebec` missing_from_pack way/334796070 name='Saint-Augustin/Blanc-Sablon' class=None
- `north_america_canada_quebec` missing_from_pack way/334796072 name='Saint-Augustin/Pointe-à-la-Truite' class=None
- `north_america_us_maine` wrongly_admitted_passenger way/1547672289 name='Rockland - Matinicus' class=passenger-bicycle-only
- `north_america_us_maine` wrongly_admitted_passenger way/1547672289 name='Rockland - Matinicus' class=passenger-bicycle-only
- `north_america_us_missouri` missing_from_pack way/109311013 name='Dorena-Hickman Ferry' class=None
- `north_america_us_new_york` missing_from_pack way/609332006 name='New London Terminal - Fishers Island' class=None
- `north_america_us_wisconsin` missing_from_pack way/25655112 name='Washington Island Ferry Line' class=None
- `russia_crimean_fed_district` missing_from_pack way/32806423 name='Кавказ — Крым' class=None
- `south_america_bolivia` missing_from_pack way/31525498 name='Abunã - Fortaleza do Abunã' class=None
- `south_america_bolivia` missing_from_pack way/317315736 name='' class=None
- `south_america_brazil_centro_oeste` missing_from_pack way/486878894 name='Balsa Rio Roosevelt' class=None
- `south_america_brazil_nordeste` missing_from_pack way/358457490 name='São Sebastião do Tocantins - Vila Nova dos Martírios' class=None
- `south_america_brazil_norte` missing_from_pack way/361348335 name='Balsa Manaus x Nhamandu' class=None
- `south_america_brazil_norte` missing_from_pack way/595197491 name='Floresta - Bela Vista - Itaquera' class=None
- `south_america_brazil_norte` missing_from_pack way/709678352 name='Sacaí - Foz do rio Branco' class=None
- `south_america_brazil_norte` missing_from_pack way/751608858 name='Manaus - Belém' class=None
- `south_america_brazil_norte` missing_from_pack way/753103071 name='Manaus - Belém' class=None
- `south_america_brazil_sudeste` missing_from_pack way/286995646 name='Cananéia - Ilha Comprida' class=None
- `south_america_brazil_sudeste` missing_from_pack way/345807829 name='Cananéia - Continente' class=None
- `south_america_brazil_sudeste` missing_from_pack way/346916108 name='Manga - Matias Cardoso' class=None
- `south_america_brazil_sudeste` missing_from_pack way/398212557 name='Mocambinho - Itacarambi' class=None
- `south_america_brazil_sudeste` missing_from_pack way/1434122458 name='' class=None
- `south_america_brazil_sul` missing_from_pack way/228185234 name='' class=None
- `south_america_brazil_sul` missing_from_pack way/273429268 name='Alvear - Itaqui' class=None
- `south_america_brazil_sul` missing_from_pack way/673384334 name='' class=None
- `south_america_brazil_sul` missing_from_pack way/673384337 name='Canal Miguel da Cunha' class=None
- `south_america_brazil_sul` missing_from_pack way/673384338 name='' class=None
- `south_america_brazil_sul` missing_from_pack way/967676507 name='Travessia do Rio Paraná (balsa)' class=None
- `south_america_brazil_sul` missing_from_pack way/1466618604 name='' class=None
- `south_america_colombia` wrongly_admitted_passenger way/1150705134 name=None class=passenger-bicycle-only
- `south_america_colombia` wrongly_admitted_passenger way/1150705134 name=None class=passenger-bicycle-only
- `south_america_peru` missing_from_pack way/687707495 name='Pontón de Vehículos' class=None

### Terminal audit samples (`no_road` / `tiny`)

#### south_america_brazil_norte (gen `20260929T033755Z-180067-south_america_brazil_norte-649c6551`)

- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=5670473296 lat=1.8150846 lon=-61.126406 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=13009074808 lat=1.8142484 lon=-61.126330700000004 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=5670473297 lat=1.8128579 lon=-61.1262054 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=5670474914 lat=0.5748875 lon=-61.613114700000004 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=6648191199 lat=0.5328620000000001 lon=-61.6296169 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=6657488660 lat=1.7388686000000002 lon=-61.1433121 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=6657488652 lat=1.7306320000000002 lon=-61.149229000000005 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=5670475026 lat=0.48491120000000004 lon=-61.694368600000004 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=6660698846 lat=0.4686173 lon=-61.692306 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=6528329161 lat=1.8190257 lon=-61.124521800000004 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=5670475023 lat=0.5042458000000001 lon=-61.662246700000004 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=5670475045 lat=0.39189690000000005 lon=-61.7205044 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=5670475094 lat=0.0615846 lon=-61.764003100000004 comp_nodes=0
- `no_road` name='Caracaraí - Santa mª do Boiaçu' node=5675192965 lat=0.4833481 lon=-61.701633400000006 comp_nodes=0
- `tiny` name='Caracaraí - Santa mª do Boiaçu' node=5670475242 lat=-0.5094539 lon=-61.789219800000005 comp_nodes=21
- `tiny` name='Caracaraí - Sacaí' node=5670475269 lat=-0.7468308 lon=-61.864041400000005 comp_nodes=4
- `tiny` name='Água Boa do Univini' node=7322276937 lat=0.8000871 lon=-61.65905300000001 comp_nodes=6
- `tiny` name='Balsa do cajueiro' node=1428577160 lat=-9.4529958 lon=-56.491485700000005 comp_nodes=4
- `no_road` name=None node=6225362269 lat=-9.0462947 lon=-56.587458600000005 comp_nodes=0
- `no_road` name=None node=3755058776 lat=-9.0452727 lon=-56.58740040000001 comp_nodes=0
- `tiny` name=None node=3277382686 lat=-9.1506355 lon=-67.4366163 comp_nodes=17
- `no_road` name='Porto Velho, RO - Democracia, AM' node=6476821157 lat=-7.491323800000001 lon=-63.0180756 comp_nodes=0
- `tiny` name=None node=2671442370 lat=-7.265354100000001 lon=-55.4097055 comp_nodes=5
- `no_road` name=None node=7567988762 lat=-6.4004431 lon=-48.552324600000006 comp_nodes=0
- `no_road` name=None node=316508922 lat=-6.410438200000001 lon=-48.5438016 comp_nodes=0
- … +88 more

#### europe_norway_vestlandet (gen `20261001T135715Z-625025-europe_norway_vestlandet-d5ab462f`)

- `no_road` name='Hirtshals - Stavanger' node=1117218378 lat=57.595890000000004 lon=9.9755886 comp_nodes=0
- `no_road` name=None node=3314843572 lat=60.3916599 lon=5.3065688 comp_nodes=0
- `tiny` name=None node=7211418531 lat=59.0474983 lon=5.8278818 comp_nodes=23
- `no_road` name=None node=8349228973 lat=59.092550700000004 lon=5.7937002 comp_nodes=0
- `no_road` name=None node=8349229139 lat=59.08588460000001 lon=5.7901543 comp_nodes=0
- `no_road` name=None node=8349229239 lat=59.063344300000004 lon=5.9088528 comp_nodes=0
- `no_road` name=None node=8349228718 lat=59.0118633 lon=6.263120300000001 comp_nodes=0
- `tiny` name=None node=4849879374 lat=59.0152657 lon=6.431332500000001 comp_nodes=22
- `no_road` name=None node=7218445473 lat=59.0402781 lon=6.5137483000000005 comp_nodes=0
- `tiny` name=None node=4976832130 lat=59.1591237 lon=5.9698389 comp_nodes=43
- `no_road` name=None node=8349229005 lat=59.052099000000005 lon=5.836358000000001 comp_nodes=0
- `no_road` name=None node=8349228949 lat=59.239189800000005 lon=5.9059882 comp_nodes=0
- `no_road` name=None node=8349229420 lat=59.2408689 lon=5.9045505 comp_nodes=0
- `no_road` name=None node=8349229117 lat=59.0621943 lon=5.8969331 comp_nodes=0
- `no_road` name=None node=8349229149 lat=59.0614111 lon=5.9045291 comp_nodes=0
- `no_road` name=None node=5843435091 lat=59.0659338 lon=5.882492 comp_nodes=0
- `tiny` name=None node=7211418555 lat=59.026138800000005 lon=5.895715 comp_nodes=13
- `tiny` name=None node=5063887035 lat=59.0861274 lon=5.7972567 comp_nodes=19
- `tiny` name=None node=6074515011 lat=59.16188760000001 lon=5.9467607000000005 comp_nodes=17
- `no_road` name=None node=8349229140 lat=59.01021600000001 lon=5.8401269000000005 comp_nodes=0
- `no_road` name=None node=7211418448 lat=59.1541402 lon=5.9681832 comp_nodes=0
- `no_road` name=None node=1392480315 lat=59.0132094 lon=6.2905943 comp_nodes=0
- `no_road` name=None node=390878296 lat=59.0089161 lon=6.393690100000001 comp_nodes=0
- `no_road` name=None node=8349228983 lat=59.084393600000006 lon=5.7914257000000005 comp_nodes=0
- `no_road` name='Byøyene' node=8349228940 lat=58.9740429 lon=5.744123 comp_nodes=0
- … +49 more

#### asia_south_korea (gen `20260929T141614Z-180067-asia_south_korea-e162027b`)

- `tiny` name='관사도-소마도' node=5652770048 lat=34.309237 lon=125.97772520000001 comp_nodes=18
- `tiny` name='관사도-소마도' node=8948442455 lat=34.3008159 lon=125.98286660000001 comp_nodes=8
- `tiny` name='하조도-대마도' node=8948454611 lat=34.2708139 lon=125.99747730000001 comp_nodes=25
- `no_road` name='하조도-상하죽도' node=2805669527 lat=34.250177 lon=125.92351860000001 comp_nodes=0
- `no_road` name='하조도-서거차도' node=2805669908 lat=34.253843100000005 lon=125.91606990000001 comp_nodes=0
- `tiny` name=None node=8910693000 lat=34.2903587 lon=127.3594527 comp_nodes=18
- `no_road` name='목포(북항) - 도초' node=5645478832 lat=34.7155544 lon=125.9360467 comp_nodes=0
- `tiny` name='장산도(북강) - 기도' node=5721802578 lat=34.6352172 lon=126.08616660000001 comp_nodes=4
- `tiny` name='옥도 - 장병도' node=6325152699 lat=34.6532943 lon=126.0526138 comp_nodes=23
- `no_road` name='비금-흑산' node=5400749611 lat=34.6845743 lon=125.44157200000001 comp_nodes=0
- `tiny` name='계마항-송이도-대석만도' node=8948442074 lat=35.3725952 lon=126.05553210000001 comp_nodes=4
- `tiny` name='장산도(북강) - 막금도' node=8949539462 lat=34.621283600000005 lon=126.1259983 comp_nodes=12
- `tiny` name='향화도-대각씨도-상낙월도' node=5418097512 lat=35.1999304 lon=126.144052 comp_nodes=4
- `no_road` name='압해도 - 매화도' node=5163148418 lat=34.918975800000005 lon=126.21856500000001 comp_nodes=0
- `tiny` name='고이 - 신월' node=5170312838 lat=34.9602457 lon=126.2902298 comp_nodes=26
- `tiny` name='복지선착장 - 하사치도' node=6317617869 lat=34.7551085 lon=126.06118660000001 comp_nodes=46
- `no_road` name='주지도-가사도' node=244803343 lat=34.486430500000004 lon=126.08579230000001 comp_nodes=0
- `tiny` name='송도-혈도' node=5665508793 lat=34.5181805 lon=126.0966077 comp_nodes=2
- `tiny` name='송도-혈도' node=5665508552 lat=34.517203900000005 lon=126.0868312 comp_nodes=11
- `no_road` name='혈도-양덕도' node=5665508007 lat=34.494464300000004 lon=126.10536440000001 comp_nodes=0
- `tiny` name='광대도-송도' node=5666338601 lat=34.529234200000005 lon=126.10307630000001 comp_nodes=2
- `tiny` name='저도-광대도' node=2813165864 lat=34.506904500000005 lon=126.16605750000001 comp_nodes=6
- `tiny` name='상낙월도-하낙월도' node=5418097506 lat=35.192225400000005 lon=126.1326413 comp_nodes=4
- `tiny` name='장도사랑호(상진포구 - 장도)' node=4730317350 lat=34.7949672 lon=127.4702149 comp_nodes=46
- `tiny` name='득량호(녹동항 - 득량도)' node=4734268014 lat=34.594996800000004 lon=127.0991951 comp_nodes=23
- … +23 more

#### europe_denmark (gen `20260930T084525Z-2389780-europe_denmark-02429647`)

- `no_road` name='Sylt - Rømø (List - Havneby)' node=391498589 lat=55.015529900000004 lon=8.4398008 comp_nodes=0
- `no_road` name='Kiel (D) – Oslo (N)' node=289394625 lat=59.90982820000001 lon=10.7087912 comp_nodes=0
- `no_road` name='Kiel (D) – Oslo (N)' node=283251508 lat=54.316804100000006 lon=10.1391693 comp_nodes=0
- `tiny` name='Svendborg - Hjortø' node=21961621 lat=54.9666329 lon=10.4918268 comp_nodes=16
- `no_road` name='Kiel (D) – Göteborg (S)' node=288583407 lat=57.6958324 lon=11.914393200000001 comp_nodes=0
- `no_road` name='Kiel (D) – Göteborg (S)' node=916106851 lat=54.319211100000004 lon=10.139547 comp_nodes=0
- `no_road` name='Rødby (DK) - Puttgarden (D)' node=1587892816 lat=54.502548700000006 lon=11.228016100000001 comp_nodes=0
- `no_road` name='Rødby (DK) - Puttgarden (D)' node=10702747587 lat=54.5028164 lon=11.228220700000001 comp_nodes=0
- `no_road` name='Kragenæs - Askø' node=1518198189 lat=54.9160436 lon=11.3600673 comp_nodes=0
- `no_road` name='Kragenæs - Askø' node=1518198193 lat=54.91606 lon=11.362267300000001 comp_nodes=0
- `no_road` name='Kragenæs - Askø' node=1083045122 lat=54.916718200000005 lon=11.3724378 comp_nodes=0
- `no_road` name='Kragenæs - Askø' node=1083045118 lat=54.908232100000006 lon=11.3933804 comp_nodes=0
- `no_road` name='Travemünde (D) – Liepāja (LV)' node=1360276252 lat=53.9404905 lon=10.862189500000001 comp_nodes=0
- `no_road` name='Travemünde (D) – Liepāja (LV)' node=1416066420 lat=56.527623000000006 lon=20.9960302 comp_nodes=0
- `no_road` name='Klaipėda (LT) – Kiel (D)' node=412686570 lat=55.684453600000005 lon=21.1387897 comp_nodes=0
- `no_road` name='Klaipėda (LT) – Kiel (D)' node=921265497 lat=54.33667620000001 lon=10.1695217 comp_nodes=0
- `no_road` name='Travemünde (D) - Klaipėda (LT)' node=7177550996 lat=55.6579248 lon=21.1572932 comp_nodes=0
- `no_road` name='Rostock - Trelleborg' node=563845312 lat=54.1445881 lon=12.0976681 comp_nodes=0
- `no_road` name='Rostock - Trelleborg' node=2406777893 lat=55.3676126 lon=13.1567232 comp_nodes=0
- `no_road` name='Travemünde (D) – Helsinki (FIN)' node=1416013478 lat=60.2200367 lon=25.196558200000002 comp_nodes=0
- `no_road` name='Travemünde (D) – Helsinki (FIN)' node=1360276249 lat=53.9414183 lon=10.8604819 comp_nodes=0
- `no_road` name='Travemünde (D) – Trelleborg (SE)' node=3513574374 lat=55.367314400000005 lon=13.157710900000001 comp_nodes=0
- `no_road` name='Travemünde (D) – Trelleborg (SE)' node=1415854311 lat=53.9421217 lon=10.859804700000002 comp_nodes=0
- `no_road` name='Travemünde (D) – Malmö (SE)' node=11171009141 lat=55.627301300000006 lon=12.990010600000002 comp_nodes=0
- `tiny` name='Køge - Rønne' node=573233232 lat=55.455995 lon=12.194939600000001 comp_nodes=38
- … +21 more

#### asia_japan_hokkaido (gen `20260929T141242Z-180067-asia_japan_hokkaido-dd238b7f`)

- `no_road` name='江差 ー 奥尻島' node=3376474631 lat=42.1751445 lon=139.5170514 comp_nodes=0
- `no_road` name='青函フェリー' node=8347084450 lat=40.844382 lon=140.7171763 comp_nodes=0
- `no_road` name='津軽海峡ロード' node=6280598974 lat=40.8456174 lon=140.71601030000002 comp_nodes=0
- `no_road` name='ノスタルジック航路' node=1760921419 lat=41.5261366 lon=140.89803460000002 comp_nodes=0
- `no_road` name='新日本海フェリー（敦賀―苫小牧東）' node=9051973201 lat=35.678374000000005 lon=136.0733414 comp_nodes=0
- `no_road` name='太平洋フェリー（仙台―苫小牧）' node=4240945489 lat=38.273569800000004 lon=141.0055201 comp_nodes=0
- `no_road` name='新日本海フェリー（新潟―小樽）' node=5113055832 lat=37.9380752 lon=139.068916 comp_nodes=0
- `no_road` name='新日本海フェリー（新潟―小樽）' node=2519443251 lat=43.1907024 lon=141.0170209 comp_nodes=0
- `no_road` name=None node=2519432095 lat=35.481006400000005 lon=135.3924806 comp_nodes=0
- `no_road` name='大洗〜苫小牧' node=5316274548 lat=36.3087411 lon=140.57514310000002 comp_nodes=0
- `no_road` name='羽幌沿海フェリー' node=6652368705 lat=44.442437500000004 lon=141.4284375 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=1371004447 lat=45.297269400000005 lon=141.0543422 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=6313096517 lat=45.2963558 lon=141.05245580000002 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=1371004485 lat=45.2961645 lon=141.0493788 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=1371004413 lat=45.2966507 lon=141.04812220000002 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=8440418602 lat=45.2966897 lon=141.04806770000002 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=1371004422 lat=45.297352100000005 lon=141.0471411 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=1371004479 lat=45.298031 lon=141.0464977 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=3733195965 lat=45.298038000000005 lon=141.0463353 comp_nodes=0
- `no_road` name='利尻島（鴛泊） ー 礼文島 （香深）' node=6313096513 lat=45.2979081 lon=141.04574770000002 comp_nodes=0
- `no_road` name='稚内 ー 利尻島 （鴛泊）' node=6313096514 lat=45.42994 lon=141.7015818 comp_nodes=0
- `no_road` name='稚内 ー 利尻島 （鴛泊）' node=6313096515 lat=45.4408672 lon=141.6917937 comp_nodes=0
- `no_road` name='稚内 ー 利尻島 （鴛泊）' node=1371004417 lat=45.450661200000006 lon=141.6814962 comp_nodes=0
- `no_road` name='稚内 ー 利尻島 （鴛泊）' node=1371004396 lat=45.460111500000004 lon=141.67122450000002 comp_nodes=0
- `no_road` name='稚内 ー 利尻島 （鴛泊）' node=6313096512 lat=45.4628749 lon=141.6650544 comp_nodes=0
- … +5 more

#### europe_finland (gen `20260930T074110Z-2389780-europe_finland-971e62f7`)

- `no_road` name='Stockholm/Kapellskär – Mariehamn' node=4495691551 lat=59.7830469 lon=19.4409922 comp_nodes=0
- `tiny` name='Brändö-Kumlingelinjen' node=1296525563 lat=60.3041563 lon=21.034402800000002 comp_nodes=33
- `no_road` name='Brändö-Kumlingelinjen' node=7486276608 lat=60.290367700000004 lon=20.8693923 comp_nodes=0
- `no_road` name='Brändö-Kumlingelinjen' node=7486276624 lat=60.2904616 lon=20.790162000000002 comp_nodes=0
- `no_road` name=None node=1675218194 lat=60.0914262 lon=19.9281648 comp_nodes=0
- `tiny` name='Södra Linjen' node=419001670 lat=60.0646695 lon=20.8073123 comp_nodes=20
- `tiny` name='Södra Linjen' node=1937327514 lat=60.031232100000004 lon=20.825000600000003 comp_nodes=38
- `no_road` name='Brändö-Kumlingelinjen' node=7486276645 lat=60.3041192 lon=21.0342811 comp_nodes=0
- `no_road` name='Brändö-Kumlingelinjen' node=1536003409 lat=60.303862800000005 lon=21.033440300000002 comp_nodes=0
- `no_road` name='Brändö-Kumlingelinjen' node=7486205684 lat=60.303798 lon=21.032097500000003 comp_nodes=0
- `no_road` name='Brändö-Kumlingelinjen' node=1536003457 lat=60.30393050000001 lon=21.030962300000002 comp_nodes=0
- `no_road` name='Brändö-Kumlingelinjen' node=1536003438 lat=60.304195500000006 lon=21.030273 comp_nodes=0
- `no_road` name='Högsar' node=4970997688 lat=60.16967090000001 lon=21.8789266 comp_nodes=0
- `no_road` name='Högsar' node=6661360506 lat=60.1670954 lon=21.8802543 comp_nodes=0
- `no_road` name=None node=5658981105 lat=60.4260475 lon=22.1871565 comp_nodes=0
- `no_road` name=None node=6443737157 lat=60.42739100000001 lon=22.1914829 comp_nodes=0
- `no_road` name='Travemünde (D) – Helsinki (FIN)' node=1360276249 lat=53.9414183 lon=10.8604819 comp_nodes=0
- `no_road` name='Helsinki (FIN) – Tallinn (EST)' node=319666667 lat=59.445391300000004 lon=24.7625615 comp_nodes=0
- `no_road` name='Helsinki (FIN) – Tallinn (EST)' node=59631552 lat=60.1639885 lon=24.9658523 comp_nodes=0
- `no_road` name='Helsinki (FIN) – Tallinn (EST)' node=421693998 lat=59.443955 lon=24.770046100000002 comp_nodes=0
- `tiny` name='Tallinn (Muuga) - Helsinki (Vuosaari)' node=6647141384 lat=60.20889210000001 lon=25.1928088 comp_nodes=3
- `no_road` name='Tallinn (Muuga) - Helsinki (Vuosaari)' node=12412888475 lat=59.49013600000001 lon=24.965645300000002 comp_nodes=0
- `no_road` name='Tallinn (EST) - Helsinki (FIN)' node=6647118962 lat=59.4448627 lon=24.7628445 comp_nodes=0
- `no_road` name='Tallinn (Muuga) - Helsinki (Vuosaari)' node=6647120547 lat=59.489553900000004 lon=24.9695931 comp_nodes=0
- `no_road` name='Tykistölahti-Kuninkaanportti' node=1815986108 lat=60.14487570000001 lon=24.9877307 comp_nodes=0
- … +4 more

#### europe_turkey (gen `20260929T234517Z-1335901-europe_turkey-aa6f2fca`)

- `no_road` name='Σάμος - Κως' node=2901676695 lat=37.7526005 lon=26.9598078 comp_nodes=0
- `no_road` name='Σάμος - Κως' node=667467466 lat=36.8985594 lon=27.290450200000002 comp_nodes=0
- `no_road` name='Piraeus - Limassol' node=10979285245 lat=37.937329600000005 lon=23.637055200000002 comp_nodes=0
- `no_road` name='Piraeus - Limassol' node=10979285244 lat=34.6555364 lon=33.019948 comp_nodes=0
- `no_road` name='Rhodes - Symi' node=4219261322 lat=36.6208766 lon=27.8465826 comp_nodes=0
- `no_road` name='Rhodes - Symi' node=9525051217 lat=36.443012 lon=28.2367989 comp_nodes=0
- `no_road` name='Ρόδος - Καστελόριζο' node=667473331 lat=36.4450032 lon=28.232011900000003 comp_nodes=0
- `no_road` name='Ρόδος - Καστελόριζο' node=669646784 lat=36.15052 lon=29.592326800000002 comp_nodes=0
- `no_road` name='Μarmaris - Ρόδος' node=872722200 lat=36.447148000000006 lon=28.2326921 comp_nodes=0
- `no_road` name='Taşucu - Girne Feribotu' node=2012091240 lat=35.3431512 lon=33.3321387 comp_nodes=0
- `no_road` name='Χίος - Ικαρία (Άγιος Κήρυκος)' node=586834408 lat=38.372215000000004 lon=26.137742000000003 comp_nodes=0
- `no_road` name='Χίος - Ικαρία (Άγιος Κήρυκος)' node=1800695508 lat=37.613146 lon=26.295557300000002 comp_nodes=0
- `no_road` name='Χίος - Τσεσμέ / Sakız - Çeşme' node=2867334834 lat=38.372224 lon=26.1410096 comp_nodes=0
- `no_road` name='Μυτιλήνη - Χίος' node=7534926349 lat=39.1043341 lon=26.563055900000002 comp_nodes=0
- `no_road` name='Μυτιλήνη - Ayvalık' node=2674342191 lat=39.103776800000006 lon=26.5601522 comp_nodes=0
- `no_road` name='Λήμνος - Μυτιλήνη' node=7534926350 lat=39.1043613 lon=26.5617437 comp_nodes=0
- `no_road` name='Λήμνος - Μυτιλήνη' node=7843662884 lat=39.8710597 lon=25.0548551 comp_nodes=0
- `no_road` name='Kabatepe - Gökçeada' node=4107967308 lat=40.2287837 lon=25.947787700000003 comp_nodes=0
- `no_road` name='Gelibolu - Lapseki' node=13971161373 lat=40.406014500000005 lon=26.6583921 comp_nodes=0
- `no_road` name='Marmara Adası - Ekincik Adası - Avşa Adası' node=2235251355 lat=40.584999200000006 lon=27.5553096 comp_nodes=0
- `no_road` name='Тробзон — Сочи' node=522000951 lat=43.5795856 lon=39.711165400000006 comp_nodes=0
- `no_road` name='Yenikapı-Yalova Hızlı Ferbot Güzergahı' node=6233164456 lat=41.0019835 lon=28.954173800000003 comp_nodes=0
- `tiny` name='Pendik-Yalova Hızlı Feribot Güzergahı' node=4239216992 lat=40.8746122 lon=29.2363615 comp_nodes=2
- `no_road` name='Yenikapı - Armutlu - Bursa' node=13361681575 lat=41.0019861 lon=28.955698100000003 comp_nodes=0
- `no_road` name='Eskihisar - Topçular' node=3762317714 lat=40.694765100000005 lon=29.4338346 comp_nodes=0
- … +3 more

#### europe_germany_niedersachsen (gen `20261001T135043Z-625025-europe_germany_niedersachsen-7aa7bf38`)

- `tiny` name='Weserfähre Hemeln-Reinhardshagen' node=510727914 lat=51.4980466 lon=9.604783900000001 comp_nodes=13
- `tiny` name='Weserfähre Wahmbeck' node=311099473 lat=51.6257506 lon=9.5228353 comp_nodes=5
- `no_road` name='Anleger Borkumfähre' node=4763873121 lat=53.343519400000005 lon=7.1864841 comp_nodes=0
- `no_road` name=None node=10721116698 lat=53.6258345 lon=7.1593881 comp_nodes=0
- `no_road` name=None node=3523700185 lat=53.6277921 lon=7.1581216 comp_nodes=0
- `no_road` name=None node=11982586611 lat=53.699214600000005 lon=7.162457900000001 comp_nodes=0
- `no_road` name=None node=2170910095 lat=53.697285 lon=7.1645352 comp_nodes=0
- `no_road` name=None node=11843219470 lat=53.626339400000006 lon=7.1575177000000005 comp_nodes=0
- `no_road` name=None node=249504170 lat=53.6456092 lon=7.147452400000001 comp_nodes=0
- `tiny` name=None node=11972396410 lat=53.6970036 lon=7.157231100000001 comp_nodes=4
- `no_road` name='Emden-Borkum' node=4060371938 lat=53.5637812 lon=6.7561868 comp_nodes=0
- `no_road` name='Fähre Ditzum–Petkum' node=254000264 lat=53.3295315 lon=7.270005500000001 comp_nodes=0
- `no_road` name='Fähre Ditzum–Petkum' node=254000265 lat=53.324712700000006 lon=7.2684473 comp_nodes=0
- `no_road` name='Fähre Ditzum–Petkum' node=4881660832 lat=53.3234087 lon=7.269631700000001 comp_nodes=0
- `no_road` name='Fähre Ditzum–Petkum' node=1824614742 lat=53.3201138 lon=7.2781210000000005 comp_nodes=0
- `no_road` name=None node=4880992598 lat=53.700136500000006 lon=7.1351909000000004 comp_nodes=0
- `no_road` name='Eemshaven - Borkum' node=471536224 lat=53.456019000000005 lon=6.832454 comp_nodes=0
- `no_road` name=None node=11982586617 lat=53.698932600000006 lon=7.162954900000001 comp_nodes=0
- `no_road` name=None node=3523701911 lat=53.653363600000006 lon=7.147361200000001 comp_nodes=0
- `no_road` name='Fähre Blumenthal–Motzen' node=12631312637 lat=53.1810362 lon=8.5579177 comp_nodes=0
- `no_road` name='Fähre Vegesack–Lemwerder' node=314401045 lat=53.167750700000006 lon=8.618768900000001 comp_nodes=0
- `no_road` name='Columbushopper' node=11137088534 lat=53.571211600000005 lon=8.552880100000001 comp_nodes=0
- `no_road` name='Columbushopper' node=11137074018 lat=53.571255300000004 lon=8.553549400000001 comp_nodes=0
- `no_road` name='Fähre Farge–Berne' node=3080344118 lat=53.1970134 lon=8.516601600000001 comp_nodes=0
- `no_road` name='Elbfähre Glückstadt-Wischhafen' node=1371981000 lat=53.794767900000004 lon=9.4030135 comp_nodes=0

#### asia_indonesia_sulawesi (gen `20260929T185017Z-1335901-asia_indonesia_sulawesi-a4989370`)

- `no_road` name='Batulicin - Makassar' node=12182574311 lat=-3.4322332 lon=116.00890430000001 comp_nodes=0
- `no_road` name='Surabaya-Makassar' node=1509464158 lat=-7.198609500000001 lon=112.735117 comp_nodes=0
- `no_road` name='Makassar-Maumere' node=1493574199 lat=-8.614607300000001 lon=122.2198072 comp_nodes=0
- `no_road` name='Bira - Pattumbukang' node=7949445848 lat=-6.3922037000000005 lon=120.4961973 comp_nodes=0
- `no_road` name='Bira - Jampea' node=3774843678 lat=-7.059530700000001 lon=120.61087450000001 comp_nodes=0
- `no_road` name='Baubau - Namrole' node=9492954391 lat=-3.8522325000000004 lon=126.73139250000001 comp_nodes=0
- `no_road` name='Baubau - Ambon' node=11889186423 lat=-3.6946347000000004 lon=128.17521190000002 comp_nodes=0
- `no_road` name='Namlea - Baubau' node=9515141813 lat=-3.2693773000000004 lon=127.08329900000001 comp_nodes=0
- `no_road` name='Awerange - Bontang' node=4330301638 lat=0.16455350000000002 lon=117.48758330000001 comp_nodes=0
- `no_road` name='Balikpapan - Pare-Pare' node=5828392884 lat=-1.2734299 lon=116.80543460000001 comp_nodes=0
- `no_road` name='Kariangau - Mamuju' node=3255306668 lat=-1.2008863 lon=116.8175169 comp_nodes=0
- `no_road` name='Banggai - Bobong' node=10585949779 lat=-1.8993092 lon=124.34055930000001 comp_nodes=0
- `no_road` name='Nunukan - Pantoloan' node=4684478444 lat=4.1467393 lon=117.6663668 comp_nodes=0
- `tiny` name='Pasokan - Dolong' node=9585949257 lat=-0.26190240000000004 lon=122.21513460000001 comp_nodes=43
- `no_road` name='Balikpapan - Tarakan' node=14078277907 lat=3.2811586000000004 lon=117.5933411 comp_nodes=0
- `no_road` name='Tolitoli - Tarakan' node=6382424925 lat=3.4314231000000004 lon=117.5358372 comp_nodes=0
- `no_road` name='Amurang - Melonguane' node=9509470934 lat=3.9927145000000004 lon=126.68033190000001 comp_nodes=0
- `no_road` name='Manado - Jailolo' node=9598470757 lat=1.0573369000000001 lon=127.46982390000001 comp_nodes=0
- `no_road` name='Manado - Jailolo' node=5606173047 lat=1.4961161 lon=124.8386425 comp_nodes=0
- `no_road` name='Bitung–Bastiong (Ternate)' node=3926231879 lat=0.7638021 lon=127.37629100000001 comp_nodes=0
- `no_road` name='Bitung–Tobelo' node=10570270876 lat=1.767829 lon=127.9834134 comp_nodes=0
- `no_road` name='Bitung - Ternate' node=6766602991 lat=0.7800995000000001 lon=127.38834750000001 comp_nodes=0
- `tiny` name='Makalehi - Tagulandang' node=10572080686 lat=2.7311219 lon=125.16298270000001 comp_nodes=24
- `no_road` name='Ulu Siau - Buhias' node=10572227586 lat=2.6757053 lon=125.45225880000001 comp_nodes=0

#### europe_france_nord_pas_de_calais (gen `20261001T135956Z-625025-europe_france_nord_pas_de_calais-84afdbc7`)

- `no_road` name='Boulogne-sur-Mer (F) - Cork (IRL)' node=13784977181 lat=50.7205778 lon=1.5739946 comp_nodes=0
- `no_road` name='Boulogne-sur-Mer (F) - Cork (IRL)' node=10760891981 lat=51.831425 lon=-8.323083500000001 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=5750113383 lat=52.253827300000005 lon=-6.3365657 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=2982398975 lat=51.049758600000004 lon=2.1355872000000002 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=2982398974 lat=51.0467194 lon=2.1475267000000002 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=252942684 lat=51.042351800000006 lon=2.1587008 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=252942685 lat=51.039446000000005 lon=2.1670358000000003 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=13111266266 lat=51.0380437 lon=2.168889 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=13111266267 lat=51.036614300000004 lon=2.1698949 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=506228710 lat=51.035119800000004 lon=2.1706366 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=252942688 lat=51.022649200000004 lon=2.1716401000000003 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=252942689 lat=51.020543100000005 lon=2.1725397 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=252942690 lat=51.018350700000006 lon=2.1747346000000003 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=13111266269 lat=51.018078900000006 lon=2.1751089 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=13111266268 lat=51.0178535 lon=2.1756926 comp_nodes=0
- `no_road` name='Rosslare (IRL) - Dunkerque (F)' node=506228709 lat=51.017689700000005 lon=2.1765643000000003 comp_nodes=0
- `no_road` name='Dover (UK) - Calais (F)' node=1143021655 lat=51.125877700000004 lon=1.3357696000000001 comp_nodes=0
- `no_road` name='Dover (UK) - Calais (F)' node=2301299535 lat=50.973673100000006 lon=1.8241572000000001 comp_nodes=0
- `no_road` name='Dover (UK) - Calais (F)' node=136942322 lat=50.9686481 lon=1.8492160000000002 comp_nodes=0
- `no_road` name='Dover (UK) - Calais (F)' node=1607070508 lat=50.968224500000005 lon=1.8507545 comp_nodes=0
- `no_road` name='Douvres - Calais' node=9484416483 lat=50.977372100000004 lon=1.8560886 comp_nodes=0
- `no_road` name='Douvres - Calais' node=9484416480 lat=50.9792635 lon=1.8635130000000002 comp_nodes=0
- `no_road` name='Dover (UK) - Calais (F)' node=1143064924 lat=51.123741200000005 lon=1.3323926000000001 comp_nodes=0
- `no_road` name='Dover (UK) - Dunkerque (F)' node=289661007 lat=51.1268047 lon=1.3380595000000002 comp_nodes=0

#### europe_norway_nord_norge (gen `20260930T074732Z-2389780-europe_norway_nord_norge-e4ba6711`)

- `no_road` name='Hurtigruten' node=279163665 lat=64.1059268 lon=10.0038826 comp_nodes=0
- `no_road` name='Hurtigruten' node=5499292413 lat=65.475727 lon=12.210708 comp_nodes=0
- `no_road` name='Hurtigruten' node=279163550 lat=66.02452290000001 lon=12.6397556 comp_nodes=0
- `tiny` name='Dagsvik - Sørnes' node=3094941717 lat=65.9716234 lon=12.852662400000002 comp_nodes=29
- `tiny` name='Vikdal - Hundåla' node=1779020497 lat=65.8879323 lon=13.096060900000001 comp_nodes=40
- `tiny` name='Træna - Selvær' node=4935373675 lat=66.58404850000001 lon=12.220797800000001 comp_nodes=38
- `tiny` name='Selvær - Indre Kvarøy' node=5563943242 lat=66.48744690000001 lon=12.9665294 comp_nodes=40
- `no_road` name=None node=9512005861 lat=68.477017 lon=15.159625400000001 comp_nodes=0
- `no_road` name=None node=289330698 lat=68.4785464 lon=15.198788400000002 comp_nodes=0
- `tiny` name=None node=1894347698 lat=68.5193904 lon=15.254271300000001 comp_nodes=39
- `no_road` name=None node=9512005769 lat=68.5199417 lon=15.253843400000001 comp_nodes=0
- `tiny` name=None node=5041152326 lat=68.4774886 lon=15.156723900000001 comp_nodes=30
- `no_road` name=None node=9512005783 lat=68.51712520000001 lon=15.2338774 comp_nodes=0
- `no_road` name=None node=289330745 lat=68.52148700000001 lon=15.246233900000002 comp_nodes=0
- `no_road` name=None node=289330739 lat=68.504236 lon=15.212374 comp_nodes=0
- `no_road` name=None node=9512005950 lat=68.5020173 lon=15.2100348 comp_nodes=0
- `tiny` name='AltafjordXpressen' node=6093323305 lat=70.3183887 lon=22.871723000000003 comp_nodes=22
- `no_road` name=None node=4977065066 lat=70.03274710000001 lon=18.565396500000002 comp_nodes=0
- `no_road` name=None node=4977058136 lat=69.87580340000001 lon=18.577399 comp_nodes=0
- `no_road` name=None node=4975262411 lat=69.9730579 lon=18.528487900000002 comp_nodes=0
- `tiny` name='Karlsøy - Skåningsbukt' node=1935276152 lat=70.0017182 lon=19.8877555 comp_nodes=38
- `tiny` name='Rotsund - Uløybukt' node=4386639273 lat=69.855654 lon=20.6956598 comp_nodes=30
- `tiny` name='Nikkeby - Storstein' node=4392580567 lat=70.0740174 lon=20.815366 comp_nodes=23
- `tiny` name='Sør-Tverrfjord - Bergsfjord - Tverrfjord - Øksfjord' node=835305502 lat=70.2518501 lon=21.7844926 comp_nodes=30

#### north_america_us_washington (gen `20260929T140726Z-180067-north_america_us_washington-9648d39c`)

- `tiny` name='Steilacoom-Ketron-Anderson Island Ferry' node=1907731575 lat=47.1622293 lon=-122.62920380000001 comp_nodes=34
- `tiny` name='Herron Island Ferry' node=2328979484 lat=47.266985500000004 lon=-122.82747060000001 comp_nodes=36
- `no_road` name='Port Angeles ↔ Victoria' node=639371829 lat=48.421618900000006 lon=-123.372083 comp_nodes=0
- `no_road` name='Seattle-Bremerton Ferry' node=2128433309 lat=47.5602078 lon=-122.62278300000001 comp_nodes=0
- `no_road` name='Seattle–Bainbridge Ferry' node=48532375 lat=47.622113600000006 lon=-122.50704110000001 comp_nodes=0
- `tiny` name='Edmonds - Kingston Ferry' node=945601797 lat=47.7949768 lon=-122.49453720000001 comp_nodes=3
- `tiny` name='Brown Island - San Juan Island Ferry' node=4960623393 lat=48.5356437 lon=-123.0016844 comp_nodes=6
- `no_road` name='Anacortes ↔ Lopez Island' node=243583019 lat=48.5085836 lon=-122.6765567 comp_nodes=0
- `tiny` name='Anacortes ↔ Friday Harbor' node=2937923212 lat=48.5073747 lon=-122.6774999 comp_nodes=2
- `no_road` name='Anacortes ↔ Shaw Island' node=677173535 lat=48.5071804 lon=-122.67702320000001 comp_nodes=0
- `no_road` name='Friday Harbor ↔ Shaw Island' node=12325161351 lat=48.5885839 lon=-123.01244500000001 comp_nodes=0
- `no_road` name='Alaska Marine Highway - Bellingham ↔ Ketchikan' node=1609416212 lat=55.353683800000006 lon=-131.69460700000002 comp_nodes=0
- `no_road` name='Duke Point ↔ Tsawwassen' node=5371656508 lat=49.003110500000005 lon=-123.1372869 comp_nodes=0
- `no_road` name='Duke Point ↔ Tsawwassen' node=470970816 lat=49.1625164 lon=-123.890973 comp_nodes=0
- `no_road` name='Berth 5 Approach' node=660826306 lat=49.0069336 lon=-123.13321400000001 comp_nodes=0
- `tiny` name='Berth 4 Approach' node=660826308 lat=49.006514800000005 lon=-123.132417 comp_nodes=25
- `tiny` name='Berth 3 Approach' node=660826317 lat=49.0060626 lon=-123.131684 comp_nodes=25
- `no_road` name='Mayne Island (Village Bay) ↔ Tsawwassen' node=1682463778 lat=48.8447049 lon=-123.32482710000001 comp_nodes=0
- `no_road` name='Mayne Island (Village Bay) ↔ Tsawwassen' node=5371661805 lat=49.0041243 lon=-123.1296614 comp_nodes=0
- `tiny` name='Berth 2 Approach' node=660826272 lat=49.006203600000006 lon=-123.129412 comp_nodes=25
- `tiny` name='Berth 1 Approach' node=660826385 lat=49.0062901 lon=-123.12895 comp_nodes=25
- `no_road` name='Salt Spring Island (Long Harbour) ↔ Tsawwassen' node=1682319513 lat=48.852109000000006 lon=-123.44578220000001 comp_nodes=0
- `no_road` name='Galiano Island (Sturdies Bay) ↔ Tsawwassen' node=1327617345 lat=48.876566800000006 lon=-123.31486910000001 comp_nodes=0
- `no_road` name='Swartz Bay ↔ Tsawwassen' node=323308217 lat=48.6945513 lon=-123.41021130000001 comp_nodes=0

#### asia_india_southern_zone (gen `20260929T232401Z-1335901-asia_india_southern_zone-0b794fd4`)

- `no_road` name='Alappuzha to Kollam' node=191766533 lat=8.891958 lon=76.58519910000001 comp_nodes=0
- `tiny` name='NTF1' node=6251739267 lat=9.500764400000001 lon=76.35325230000001 comp_nodes=4
- `no_road` name='Alappuzha to Kollam' node=648260188 lat=9.387443600000001 lon=76.38730740000001 comp_nodes=0
- `no_road` name='Alappuzha to Kollam' node=648266418 lat=9.3188461 lon=76.3898274 comp_nodes=0
- `no_road` name='Alappuzha to Kollam' node=5849725332 lat=9.4898156 lon=76.3657951 comp_nodes=0
- `no_road` name='Alappuzha to Kollam' node=5863435938 lat=9.4891773 lon=76.3656425 comp_nodes=0
- `no_road` name='NW3' node=4028882762 lat=9.531877900000001 lon=76.3753532 comp_nodes=0
- `no_road` name='NW3' node=4028882731 lat=9.5086977 lon=76.3540062 comp_nodes=0
- `no_road` name='vattakkayal ferry' node=4028895369 lat=9.514999000000001 lon=76.39371700000001 comp_nodes=0
- `tiny` name='vattakkayal ferry' node=3929976929 lat=9.5061411 lon=76.3696826 comp_nodes=2
- `no_road` name='Muhamma - Kumarakom' node=3927476349 lat=9.6050573 lon=76.36587440000001 comp_nodes=0
- `no_road` name='pathiramanal ferry' node=7158099892 lat=9.5509879 lon=76.434825 comp_nodes=0
- `no_road` name='pathiramanal ferry' node=1379605602 lat=9.619701500000001 lon=76.3830499 comp_nodes=0
- `no_road` name='Alappuzha to Kollam' node=9507613338 lat=9.398768 lon=76.38635450000001 comp_nodes=0
- `no_road` name='kottayam -Alappuzha ferry' node=7158022161 lat=9.543626100000001 lon=76.40642890000001 comp_nodes=0
- `tiny` name='NTF' node=6251739252 lat=9.500623000000001 lon=76.35335450000001 comp_nodes=4
- `no_road` name='NW3' node=3509595153 lat=9.500129300000001 lon=76.35653040000001 comp_nodes=0
- `no_road` name='Wellingdon - Ernakulam Ferry' node=982135833 lat=9.9858285 lon=76.26820000000001 comp_nodes=0
- `no_road` name='Wellingdon - Ernakulam Ferry' node=982135715 lat=9.970141 lon=76.26218610000001 comp_nodes=0
- `no_road` name=None node=7252196567 lat=9.5856314 lon=76.51872970000001 comp_nodes=0
- `no_road` name=None node=7252196560 lat=9.5760331 lon=76.51524760000001 comp_nodes=0
- `tiny` name='Rajahisland ferry route' node=6690030631 lat=10.533316600000001 lon=76.04973460000001 comp_nodes=14
- `tiny` name='r' node=6094957623 lat=10.534485 lon=76.0494702 comp_nodes=14

#### asia_japan_shikoku (gen `20260929T141531Z-180067-asia_japan_shikoku-21d89bec`)

- `no_road` name='オレンジライン(柳井-伊保田-三津浜)' node=4375142897 lat=33.9442177 lon=132.4391337 comp_nodes=0
- `tiny` name='中島汽船西線' node=4874314224 lat=33.8943108 lon=132.6400064 comp_nodes=24
- `tiny` name='中島汽船西線' node=9132361864 lat=33.9337457 lon=132.5369485 comp_nodes=16
- `tiny` name='中島汽船西線' node=9132388543 lat=33.9830671 lon=132.5164254 comp_nodes=42
- `tiny` name='中島汽船東線' node=5498145937 lat=33.9753489 lon=132.68800570000002 comp_nodes=12
- `no_road` name='中島汽船東線(松山観光港寄港便)' node=9248038144 lat=33.8891617 lon=132.70407840000001 comp_nodes=0
- `no_road` name='大三島フェリー(忠海-大久野島-盛)' node=5489867927 lat=34.3085301 lon=132.9991615 comp_nodes=0
- `no_road` name='石崎汽船・瀬戸内海汽船クルーズフェリー(広島－呉－松山)' node=8524105832 lat=34.239899900000005 lon=132.55598930000002 comp_nodes=0
- `no_road` name='防予フェリー(柳井-三津浜)' node=4374970791 lat=33.9566632 lon=132.13270550000001 comp_nodes=0
- `tiny` name='今治市営せきぜん渡船 (今治－宗方－大下－小大下－岡村)' node=538722808 lat=34.1903785 lon=132.8995807 comp_nodes=32
- `no_road` name='大三島ブルーライン (今治－宗方－木江）' node=540521872 lat=34.233214000000004 lon=132.9174343 comp_nodes=0
- `tiny` name='大三島ブルーライン (今治－宗方－木江)・今治市営せきぜん渡船 (今治－宗方－大下－小大下－岡村)' node=7914178380 lat=34.0707602 lon=133.0058804 comp_nodes=7
- `no_road` name='新居浜市営渡海船（大島 ～ 黒島）' node=5456656869 lat=33.993706800000005 lon=133.36508980000002 comp_nodes=0
- `no_road` name='三光汽船 (州江－小漕)' node=540530629 lat=34.285025700000006 lon=133.14399880000002 comp_nodes=0
- `tiny` name='長江フェリー (長江－土生)・岩城汽船(岩城－長江－土生)' node=7914178578 lat=34.2834658 lon=133.1780053 comp_nodes=22
- `no_road` name='家老渡フェリー汽船 (家老渡－上弓削)' node=3405204183 lat=34.2792735 lon=133.19880030000002 comp_nodes=0
- `no_road` name='オーシャン東九フェリー（東京―徳島―北九州）' node=4006529416 lat=35.616579200000004 lon=139.7967056 comp_nodes=0
- `no_road` name='小豆島急行フェリー' node=357427174 lat=34.7839734 lon=134.66277390000002 comp_nodes=0
- `no_road` name='南海フェリー' node=2519428653 lat=34.217601 lon=135.1441039 comp_nodes=0
- `tiny` name='高松発↔鬼ヶ島着↔男木島着 フェリー' node=1444064271 lat=34.389105 lon=134.052276 comp_nodes=28
- `tiny` name='高松発↔鬼ヶ島着↔男木島着 フェリー' node=1527601931 lat=34.4218701 lon=134.0539122 comp_nodes=23
- `no_road` name='高松発↔鬼ヶ島着↔男木島着 フェリー' node=1527601816 lat=34.3884044 lon=134.0532631 comp_nodes=0
- `no_road` name='高松-豊島(家浦港)フェリー' node=3732996264 lat=34.4896407 lon=134.0601645 comp_nodes=0

#### europe_albania (gen `20260930T074804Z-2389780-europe_albania-9f143342`)

- `no_road` name='Brindisi - Vlorë' node=6631787855 lat=40.647018800000005 lon=17.9615605 comp_nodes=0
- `no_road` name='Brindisi - Vlorë' node=12754155803 lat=40.649373100000005 lon=17.9607865 comp_nodes=0
- `no_road` name='Ανκόνα - Ηγουμενίτσα / Ancona - Igoumenitsa' node=534965622 lat=43.617490800000006 lon=13.5057586 comp_nodes=0
- `no_road` name='Ανκόνα - Ηγουμενίτσα / Ancona - Igoumenitsa' node=1506860386 lat=39.4905264 lon=20.2576538 comp_nodes=0
- `no_road` name='Μπρίντιζι - Πάτρα' node=1223363269 lat=40.645634 lon=17.9593394 comp_nodes=0
- `no_road` name='Μπρίντιζι - Πάτρα' node=8695549420 lat=38.226271100000005 lon=21.7189243 comp_nodes=0
- `no_road` name='Βενετία - Πάτρα' node=11504661733 lat=45.4250667 lon=12.255484500000001 comp_nodes=0
- `no_road` name='Μπάρι - Πάτρα' node=14056872640 lat=41.1397719 lon=16.8641042 comp_nodes=0
- `no_road` name='Κέρκυρα - Ερεικούσα - Οθωνοί - Μαθράκι' node=7904237332 lat=39.6285182 lon=19.9129735 comp_nodes=0
- `no_road` name='Κέρκυρα - Ερεικούσα - Οθωνοί - Μαθράκι' node=5432253194 lat=39.780322600000005 lon=19.5197988 comp_nodes=0
- `no_road` name='Ανκόνα - Κέρκυρα' node=828717455 lat=39.631708800000006 lon=19.901579 comp_nodes=0
- `no_road` name='Βενετία - Ηγουμενίτσα' node=1421824123 lat=39.4924163 lon=20.258386100000003 comp_nodes=0
- `no_road` name='Μπάρι - Ηγουμενίτσα' node=5109958764 lat=39.4879667 lon=20.256996 comp_nodes=0
- `no_road` name='Bari - Durrës' node=3143807000 lat=41.1333223 lon=16.8666547 comp_nodes=0
- `no_road` name='Ancona - Durrës' node=12618285380 lat=43.6211087 lon=13.5090781 comp_nodes=0
- `no_road` name='Trieste - Durrës' node=447719199 lat=45.633651900000004 lon=13.766256700000001 comp_nodes=0
- `no_road` name=None node=274294591 lat=42.168014 lon=19.8792455 comp_nodes=0
- `no_road` name=None node=2494320340 lat=42.196280800000004 lon=19.887458600000002 comp_nodes=0
- `no_road` name=None node=2494320396 lat=42.222947100000006 lon=19.8949541 comp_nodes=0
- `no_road` name=None node=2494320689 lat=42.2610055 lon=19.951913400000002 comp_nodes=0
- `no_road` name=None node=2494320592 lat=42.2578644 lon=19.910556800000002 comp_nodes=0
- `no_road` name=None node=2494320446 lat=42.2451224 lon=19.8901624 comp_nodes=0

#### europe_greece (gen `20260930T074329Z-2389780-europe_greece-df0f0f14`)

- `no_road` name='Ρέθυμνο - Ηράκλειο' node=9160117557 lat=35.3725074 lon=24.4851987 comp_nodes=0
- `no_road` name='Σφακιά - Γαύδος' node=1947370863 lat=34.848241800000004 lon=24.118779500000002 comp_nodes=0
- `no_road` name='Λουτρό - Αγία Ρούμελη' node=298742402 lat=35.1980567 lon=24.079919800000003 comp_nodes=0
- `no_road` name='Κάρπαθος - Ρόδος' node=667473331 lat=36.4450032 lon=28.232011900000003 comp_nodes=0
- `no_road` name='Πειραιάς - Κύθηρα' node=3526751581 lat=37.944062200000005 lon=23.643193800000002 comp_nodes=0
- `no_road` name='Ίος - Νάξος' node=667948006 lat=36.7157452 lon=25.262640700000002 comp_nodes=0
- `no_road` name='Πολλώνια - Ψάθη' node=2928336198 lat=36.7644436 lon=24.527065200000003 comp_nodes=0
- `no_road` name='Piraeus - Limassol' node=10979285245 lat=37.937329600000005 lon=23.637055200000002 comp_nodes=0
- `no_road` name='Piraeus - Limassol' node=10979285244 lat=34.6555364 lon=33.019948 comp_nodes=0
- `no_road` name='Rhodes - Symi' node=9525051217 lat=36.443012 lon=28.2367989 comp_nodes=0
- `no_road` name='Μπάρι - Ζάκυνθος' node=14056872640 lat=41.1397719 lon=16.8641042 comp_nodes=0
- `tiny` name=None node=8693529088 lat=37.058046700000006 lon=27.090724700000003 comp_nodes=11
- `no_road` name='Μπρίντιζι - Πάτρα' node=1223363269 lat=40.645634 lon=17.9593394 comp_nodes=0
- `no_road` name='Ανκόνα - Πάτρα' node=534965622 lat=43.617490800000006 lon=13.5057586 comp_nodes=0
- `no_road` name='Βενετία - Πάτρα' node=11504661733 lat=45.4250667 lon=12.255484500000001 comp_nodes=0
- `tiny` name=None node=6363358913 lat=38.3512712 lon=21.418655100000002 comp_nodes=25
- `no_road` name='Χίος - Τσεσμέ / Sakız - Çeşme' node=3532227914 lat=38.3227914 lon=26.2976308 comp_nodes=0
- `no_road` name='Κέρκυρα - Ερεικούσα - Οθωνοί - Μαθράκι' node=5432253194 lat=39.780322600000005 lon=19.5197988 comp_nodes=0
- `no_road` name='Μυτιλήνη - Ayvalık' node=4625122423 lat=39.3283651 lon=26.695944400000002 comp_nodes=0
- `no_road` name='Brindisi - Saranda' node=6631787855 lat=40.647018800000005 lon=17.9615605 comp_nodes=0
- `no_road` name='Brindisi - Saranda' node=828717479 lat=39.8711597 lon=20.003437700000003 comp_nodes=0
- `no_road` name='Δάφνη - Καυσοκαλυβίων' node=6612021304 lat=40.125457700000005 lon=24.3386484 comp_nodes=0

#### asia_philippines (gen `20260929T185214Z-1335901-asia_philippines-d6495f34`)

- `no_road` name='Zamboanga – Bongao' node=3823540280 lat=5.0345746 lon=119.7747123 comp_nodes=0
- `no_road` name='Genaral Santos - Balut Island' node=8755045825 lat=5.412288500000001 lon=125.42753320000001 comp_nodes=0
- `tiny` name='Manila - Davao City' node=4691858168 lat=14.602291000000001 lon=120.9577524 comp_nodes=5
- `no_road` name='Manila - Iligan' node=12361097165 lat=8.7508555 lon=124.0057809 comp_nodes=0
- `no_road` name='Manila - Iligan' node=4719640713 lat=14.5799874 lon=120.96853180000001 comp_nodes=0
- `no_road` name='Tagbilaran - Plaridel' node=959785483 lat=9.6497936 lon=123.84668250000001 comp_nodes=0
- `no_road` name='Manila - Iligan' node=12361097164 lat=8.997160800000001 lon=123.02797310000001 comp_nodes=0
- `tiny` name='Cebu - Tagbilaran' node=600288698 lat=10.2940813 lon=123.90871560000001 comp_nodes=4
- `no_road` name='Iloilo - Jordan' node=6369858560 lat=10.669707800000001 lon=122.58238910000001 comp_nodes=0
- `no_road` name='Iloilo - Jordan' node=625449286 lat=10.688786400000001 lon=122.57238000000001 comp_nodes=0
- `no_road` name='Iloilo - Jordan' node=3024100748 lat=10.692562 lon=122.58345360000001 comp_nodes=0
- `no_road` name='Iloilo - Jordan' node=1783202960 lat=10.6673918 lon=122.58852300000001 comp_nodes=0
- `no_road` name='Iloilo - Jordan' node=12452622222 lat=10.6677424 lon=122.58759450000001 comp_nodes=0
- `no_road` name='Iloilo – Buenavista' node=3024100757 lat=10.688855700000001 lon=122.6125353 comp_nodes=0
- `no_road` name='Cebu - Maasin' node=4719601968 lat=10.294523100000001 lon=123.91044830000001 comp_nodes=0
- `no_road` name='Manila - El Nido' node=8445519870 lat=14.5950687 lon=120.96324390000001 comp_nodes=0
- `no_road` name='Batangas-Odiongan' node=13826983956 lat=12.417294700000001 lon=121.9885081 comp_nodes=0
- `tiny` name='Legazpi City - Rapu-Rapu Ferry Route' node=5322818946 lat=13.184322900000002 lon=124.12259320000001 comp_nodes=43
- `no_road` name='Centennial Wharf - Calaguas' node=13036926686 lat=14.148490700000002 lon=122.9785511 comp_nodes=0
- `no_road` name='Centennial Wharf - Calaguas' node=2852993283 lat=14.4803183 lon=122.93916700000001 comp_nodes=0

#### europe_italy_isole (gen `20260930T084508Z-2389780-europe_italy_isole-070bfbe8`)

- `no_road` name='Malta - Marina di Ragusa' node=12850514327 lat=35.888326500000005 lon=14.507086500000002 comp_nodes=0
- `no_road` name='Malta - Pozzallo' node=2322162475 lat=35.8852901 lon=14.5010402 comp_nodes=0
- `no_road` name='Marsala-Favignana' node=2911201255 lat=37.791884700000004 lon=12.4351307 comp_nodes=0
- `no_road` name='Genova-Palermo' node=301390597 lat=44.4089363 lon=8.9123181 comp_nodes=0
- `no_road` name='Livorno - Palermo' node=1864720057 lat=43.5805018 lon=10.3034897 comp_nodes=0
- `no_road` name='Messina - Villa San Giovanni' node=1753698278 lat=38.221081600000005 lon=15.6329673 comp_nodes=0
- `no_road` name='Messina - Salerno' node=1728385616 lat=40.6702667 lon=14.741449300000001 comp_nodes=0
- `no_road` name='Tremestieri - Reggio di Calabria' node=2474427013 lat=38.123206100000004 lon=15.652166500000002 comp_nodes=0
- `no_road` name='Barcelona (E) – Posthudorra / Porto Torres (I)' node=30707380 lat=41.3624151 lon=2.17377 comp_nodes=0
- `no_road` name='Bonifacio - Santa Teresa di Gallura' node=257061395 lat=41.388641400000004 lon=9.156211800000001 comp_nodes=0
- `no_road` name='Genova - Porto Torres' node=1612874356 lat=44.412369500000004 lon=8.9123216 comp_nodes=0
- `no_road` name='Toulon - Porto Torres' node=2686376669 lat=43.1172156 lon=5.9293695 comp_nodes=0
- `no_road` name='Ajaccio - Porto Torres' node=1306490811 lat=41.922468 lon=8.740925 comp_nodes=0
- `no_road` name='Vado Ligure - Porto Torres' node=12179824208 lat=44.2627663 lon=8.4496538 comp_nodes=0
- `no_road` name='Civitavecchia - Olbia' node=456381084 lat=42.0990729 lon=11.782536 comp_nodes=0
- `no_road` name='Golfo Aranci - Porto Vecchio' node=1900085364 lat=41.587254200000004 lon=9.2912908 comp_nodes=0
- `no_road` name='Golfo Aranci - Porto Vecchio' node=12396707933 lat=41.005937 lon=9.690401600000001 comp_nodes=0
- `no_road` name='Livorno - Olbia' node=8989624642 lat=43.5480314 lon=10.2917374 comp_nodes=0
- `no_road` name='Genova - Olbia' node=1612874455 lat=44.412893000000004 lon=8.9132577 comp_nodes=0
- `no_road` name='Piombino - Olbia' node=370929662 lat=42.929700000000004 lon=10.546599200000001 comp_nodes=0

#### asia_indonesia_kalimantan (gen `20260929T190502Z-1335901-asia_indonesia_kalimantan-dc4cc92a`)

- `no_road` name='Batulicin - Makassar' node=8376613901 lat=-5.122135500000001 lon=119.4074487 comp_nodes=0
- `no_road` name='Batulicin - Pare-Pare' node=8265343168 lat=-4.002684 lon=119.6210664 comp_nodes=0
- `no_road` name='Surabaya - Batulicin' node=1509464158 lat=-7.198609500000001 lon=112.735117 comp_nodes=0
- `no_road` name='Tanjung Pandan - Tanjung Priok' node=2794303906 lat=-6.1037982 lon=106.8824532 comp_nodes=0
- `no_road` name='Kendal - Kumai' node=9583625763 lat=-6.9142277000000005 lon=110.28431490000001 comp_nodes=0
- `no_road` name='Balikpapan - Pantoloan' node=2419513902 lat=-0.7112336 lon=119.85604330000001 comp_nodes=0
- `no_road` name='Balikpapan - Mamuju' node=9515327687 lat=-2.6670335 lon=118.89324950000001 comp_nodes=0
- `no_road` name='Taipa - Kariangau' node=5947604949 lat=-0.7795713000000001 lon=119.8580211 comp_nodes=0
- `no_road` name='Kariangau - Mamuju' node=8264565916 lat=-2.6745813000000003 lon=118.86777110000001 comp_nodes=0
- `no_road` name='Awerange - Bontang' node=9514806014 lat=-4.219312 lon=119.6149678 comp_nodes=0
- `no_road` name='Bontang - Pare-Pare' node=7749854961 lat=-4.0125833 lon=119.6202238 comp_nodes=0
- `no_road` name='Tambelan-Sintete' node=12854856288 lat=0.9759453 lon=107.54912 comp_nodes=0
- `no_road` name='Serasan-Sintete' node=12860104473 lat=2.4948611 lon=109.0057177 comp_nodes=0
- `no_road` name='Serasan-Sintete' node=12864488207 lat=1.2021425000000001 lon=109.05247150000001 comp_nodes=0
- `no_road` name='Serasan-Sintete' node=12854874147 lat=2.4978503 lon=109.00616160000001 comp_nodes=0
- `no_road` name='Tolitoli - Tarakan' node=9509167491 lat=1.0374479 lon=120.80846820000001 comp_nodes=0
- `no_road` name='Tarakan - Tanjung Selor' node=11993130495 lat=2.8726134 lon=117.37628430000001 comp_nodes=0
- `no_road` name='Sebatik - Nunukan' node=13352501390 lat=4.1451817 lon=117.6463573 comp_nodes=0
- `tiny` name='Nunukan - Sei Menggaris' node=6382451580 lat=4.194230500000001 lon=117.3177174 comp_nodes=20

#### asia_indonesia_maluku (gen `20260929T141608Z-180067-asia_indonesia_maluku-092bc3cc`)

- `no_road` name='Kalabahi - Saumlaki' node=10583872532 lat=-8.2197007 lon=124.51640330000001 comp_nodes=0
- `no_road` name='Tepa - Saumlaki' node=6568744935 lat=-7.9794292 lon=131.2902163 comp_nodes=0
- `no_road` name='Manado - Jailolo' node=5606173047 lat=1.4961161 lon=124.8386425 comp_nodes=0
- `no_road` name='Bitung–Tobelo' node=1166224026 lat=1.4409877 lon=125.20020360000001 comp_nodes=0
- `no_road` name='Daruba–Posi-Posi' node=8341858142 lat=2.2905263000000002 lon=128.1772975 comp_nodes=0
- `no_road` name='Baubau - Namrole' node=2956957192 lat=-5.4536769000000005 lon=122.61032920000001 comp_nodes=0
- `no_road` name='Dobo - Timika' node=9477916673 lat=-4.820096400000001 lon=136.8486819 comp_nodes=0
- `no_road` name='Dobo - Timika' node=11889199307 lat=-4.8028404 lon=136.7690398 comp_nodes=0
- `no_road` name='Dobo - Kaimana' node=5765701908 lat=-3.6616055000000003 lon=133.757879 comp_nodes=0
- `no_road` name='Tual - Dobo' node=9477491607 lat=-5.6281407 lon=132.74107410000002 comp_nodes=0
- `no_road` name='Tual - Dobo' node=9477491595 lat=-5.7550907 lon=134.2393506 comp_nodes=0
- `no_road` name='Dobo - Kaimana' node=9490987754 lat=-5.7549532 lon=134.2394457 comp_nodes=0
- `tiny` name='Kuur - Tual' node=2062780835 lat=-5.309037900000001 lon=132.02272390000002 comp_nodes=48
- `no_road` name='Banda Eli - Holat' node=3511928224 lat=-5.4044243000000005 lon=133.1565287 comp_nodes=0
- `no_road` name='Wanci - Ambon' node=5732930542 lat=-5.338910800000001 lon=123.53354010000001 comp_nodes=0
- `no_road` name='Fak-Fak - Geser' node=6637440050 lat=-2.9323294 lon=132.3099335 comp_nodes=0
- `no_road` name='Banggai - Bobong' node=6384568943 lat=-1.6029222 lon=123.4924074 comp_nodes=0
- `no_road` name='Pulau Gag–Gebe' node=9571798604 lat=-0.44032160000000004 lon=129.9047371 comp_nodes=0
- `no_road` name='Bitung - Ternate' node=6766602998 lat=1.4384948000000002 lon=125.19155520000001 comp_nodes=0

#### asia_indonesia_sumatra (gen `20260929T191458Z-1335901-asia_indonesia_sumatra-d32b09df`)

- `no_road` name='Banda Aceh - Lamteng (Pulau Nasi)' node=9528650966 lat=5.6433112 lon=95.1619964 comp_nodes=0
- `tiny` name='Banda Aceh - Lamteng (Pulau Nasi)' node=7678537831 lat=5.5663158 lon=95.2946802 comp_nodes=2
- `tiny` name='Banda Aceh - Balohan (Pulau Weh)' node=14103876047 lat=5.5645828 lon=95.2950393 comp_nodes=4
- `no_road` name='Banda Aceh - Balohan (Pulau Weh)' node=7184058521 lat=5.826294000000001 lon=95.3470412 comp_nodes=0
- `no_road` name='Banda Aceh - Balohan (Pulau Weh)' node=4974426540 lat=5.82646 lon=95.34740790000001 comp_nodes=0
- `no_road` name=None node=7566779096 lat=-3.019311 lon=104.8367535 comp_nodes=0
- `no_road` name=None node=12184333075 lat=-2.9865153 lon=104.7708558 comp_nodes=0
- `no_road` name=None node=8941051020 lat=-2.9843576 lon=104.77481590000001 comp_nodes=0
- `no_road` name='Tanjung Kalian (Muntok) - Tanjung Api-Api' node=5258350966 lat=-2.0849396000000002 lon=105.1335622 comp_nodes=0
- `tiny` name=None node=6577532676 lat=-2.1354301 lon=104.2194695 comp_nodes=24
- `tiny` name=None node=7538605809 lat=-1.458234 lon=103.96715800000001 comp_nodes=21
- `tiny` name='Lasondre - Pulau Tello' node=1732918538 lat=-0.0244966 lon=98.30161930000001 comp_nodes=8
- `no_road` name='Bintan-Tambelan' node=12864488021 lat=0.703986 lon=104.4196405 comp_nodes=0
- `no_road` name='Bintan-Tambelan' node=12860138893 lat=0.9730184000000001 lon=107.54964290000001 comp_nodes=0
- `no_road` name='Tanjung Uban-Tambelan' node=12854856288 lat=0.9759453 lon=107.54912 comp_nodes=0
- `no_road` name='Tanjung Uban-Matak' node=12854874071 lat=3.3259831 lon=106.2399163 comp_nodes=0
- `no_road` name='Batu Ampar Batam - Tanjung Balai Karimun' node=9571235006 lat=1.1613713 lon=103.99399840000001 comp_nodes=0
- `no_road` name='Pel.Sagulung <> P.Seraya' node=702330145 lat=0.9641201 lon=103.97721650000001 comp_nodes=0
- `no_road` name='Tarempa-Letung' node=5217882501 lat=3.2178011000000004 lon=106.2169047 comp_nodes=0

#### europe_italy_sud (gen `20260930T075313Z-2389780-europe_italy_sud-95c203fc`)

- `no_road` name='Messina - Villa San Giovanni' node=247958241 lat=38.210311000000004 lon=15.5616576 comp_nodes=0
- `no_road` name='Tremestieri - Reggio di Calabria' node=1753698247 lat=38.1319596 lon=15.523484100000001 comp_nodes=0
- `no_road` name='Messina - Salerno' node=2012177911 lat=38.1907602 lon=15.5667383 comp_nodes=0
- `no_road` name=None node=444815368 lat=40.751157400000004 lon=13.907986800000002 comp_nodes=0
- `no_road` name=None node=2912541325 lat=40.7513087 lon=13.9074272 comp_nodes=0
- `no_road` name='Μπρίντιζι - Πάτρα' node=12754155803 lat=40.649373100000005 lon=17.9607865 comp_nodes=0
- `no_road` name='Μπρίντιζι - Πάτρα' node=8695549420 lat=38.226271100000005 lon=21.7189243 comp_nodes=0
- `no_road` name='Brindisi - Vlorë' node=2614985001 lat=40.449615300000005 lon=19.480891500000002 comp_nodes=0
- `no_road` name='Μπάρι - Ηγουμενίτσα' node=5109958764 lat=39.4879667 lon=20.256996 comp_nodes=0
- `no_road` name='Brindisi - Saranda' node=828717479 lat=39.8711597 lon=20.003437700000003 comp_nodes=0
- `no_road` name='Μπάρι - Κέρκυρα' node=828717455 lat=39.631708800000006 lon=19.901579 comp_nodes=0
- `no_road` name='Μπάρι - Κεφαλονιά (Σάμη)' node=940164019 lat=38.2523221 lon=20.645942700000003 comp_nodes=0
- `no_road` name='Μπάρι - Ζάκυνθος' node=667195491 lat=37.779853700000004 lon=20.9020487 comp_nodes=0
- `no_road` name='Bari - Durrës' node=828617203 lat=41.314328100000004 lon=19.4554133 comp_nodes=0
- `no_road` name='Bari - Dubrovnik' node=1825561035 lat=42.6605987 lon=18.0846456 comp_nodes=0
- `no_road` name='Ανκόνα - Ηγουμενίτσα / Ancona - Igoumenitsa' node=534965622 lat=43.617490800000006 lon=13.5057586 comp_nodes=0
- `no_road` name='Ανκόνα - Ηγουμενίτσα / Ancona - Igoumenitsa' node=1506860386 lat=39.4905264 lon=20.2576538 comp_nodes=0
- `no_road` name='Βενετία - Ηγουμενίτσα' node=11504661733 lat=45.4250667 lon=12.255484500000001 comp_nodes=0
- `no_road` name='Βενετία - Ηγουμενίτσα' node=1421824123 lat=39.4924163 lon=20.258386100000003 comp_nodes=0

#### north_america_canada_british_columbia_southcoast_admreg (gen `20260929T140812Z-180067-north_america_canada_british_columbia_southcoast_admreg-c1d51794`)

- `no_road` name='Alaska Marine Highway - Bellingham ↔ Ketchikan' node=1783130876 lat=48.7218238 lon=-122.5122525 comp_nodes=0
- `no_road` name='Alaska Marine Highway - Bellingham ↔ Ketchikan' node=1609416212 lat=55.353683800000006 lon=-131.69460700000002 comp_nodes=0
- `no_road` name='Berth 1 approach' node=8033282515 lat=49.3763816 lon=-123.2720596 comp_nodes=0
- `no_road` name='Berth 3 approach' node=13040590139 lat=49.376553900000005 lon=-123.27094330000001 comp_nodes=0
- `no_road` name=None node=2493379373 lat=49.3763279 lon=-123.27108860000001 comp_nodes=0
- `no_road` name=None node=7335482002 lat=49.376037000000004 lon=-123.27165810000001 comp_nodes=0
- `no_road` name=None node=8033282514 lat=49.376213 lon=-123.2715754 comp_nodes=0
- `no_road` name=None node=2493379372 lat=49.3760318 lon=-123.27188190000001 comp_nodes=0
- `no_road` name='Saltery Bay ↔ Earls Cove' node=2642596410 lat=49.781421400000006 lon=-124.17709970000001 comp_nodes=0
- `no_road` name='Duke Point ↔ Tsawwassen' node=5371656508 lat=49.003110500000005 lon=-123.1372869 comp_nodes=0
- `no_road` name='Duke Point ↔ Tsawwassen' node=470970816 lat=49.1625164 lon=-123.890973 comp_nodes=0
- `no_road` name='Langdale ↔ Horseshoe Bay' node=5371672522 lat=49.3802387 lon=-123.2712764 comp_nodes=0
- `no_road` name='Departure Bay ↔ Horseshoe Bay' node=2501354994 lat=49.195803600000005 lon=-123.95492680000001 comp_nodes=0
- `no_road` name='Galiano Island (Sturdies Bay) ↔ Tsawwassen' node=1327617345 lat=48.876566800000006 lon=-123.31486910000001 comp_nodes=0
- `no_road` name='Galiano Island (Sturdies Bay) ↔ Tsawwassen' node=5371661805 lat=49.0041243 lon=-123.1296614 comp_nodes=0
- `no_road` name='Swartz Bay ↔ Tsawwassen' node=323308217 lat=48.6945513 lon=-123.41021130000001 comp_nodes=0
- `no_road` name='Mayne Island (Village Bay) ↔ Tsawwassen' node=1682463778 lat=48.8447049 lon=-123.32482710000001 comp_nodes=0
- `no_road` name='Salt Spring Island (Long Harbour) ↔ Tsawwassen' node=1682319513 lat=48.852109000000006 lon=-123.44578220000001 comp_nodes=0
- `tiny` name='Barnston Island Ferry' node=393625552 lat=49.1922821 lon=-122.72334470000001 comp_nodes=46

#### asia_indonesia_papua (gen `20260929T141515Z-180067-asia_indonesia_papua-5e715de6`)

- `no_road` name='Dobo - Kaimana' node=9490987754 lat=-5.7549532 lon=134.2394457 comp_nodes=0
- `no_road` name='Dobo - Timika' node=9477491610 lat=-5.758596300000001 lon=134.239971 comp_nodes=0
- `no_road` name='Dobo - Timika' node=11889199293 lat=-5.756467000000001 lon=134.238852 comp_nodes=0
- `tiny` name='Atsy - Sawa Erma' node=9512410919 lat=-5.7328865 lon=138.3847103 comp_nodes=16
- `tiny` name='Atsy - Sawa Erma' node=9512425317 lat=-5.156231200000001 lon=138.2172404 comp_nodes=9
- `no_road` name='Fak-Fak - Geser' node=2062778447 lat=-3.8791752 lon=130.90297950000001 comp_nodes=0
- `no_road` name='Kaimana - Karas' node=5273491015 lat=-3.4685988 lon=132.87149820000002 comp_nodes=0
- `no_road` name='Wahai–Fak-Fak' node=9506700294 lat=-2.7944180000000003 lon=129.51530060000002 comp_nodes=0
- `no_road` name='Sorong–Fak-Fak' node=8172397221 lat=-1.0780671000000002 lon=131.177472 comp_nodes=0
- `no_road` name='Sorong–Lenmalas' node=3361851760 lat=-1.6950285 lon=130.2956327 comp_nodes=0
- `no_road` name='Lenmalas–Waigama' node=8417327417 lat=-1.8264464 lon=129.8459966 comp_nodes=0
- `no_road` name='Sorong–Wejim' node=9601918094 lat=-1.4635026 lon=130.2649169 comp_nodes=0
- `no_road` name='Wejim–Kofiau' node=10576017464 lat=-1.1499133000000001 lon=129.85098190000002 comp_nodes=0
- `tiny` name='Sorong - Folley' node=11834646777 lat=-1.7625118000000002 lon=130.4007283 comp_nodes=26
- `tiny` name='Arefi–Pam (Pulau Fam)' node=6132126175 lat=-0.6691054000000001 lon=130.29710490000002 comp_nodes=17
- `tiny` name='Arefi–Pam (Pulau Fam)' node=9512304727 lat=-0.7901130000000001 lon=130.702857 comp_nodes=5
- `no_road` name='Pulau Gag–Gebe' node=6789727361 lat=-0.0865854 lon=129.4353418 comp_nodes=0
- `tiny` name='Sorong–Selpele' node=11836680120 lat=-0.204262 lon=130.2227981 comp_nodes=6

#### europe_croatia (gen `20260930T075248Z-2389780-europe_croatia-af205e55`)

- `no_road` name='Ανκόνα - Πάτρα' node=8695549420 lat=38.226271100000005 lon=21.7189243 comp_nodes=0
- `no_road` name='Ανκόνα - Πάτρα' node=534965622 lat=43.617490800000006 lon=13.5057586 comp_nodes=0
- `no_road` name='Ανκόνα - Ηγουμενίτσα / Ancona - Igoumenitsa' node=1506860386 lat=39.4905264 lon=20.2576538 comp_nodes=0
- `no_road` name='Ανκόνα - Κέρκυρα' node=828717455 lat=39.631708800000006 lon=19.901579 comp_nodes=0
- `no_road` name='Ancona - Durrës' node=12618285380 lat=43.6211087 lon=13.5090781 comp_nodes=0
- `no_road` name='Ancona - Durrës' node=2619602926 lat=41.3144654 lon=19.4548218 comp_nodes=0
- `no_road` name='Split - Ancona' node=472133982 lat=43.6202295 lon=13.508531600000001 comp_nodes=0
- `no_road` name='Split - Rogač (Šolta) - Stomorska (Šolta) - Milna (Brač)' node=2376868989 lat=43.3286959 lon=16.4404276 comp_nodes=0
- `no_road` name='Split -Vis (Vis)' node=867428357 lat=43.0611444 lon=16.1904481 comp_nodes=0
- `no_road` name='Trieste - Durrës' node=447719199 lat=45.633651900000004 lon=13.766256700000001 comp_nodes=0
- `no_road` name='Trieste - Durrës' node=2619697123 lat=41.31463 lon=19.454157600000002 comp_nodes=0
- `no_road` name='Bari - Dubrovnik' node=534969123 lat=41.133152900000006 lon=16.8657049 comp_nodes=0
- `no_road` name='Ferry line 432 Biograd - Tkon (Pašman)' node=8719226763 lat=43.925096200000006 lon=15.418791500000001 comp_nodes=0
- `tiny` name='Ferry line 433 Zadar (Gaženica) – Rivanj – Sestrunj – Zverinac – Molat – Ist' node=1893515086 lat=44.153225000000006 lon=15.030265600000002 comp_nodes=21
- `no_road` name='Preko (Ugljan) - Ošljak' node=1340741901 lat=44.0760798 lon=15.206645100000001 comp_nodes=0
- `tiny` name='Silba - Premuda' node=845882222 lat=44.3735371 lon=14.691597300000002 comp_nodes=2
- `tiny` name='Zadar - Ist' node=1373593173 lat=44.2793295 lon=14.7571898 comp_nodes=28
- `tiny` name='Skela Križovec' node=3906158828 lat=46.4932291 lon=16.5025701 comp_nodes=9

## 4b. Stale or below v9 packs

Fix with a **targeted single-region bake now** (`./scripts/run-weekly.sh --region <id>`). Add to weekly only if the region is not already listed **and** it fits the weekly budget — never a planet run.

| bake_id | version | in weekly | action | conf line (if later weekly) |
|---|---:|---|---|---|
| hedmark | 8 | Y | single_region_bake_now | `hedmark	url:https://download.openstreetmap.fr/extracts/europe/norway/hedmark-latest.osm.pbf	terrain_class=wetland_heavy` |
| europe_united_kingdom_england_london_enfield | 6 | Y | single_region_bake_now | `europe_united_kingdom_england_london_enfield	geofabrik:europe/united-kingdom/england/london/enfield	skip_reason=geofabrik_retired` |

```bash
cd /media/navi/navi-server && set -a; source data/config.env; set +a
./scripts/run-weekly.sh --region hedmark
./scripts/run-weekly.sh --region europe_united_kingdom_england_london_enfield
```

## 5. Regions with no ferries

51 non-composite current/weekly regions have zero assigned `route=ferry` objects.

- `africa_lesotho`
- `africa_swaziland`
- `asia_armenia`
- `asia_india_north_eastern_zone`
- `asia_israel_and_palestine`
- `asia_japan_kanto`
- `asia_lebanon`
- `australia_oceania_australia_act`
- `australia_oceania_australia_ashmore_cartier`
- `australia_oceania_australia_christmas_island`
- `australia_oceania_australia_coral_sea_islands`
- `australia_oceania_australia_heard_mcdonald`
- `australia_oceania_australia_norfolk_island`
- `australia_oceania_ile_de_clipperton`
- `australia_oceania_kiribati`
- `australia_oceania_nauru`
- `australia_oceania_niue`
- `australia_oceania_tuvalu`
- `australia_oceania_wallis_et_futuna`
- `europe_andorra`
- `europe_czech_republic_pardubicky`
- `europe_france_bourgogne`
- `europe_france_picardie`
- `europe_france_reunion`
- `europe_kosovo`
- `europe_liechtenstein`
- `europe_spain_asturias`
- `europe_spain_castilla_y_leon`
- `europe_spain_la_rioja`
- `europe_spain_madrid`
- `europe_spain_navarra`
- `europe_sweden_dalarna`
- `europe_sweden_jamtland`
- `europe_united_kingdom_england_bedfordshire`
- `europe_united_kingdom_england_buckinghamshire`
- `europe_united_kingdom_england_derbyshire`
- `europe_united_kingdom_england_greater_manchester`
- `europe_united_kingdom_england_herefordshire`
- `europe_united_kingdom_england_leicestershire`
- `europe_united_kingdom_england_london_enfield`
- `europe_united_kingdom_england_northamptonshire`
- `europe_united_kingdom_england_rutland`
- `europe_united_kingdom_england_shropshire`
- `europe_united_kingdom_england_south_yorkshire`
- `north_america_canada_nunavut_kitikmeot`
- `north_america_canada_nunavut_kivalliq`
- `north_america_us_kansas`
- `north_america_us_kentucky`
- `north_america_us_new_mexico`
- `north_america_us_north_dakota`
- `north_america_us_south_dakota`

## Appendix: ferries in no region

Count: 4861. Sample:

- `way/26933521` class=passenger-bicycle-only name='Otrobanda - Punda'
- `way/27591989` class=passenger-bicycle-only name='Tyrell Bay, Carriacou - Petite Martinque'
- `way/40539392` class=passenger-bicycle-only name='Buca Bay to Korean Wharf on Taveuni Island'
- `way/43129406` class=passenger-bicycle-only name='Klein Bonaire Watertaxi'
- `way/44828691` class=passenger-bicycle-only name='Tiger IV'
- `way/44828912` class=passenger-bicycle-only name='Tiger IV'
- `way/106468201` class=passenger-bicycle-only name='Free Passenger Ferry Crossing'
- `way/106603174` class=car-capable name="St. George's, Grenada - Tyrell Bay, Carriacou"
- `way/174860630` class=car-capable name='Buca Bay Charter'
- `way/231289457` class=passenger-bicycle-only name=''
- `way/263505319` class=passenger-bicycle-only name='Yasawa Flyer'
- `way/341340249` class=passenger-bicycle-only name='Port of Spain - San Fernando Ferry'
- `way/352778304` class=passenger-bicycle-only name='De Palm Island Ferry'
- `way/358974087` class=passenger-bicycle-only name='Caribe Watersport Watertaxi'
- `way/420015608` class=passenger-bicycle-only name=''
- `way/491885332` class=passenger-bicycle-only name='Bounty to Klein Curacao and boat dives'
- `way/564102971` class=passenger-bicycle-only name='Marigot Beach Ferry Line'
- `way/674251239` class=passenger-bicycle-only name='Renaissance Island'
- `way/674251242` class=passenger-bicycle-only name='Renaissance Island'
- `way/718503398` class=passenger-bicycle-only name='Veerpont Isla Kiniw'
- `way/719032174` class=car-capable name='Scarborough - Port of Spain Ferry'
- `way/839057557` class=car-capable name='Kingstown, Saint Vincent - Port Elizabeth, Bequia'
- `way/839057560` class=car-capable name='Kingstown, Saint Vincent - Clifton, Union Island'
- `way/839057561` class=passenger-bicycle-only name='Clifton, Union Island - Mayreau'
- `way/839057562` class=passenger-bicycle-only name='Mayreau - Charlestown, Canouan'
- `way/839057563` class=passenger-bicycle-only name='Charlestown, Canouan - Kingstown, Saint Vincent'
- `way/839065288` class=passenger-bicycle-only name='Tyrell Bay, Carriacou - Clifton, Union Island'
- `way/921708259` class=passenger-bicycle-only name='Natovi – Nabouwalu'
- `way/921723669` class=passenger-bicycle-only name='Bairiki – Abaokoro'
- `way/921729907` class=passenger-bicycle-only name='Betio – Maiana'
- `way/921733582` class=passenger-bicycle-only name='Betio – Abaiang'
- `way/936282219` class=passenger-bicycle-only name='Petit Saint Vincent - Clifton, Union Island'
- `way/1119044215` class=passenger-bicycle-only name='Leleuvia resort ferry'
- `way/1120991878` class=passenger-bicycle-only name='Roseau - Fort de France'
- `way/1189788125` class=passenger-bicycle-only name='Tavewa Seabus'
- `way/1230654930` class=passenger-bicycle-only name='Water taxi to Grand Anse Beach'
- `way/1303429965` class=passenger-bicycle-only name='Paradise Cove Connector'
- `way/1303429966` class=passenger-bicycle-only name='Paradise Cove'
- `way/1303599995` class=unknown name='Soso <-> Paradise Cove'
- `way/1308427058` class=passenger-bicycle-only name=''
- … +4821 more

## Reproduction

```bash
SCRATCH=/tmp/navi-ferry-coverage-YYYYMMDD
CLONE=/tmp/navi-server-ferry-coverage
python3 $CLONE/scripts/ferry_coverage/overpass_fetch.py --scratch $SCRATCH
python3 $CLONE/scripts/ferry_coverage/tag_inventory.py --scratch $SCRATCH
python3 $CLONE/scripts/ferry_coverage/map_regions.py --scratch $SCRATCH
export CARGO_TARGET_DIR=$SCRATCH/target
cargo build -p pack-convert-core --release --bin ferry_pack_scan
nohup nice -n 19 ionice -c3 python3 $CLONE/scripts/ferry_coverage/compare_packs.py --scratch $SCRATCH --scan-bin $SCRATCH/target/release/ferry_pack_scan > $SCRATCH/logs/pack_compare.log 2>&1 &
tail -f $SCRATCH/logs/pack_scan_progress.log
python3 $CLONE/scripts/ferry_coverage/recommend_weekly.py --scratch $SCRATCH
python3 $CLONE/scripts/ferry_coverage/write_report.py --scratch $SCRATCH --repo $CLONE
```

