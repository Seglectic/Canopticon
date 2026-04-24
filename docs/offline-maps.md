# Offline Maps

The web UI can use a local PMTiles basemap when one is present in `data/maps/`.

## Current setup

- Primary statewide file: `data/maps/florida.pmtiles`
- Optional detail file: `data/maps/central-florida.pmtiles`
- Current statewide Florida target:
  - Bounding box: `-87.7,24.3,-79.8,31.1`
  - Max zoom: `15`
- Optional Central Florida detail target:
  - Bounding box: `-82.9,27.0,-80.7,29.5`
  - Max zoom: `15`

If the statewide file exists, the map tab defaults to the offline Florida basemap.
If the Central Florida file also exists, it appears as an extra selectable detail layer.
If neither exists, the UI falls back to live OpenStreetMap raster tiles.

## Extraction command

The offline files are created with `go-pmtiles` from the public Protomaps world build:

```bash
mkdir -p staging/pmtiles
cd staging/pmtiles
curl -L https://github.com/protomaps/go-pmtiles/releases/download/v1.30.2/go-pmtiles_1.30.2_Linux_x86_64.tar.gz -o go-pmtiles_1.30.2_Linux_x86_64.tar.gz
tar -xzf go-pmtiles_1.30.2_Linux_x86_64.tar.gz
./pmtiles extract https://build.protomaps.com/20260423.pmtiles florida_z15.pmtiles \
  --bbox=-87.7,24.3,-79.8,31.1 \
  --maxzoom=15 \
  --download-threads=16
cp florida_z15.pmtiles ../../data/maps/florida.pmtiles

./pmtiles extract https://build.protomaps.com/20260423.pmtiles central_florida_z15.pmtiles \
  --bbox=-82.9,27.0,-80.7,29.5 \
  --maxzoom=15 \
  --download-threads=16
cp central_florida_z15.pmtiles ../../data/maps/central-florida.pmtiles
```

## Notes

- This is a reasonable fit for a `32 GB` card.
- It is a basemap, not geocoding or routing.
- Live tiles can still be useful when the Pi has Ethernet uplink and is simultaneously serving the Wi-Fi hotspot.
