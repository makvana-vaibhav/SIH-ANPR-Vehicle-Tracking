# Real linked-camera traffic data for NagarNetra

**Recommendation: use CityFlowV2 for the city journey demo, NGSIM for an accessible highway backup, and I24V when longer highway coverage matters most.** These sources contain observations of actual vehicles across related cameras. Iowa DOT is a concrete live-feed option, but requires building and validating the identities yourself.

Access and documentation were checked on 11 September 2026. This is a source-selection report, not a completed video-ingestion test. Full archives were not downloaded, and no particular vehicle's three-camera journey has yet been visually audited. Unknown metadata and access limitations are identified below rather than assumed.

## Ranked shortlist

| Rank | Source | Type | Best reason to choose it | Principal limitation |
|---|---|---|---|---|
| 1 | CityFlowV2, AI City 2022 Track 1 | A | Best fit for urban camera-to-camera investigation | Plates redacted; restrictive academic-use license |
| 2 | NGSIM I-80 / US-101 | A | Raw video plus ID-overlaid video makes journeys auditable | Older imagery; short corridor |
| 3 | I-24 MOTION Video, I24V | A for reference vehicles; own tracking for others | Strongest long highway corridor among this shortlist | Account approval; substantial processing and storage |
| 4 | I24-3D | A | Dense cross-camera annotations for a compact demonstration | Only 60–90 seconds per scene; dataset license needs confirmation |
| 5 | Iowa DOT I-80 camera network | B candidate | Real operational corridor and published stream URLs | No reference identities or verified capture-clock synchronization |

Type A means real connected-camera imagery, timing, and cross-camera identity evidence. Type B means connected real cameras whose identities must be established by the application. The ranking weighs demo practicality and evidence, not camera count alone. If a several-kilometre highway journey is the overriding priority, move I24V to first place after obtaining access.

## 1. CityFlowV2 — best overall fit

