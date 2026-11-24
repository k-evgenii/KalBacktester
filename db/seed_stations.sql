-- Seed: the KNYC station and the NYC_HIGH link.
-- Values come from api_probe/weather/dump/KNYC/station.json (IEM GeoJSON feature):
-- name = properties.sname, longitude/latitude = geometry.coordinates [lon, lat],
-- elevation = properties.elevation, tz = properties.tzname, iem id = id, network = properties.network.
-- std_utc_offset_h and cli_pil are not in that file: -5 is New York standard time, CLINYC is the NWS report id.
INSERT INTO station
    (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h, cli_pil, iem_id, iem_network)
VALUES
    ('KNYC', 'NEW YORK CITY', 40.779, -73.9692, 27.0, 'America/New_York', -5, 'CLINYC', 'NYC', 'NY_ASOS')
ON CONFLICT DO NOTHING;

UPDATE underlying SET station_id = 'KNYC' WHERE underlying_id = 'NYC_HIGH' AND station_id IS NULL;

UPDATE station
SET rules_names = ARRAY['(CLINYC)', 'Central Park, New York', 'New York City']
WHERE station_id = 'KNYC' AND rules_names = '{}';
