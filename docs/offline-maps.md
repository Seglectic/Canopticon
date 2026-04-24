# Offline Maps

The web UI can use a local PMTiles basemap when one is present in `data/maps/`.
Canopticon now auto-builds the configured state bundle on startup when it is missing.

## Current setup

- Primary statewide file for the default configuration: `data/maps/florida.pmtiles`
- Current default Florida target:
  - Bounding box: `-87.7,24.3,-79.8,31.1`
  - Max zoom: `15`

If the statewide file exists, the map tab defaults to the offline Florida basemap.

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

```

## Notes

- This is a reasonable fit for a `32 GB` card.
- It is a basemap, not geocoding or routing.