Official specification: [AI City 2022 Data and Evaluation](https://www.aicitychallenge.org/2022-data-and-evaluation/). Official entry point: [2022 Track 1 download](https://www.aicitychallenge.org/2022-track1-download/).

| Requested property | Verified finding or limitation |
|---|---|
| Actual video | Yes; real traffic, at least 960p, mostly 10 FPS |
| Cameras | 46 across 16 intersections |
| Connected geography | Yes within scenarios; six scenarios, maximum simultaneous-camera separation 4 km |
| Timestamps | Frame indices and per-video start offsets |
| Synchronization | Align recordings using those offsets; starting every file at frame zero is insufficient |
| Same vehicle across cameras | Yes; annotation selection requires appearances in at least two cameras |
| Vehicle IDs | 880 annotated identities; 313,931 boxes overall; use labelled splits |
| GPS / calibration | GPS metadata and camera calibration are documented; CityFlow supplies image-to-ground/GPS homographies |
| Plates | Redacted; unsuitable as the source of genuine OCR plate strings |
| Size | 3.58 aggregate video hours; current ZIP byte size not verified |
| Access | Official page links to `AICity22_Track1_MTMC_Tracking.zip` on Google Drive |
| License | Non-commercial academic use; no production/commercial use; distribution restricted |
| Hackathon feasibility | Strong technical fit for academic demonstration; public redistribution needs separate attention |
| Integration difficulty | Medium: clock alignment, identity adapter, calibrated locations, replay |
| Three-camera journey | Strong candidate; select an actual ID seen in three or more cameras, rather than assuming every ID qualifies |

The counts, split structure, timing offsets, and annotation criteria come from the [2022 specification](https://www.aicitychallenge.org/2022-data-and-evaluation/). The [original CityFlow paper](https://openaccess.thecvf.com/content_CVPR_2019/papers/Tang_CityFlow_A_City-Scale_Benchmark_for_Multi-Target_Multi-Camera_Vehicle_Tracking_and_CVPR_2019_paper.pdf) documents calibration and the city-camera layout; [AI City’s 2020 description](https://www.aicitychallenge.org/2020-data-and-evaluation/) explicitly documents plate and face redaction. Do not substitute the unrelated CityFlow traffic simulator or an image-only CityFlow-ReID download.

**Current access differs from older instructions.** The [current dataset-access page](https://www.aicitychallenge.org/ai-city-challenge-dataset-access/) says the listed datasets, including 2022 Track 1, no longer require a request form or password. It also says unlisted older datasets are no longer available. The official download link resolved to a [named Drive ZIP page](https://drive.google.com/file/d/13wNJpS_Oaoe-7y5Dzexg_Ol7bKu1OWuC/view); a successful full transfer and archive contents remain untested. Do not use the older 2020 release’s 15.7 GB figure as a verified 2022 ZIP size.

The linked [2022 license](https://www.aicitychallenge.org/wp-content/uploads/2022/02/Dataset-License-AIC2022.pdf) permits non-commercial academic use outside the challenge, prohibits commercial/production use including derived models, and generally restricts redistribution. The limited research-paper exception is not blanket permission to put footage in a public GitHub repository or a publicly downloadable demo. An academic hackathon prototype is a plausible fit; permission for a promotional public recording should not be assumed.

**Recommended use:** choose one labelled scenario, inventory IDs by camera, and find a clean three- or four-camera chain. Construct directed links from the supplied geography, field of view, and observed travel direction. A map route through an unseen gap is a reconstruction, not a continuously observed trajectory. Keep the original US geography in the demo.

CityFlow annotations deliberately omit vehicles that do not satisfy the cross-camera criteria, along with other difficult observations. They are therefore not a complete traffic census. Run the detector on all vehicles for counts and density; use reference IDs to validate linking. The [official FAQ](https://www.aicitychallenge.org/2022-faqs/) explains the annotation omissions.

## 2. NGSIM — best accessible highway backup

Official overview: [FHWA NGSIM Open Data](https://data.transportation.gov/stories/s/Next-Generation-Simulation-NGSIM-Open-Data/i5zb-xe34/). Start with [I-80 videos](https://data.transportation.gov/Automobiles/Next-Generation-Simulation-NGSIM-Program-I-80-Vide/2577-gpny), or [US-101 videos](https://data.transportation.gov/Automobiles/Next-Generation-Simulation-NGSIM-Program-US-101-Vi/4qzi-thur).

| Requested property | Verified finding or limitation |
|---|---|
| Actual video | Yes: raw AVI recordings and processed versions with vehicle-ID overlays |
| Cameras | I-80: 7; US-101: 8; arterial alternatives: Lankershim 5, Peachtree 8 |
| Connected geography | Yes within each site; separate sites are not a common journey |
| Timestamps | Dated recording intervals, frame IDs, and trajectory `Global_Time` |
| Synchronization | Collected by synchronized camera networks |
| Same vehicle across cameras | Yes across adjacent coverage within the study segment |
| Vehicle IDs | Trajectory IDs and visual overlays; scope IDs to site/session |
| GPS / calibration | Local/global trajectory coordinates and site documentation; not a ready camera latitude/longitude table |
| Plates | Readability and redaction not verified; no verified OCR labels |
| Size | Highway sites: three 15-minute periods; aggregate raw video 5.25 hours for I-80 and 6 hours for US-101; bytes unverified |
| Access | Public portal attachments; trajectory table can be exported separately |
| License | Video records: CC BY-SA 4.0; trajectory table: CC BY-SA 3.0 |
| Hackathon feasibility | Good for an auditable, repeatable real-data replay |
| Integration difficulty | Medium: decode AVI, map trajectories to camera coverage, convert coordinates |
| Three-camera journey | Yes in principle; select and visually confirm a vehicle spanning adjacent views |

The [FHWA overview](https://data.transportation.gov/stories/s/Next-Generation-Simulation-NGSIM-Open-Data/i5zb-xe34/) documents camera ordering and raw/processed videos. The [trajectory catalog](https://catalog.data.gov/dataset/next-generation-simulation-ngsim-vehicle-trajectories-and-supporting-data) documents synchronized collection and 0.1-second trajectory samples. This is recorded real traffic despite the word “Simulation” in the program name.

Public metadata was directly inspected: [I-80 metadata and attachments](https://data.transportation.gov/api/views/2577-gpny.json), [US-101 metadata and attachments](https://data.transportation.gov/api/views/4qzi-thur.json), and [trajectory schema/license](https://data.transportation.gov/api/views/8ect-6jqj.json). I-80 lists 42 AVI attachments: seven cameras, three periods, and both raw and processed copies. The trajectory table includes `Vehicle_ID`, `Frame_ID`, `Global_Time`, `Local_X/Y`, `Global_X/Y`, `v_Vel`, `Lane_ID`, and `Location`. Attribution and share-alike obligations apply to the relevant reused/adapted material; do not label the entire package public domain.

**Concrete starting files:** in the I-80 attachment list, select `nb-camera1-0400pm-0415pm.avi`, `nb-camera2-0400pm-0415pm.avi`, `nb-camera3-0400pm-0415pm.avi`, and `nb-camera4-0400pm-0415pm.avi`, plus their `-processed.avi` counterparts. These share a recording period. Use raw footage as pipeline input and overlays as independent visual reference. The files were listed, not downloaded and played.

NGSIM camera views can originate from a common elevated vantage point while covering successive road sections. They need not represent poles several kilometres apart. FHWA describes study roadways of approximately 0.5–1 km in its [program fact sheet](https://www.fhwa.dot.gov/publications/research/operations/its/06135/index.cfm). This supports a real short journey, not a 14 km city-crossing claim. Check the site coordinate reference system before converting global coordinates to map latitude/longitude, and validate speeds rather than treating every legacy trajectory sample as error-free.

## 3. I24V — strongest longer highway option

Official technical source: [I24V dataset documentation](https://github.com/I24-MOTION/i24-video-dataset-utils). Access: [I-24 MOTION data sharing](https://i24motion.org/index.php/data).

| Requested property | Verified finding or limitation |
|---|---|
| Actual video / cameras | 234 concurrent HD camera recordings |
| Connected geography | Overlapping coverage along 4.2 miles, approximately 6.76 km, of I-24 |
| Timing / synchronization | MKV timestamps plus filename offsets; compensate offsets before combining cameras |
| Same vehicle / IDs | 270 reference vehicle passes with GPS-based corrected trajectories; not exhaustive manual identity ground truth |
| Geography | Roadway and State Plane positions, calibration, eastbound/westbound transforms |
| Camera GPS | Geography is recoverable through coordinate tooling; a simple pole-coordinate CSV was not verified |
| Plates | Visible plates redacted; no usable OCR ground truth confirmed |
| Size | 234 aggregate video hours, approximately one concurrent hour; exact download bytes unverified |
| Access | Approved account through project portal; video package availability behind login not verified |
| License | Academic/commercial work and derivative sharing allowed under the dataset’s stated conditions; identifying individuals/harmful use prohibited |
| Hackathon feasibility | Excellent corridor material once access and a small working subset are secured |
| Integration difficulty | High initially: video volume, timestamp offsets, geometry and reference-track association |
| Three-camera journey | Strong fit for selected reference vehicles; verify the chosen cameras and pass |

The [repository](https://github.com/I24-MOTION/i24-video-dataset-utils) documents data formats and the data-use agreement. The [WACV paper](https://openaccess.thecvf.com/content/WACV2024/papers/Gloudemans_So_You_Think_You_Can_Track_WACV_2024_paper.pdf) describes the released video benchmark and redaction. Reference passes must not be presented as a complete set of human-labelled identities for every car; supplied algorithmic tracking results have a different evidentiary status.

The [data portal](https://i24motion.org/index.php/data) was reachable by HTTP and states free-account approval within one business day; that is the publisher’s stated turnaround, not a guarantee. Its access page returned access denied without login. Request the **I24V video benchmark**, rather than assuming the portal’s INCEPTION trajectory release contains the same videos. Account creation was not performed.

**Recommended use:** identify a reference pass first, then select three or four views along its route and a common time interval. Retain original timing when trimming/transcoding. Start with the coordinate conversion utilities instead of inventing locations. Overlapping views can see one vehicle simultaneously; those observations are not separate distance-travel events.

## 4. I24-3D — compact, densely annotated alternative

Official source: [I24-3D repository](https://github.com/I24-MOTION/I24-3D-dataset); [BMVC paper](https://papers.bmvc2023.org/0015.pdf).

| Requested property | Verified finding or limitation |
|---|---|
| Actual video / cameras | Real 4K, 30 FPS; 16–17 cameras per scene |
| Connected geography | Approximately 2,000 feet / 610 m; three roadside poles with multiple views |
| Timestamps | Frame-to-camera timestamp CSVs and trajectory timestamps |
| Synchronization | Corrected timestamps supplied; original timing contains known issues |
| Same vehicle / IDs | 720 identities; approximately 877,000 annotated boxes across three scenes |
| Geography | Calibrated roadway coordinates in feet, direction and homographies |
| Camera GPS | Direct latitude/longitude metadata not verified; map anchoring requires checking georeferencing |
| Plates | No OCR labels verified; redaction code exists, but released-video redaction was not independently inspected |
| Size | Scenes of 90, 60, and 60 seconds; roughly 57 aggregate camera-minutes; archive bytes unverified |
| Access | Project data portal; current authenticated package not inspected |
| License | Exact I24-3D data-use terms not verified; do not infer them from I24V or repository code |
| Hackathon feasibility | Technically useful after access/license confirmation; short demonstration |
| Integration difficulty | Medium–high; 4K decoding and coordinate transforms |
| Three-camera journey | Supported by the benchmark design; verify one scene/identity and distinguish camera views from poles |

The [paper](https://papers.bmvc2023.org/0015.pdf) establishes physical coverage and scene durations. The [dataset format specification](https://raw.githubusercontent.com/I24-MOTION/I24-3D-dataset/main/dataset_README.txt) separately documents unique object IDs, camera fields, direction, geometry, original timestamps and corrected timestamps. Its supplied schema is particularly useful for a replay adapter.

**Recommended use:** create a short handoff demonstration with selected views. Use corrected timestamps and preserve the scene namespace. Because the road is short, do not stretch it into a long-distance journey by putting its camera icons elsewhere on the map. A 60-second dataset scene also cannot establish long-term traffic trends.

## 5. Iowa DOT — concrete live connected-camera option

Official sources: [511 data feeds](https://iowadot.gov/travel-tools/iowa-511/511-data-feeds), [camera data item](https://www.arcgis.com/home/item.html?id=c4063f200a7b4da5826e2ac86c677cf5), and [live feature service](https://services.arcgis.com/8lRhdTsQyJpO52F1/arcgis/rest/services/Traffic_Cameras_View/FeatureServer/0).

| Requested property | Verified finding or limitation |
|---|---|
| Actual video | Published `VideoURL` entries include HLS playlists; individual streams not playback-tested |
| Camera count | Current query: 1,252 records, 853 distinct device IDs; 696 retained unique-device records had video URLs |
| Connected geography | Yes: routes, linear references, locations and coordinates available |
| Timestamps | Metadata update date/time and UTC offset; these do not establish frame capture time |
| Synchronization | No verified cross-camera capture-clock guarantee |
| Same vehicle | Physically possible on a selected corridor; no particular multi-camera vehicle verified |
| Vehicle IDs | None supplied for tracking |
| GPS / camera locations | Latitude, longitude, camera ID, description, route, linear reference |
| Plates | Visibility/redaction unverified; no plate labels |
| Size | Live service; no fixed archive size |
| Access | Credential-free GIS service; XML option has a request process |
| License | Camera item marked CC BY 4.0 with additional DOT terms; verify applicable video recording/redistribution terms |
| Hackathon feasibility | Good live source experiment; less predictable than recorded Type A data |
| Integration difficulty | High for reliable identity: recording, clock validation, topology and ReID |
| Three-camera journey | Conditional: must demonstrate it through your own matching and visual audit |

The counts above are a dated inventory query, not a claim that all devices are online. Duplicate GIS rows and null video URLs were present. The [service schema](https://services.arcgis.com/8lRhdTsQyJpO52F1/arcgis/rest/services/Traffic_Cameras_View/FeatureServer/0) was directly inspected; geometry is stronger evidence here than the number of records.

**A real corridor to investigate immediately:**

| Device ID | I-80 location description | Latitude | Longitude |
|---|---|---|---|
| 59614022 | MM 120.4, Ashworth Rd | 41.584979 | -93.822021 |
| 59614217 | MM 121.2, Jordan Creek Pkwy | 41.592093 | -93.809252 |
| 59614296 | MM 122.2, 60th St | 41.592612 | -93.790393 |
| 59614646 | MM 122.8, West Mix | 41.594104 | -93.780235 |

All four had non-null HLS video URLs in the official service. The direction visible in each view, stream availability, camera panning, and shared clock accuracy still need inspection. The descriptions establish adjacent locations, not a prevalidated identity chain. Fetch fresh `VideoURL` values by device ID rather than embedding old URLs. Iowa announced a camera-server migration in its [28 May 2026 advisory](https://iowadot.gov/news/2026-05-28/media-advisory-iowa-dot-updates-traffic-camera-video-system-data-feed-usersdevelopers).

The [camera item](https://www.arcgis.com/home/item.html?id=c4063f200a7b4da5826e2ac86c677cf5) is labelled CC BY 4.0 and links additional terms. The [DOT terms page](https://iowadot.gov/policies-statements/terms-use) contains separate data-use provisions. Availability of metadata alone is insufficient evidence of unrestricted archival redistribution of every linked video feed.

## Other sources screened out of the main ranking

| Source | What it provides | Why it does not replace the shortlist |
|---|---|---|
| [VeRi-776](https://github.com/JDAI-CV/VeRidataset) | Real cross-camera identity images: 776 vehicles, 20 cameras | Useful ReID material; not the continuous video release needed for this demo |
| [pNEUMA](https://open-traffic.epfl.ch/) / [pNEUMA Vision](https://github.com/shgold/pNEUMA-Vision-toolbox) | Real synchronized drone experiment; imagery extension samples every tenth frame | Useful analytics lead; raw continuous multi-camera video access and cross-drone ID stitching were not verified |
| [Ahmedabad SkyEye](https://github.com/debadityaroy/SkyEye) | Around one hour at each of four named Ahmedabad intersections | No verified simultaneous cross-intersection identities; geographical proximity alone is insufficient |
| [WSDOT highway-camera API](https://www.wsdot.wa.gov/traffic/api/Documentation/class_highway_cameras.html) | Official geographically indexed cameras | API explicitly supplies snapshots rather than full video |
| [TfL JamCams](https://tfl.gov.uk/info-for/open-data-users/our-open-data?intcmp=3671) | Real city-camera images; TfL also lists rolling video products | Published refresh cadence does not establish continuous synchronized coverage or identities |
| [Synthehicle](https://github.com/fubel/synthehicle) | Rich synthetic multi-camera tracking | Excluded because the requirement is real footage |
| [BrnoCompSpeed](https://github.com/JakubSochor/BrnoCompSpeed) | Real speed-measurement benchmark | Single-camera speed validation does not establish a cross-camera road journey |

No publicly downloadable Ahmedabad source with verified synchronized cross-camera vehicle identities was established. SkyEye is a relevant local lead, but should not be relabelled Type A without additional evidence. An authorized local three-camera recording can close the Indian-plate requirement if all cameras record the same interval and the same consenting test vehicle traverses the route.

## Integration and demo design

The following is an implementation recommendation, not metadata supplied by the datasets. It follows NagarNetra’s feed → observation → linking → journey architecture.

1. **Start with one real scenario and three or four cameras.** Keep original camera names and coordinates. Expand the map only after the first journey is audited. Forty-six or 234 cameras in a dataset does not mean every camera shares one continuous recording session.
2. **Create a synchronized replay clock.** Preserve source time separately from ingestion and playback time. Frame counts alone are insufficient when offsets or variable timing exist. Loop all camera clips over a common interval and start a new replay-session namespace on each loop.
3. **Allow identity without a plate.** Carry a nullable plate and a separate anonymous vehicle key. Namespace reference identity by dataset, scene/session, and ID. Do not turn `vehicle_42` into a fabricated Indian registration number.
4. **Separate reference labels from model predictions.** A reference-driven dashboard is a valid data-replay demo if labelled accordingly. A claim that the model linked a vehicle requires independent predictions compared with withheld reference identities. Ground truth must not silently drive the model’s displayed success rate.
5. **Choose a reference journey deliberately.** Group annotations by identity, require at least three distinct cameras, determine first/last observation times, check the ordered route and direction, and inspect crops. A vehicle may occupy two overlapping views simultaneously; merge those as overlapping evidence rather than counting fictitious travel.
6. **Use a directed road graph.** Camera coordinates alone do not specify an allowed turn or path. Record view footprints, travel direction, transitions and distance along the road. Use map-matched positions or road chainage for distance; camera pole separation is not automatically vehicle travel distance.
7. **Label elapsed travel-time speed accurately.** For compatible source-time observations, journey-average speed is road distance divided by elapsed time, including stops. It is not instantaneous speed. Suppress speed when timing or road distance is uncertain.
8. **Keep the ANPR demonstration honest.** Demonstrate genuine OCR on footage with readable, permitted plates. Use anonymous vehicle identity for redacted benchmark journeys. A watchlist alert on a reference identity can demonstrate the workflow, but is not evidence that OCR recovered a hidden plate.
9. **Compute analytics over supported coverage.** Define density as vehicles per unit road length and time, flow as crossing counts per interval, and hotspots over mapped segments. Deduplicate vehicles visible in overlapping cameras. Camera detection counts alone are not city-wide density.
10. **Show the distinction between observed and inferred route.** Draw observed points and reconstructed gaps differently. A missed intermediate camera is not automatically a route anomaly: outages, occlusion and missed detections are alternative explanations. Report the actual evidence and uncertainty.

For the existing approximately 9 FPS prototype, begin with a few streams and measure total throughput. Do not assume the stated performance applies simultaneously to all 51 feeds. Any downsampling or replay acceleration should preserve source timestamps for journey calculations.

## Immediate acquisition plan

**First:** obtain the 2022 Track 1 CityFlowV2 archive through its official download page. Inspect its README, labelled scenarios, camera offsets and calibration files. Select a reference identity spanning at least three cameras; only then commit to a specific journey script.

**In parallel as a manual download alternative:** use the four named NGSIM I-80 files from the same 15-minute period. Their processed counterparts are particularly useful for proving that the vehicle in camera 1 is the vehicle subsequently visible in cameras 2–4. Preserve the original date in the demo, or explicitly describe any shifted playback clock.

**For a longer highway showcase:** request I24V access and confirm video subset availability and storage needs. Prefer a small set of cameras tied to a reference pass over downloading the entire camera network.

**For genuinely live input:** inspect the four Iowa I-80 views together, then determine whether their latency, direction and image quality permit reliable linking. Until that is demonstrated, use them for live camera activity and keep validated journey replay as the dependable hackathon presentation.

The minimum successful deliverable is three real linked views, one visually verified vehicle identity, preserved timing, accurate geography, and an explicit distinction between reference data and inferred links. That demonstrates the core capability more convincingly than unrelated footage behind 51 camera icons.
