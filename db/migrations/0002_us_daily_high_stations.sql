-- US daily-high stations, underlyings and series (unit A019).
-- Generated 2026-11-24 by api_probe/kalshi/build_us_seed.py.
-- Window: 2026-08-24 to 2026-11-22.
-- Skipped series:
--   KXHIGHHOU: inactive (no market in window)
--   KXHIGHOU: inactive (no market in window)
--   KXHIGHTCYYZ: inactive (no market in window)
--   KXHIGHTEBBR: inactive (no market in window)
--   KXHIGHTEDDB: inactive (no market in window)
--   KXHIGHTEDDF: inactive (no market in window)
--   KXHIGHTEGLL: inactive (no market in window)
--   KXHIGHTEHAM: inactive (no market in window)
--   KXHIGHTEMPDEN: inactive (no market in window)
--   KXHIGHTKSAN: inactive (no market in window)
--   KXHIGHTLFPG: inactive (no market in window)
--   KXHIGHTLSGG: inactive (no market in window)
--   KXHIGHTLTFM: inactive (no market in window)
--   KXHIGHTMMMX: inactive (no market in window)
--   KXHIGHTOMDB: inactive (no market in window)
--   KXHIGHTRJTT: inactive (no market in window)
--   KXHIGHTRKSI: inactive (no market in window)
--   KXHIGHTSBGR: inactive (no market in window)
--   KXHIGHTVABB: inactive (no market in window)
--   KXHIGHTVHHH: inactive (no market in window)
--   KXHIGHTWSSS: inactive (no market in window)
--   KXHIGHTYSSY: inactive (no market in window)
--   KXHIGHTZBAA: inactive (no market in window)
--   KXHIGHTZSPD: inactive (no market in window)

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KATL', 'ATLANTA INTL ARPT', 33.6301, -84.4418, 315.0, 'America/New_York', -5, 'CLIATL', 'ATL', 'GA_ASOS',
        ARRAY['(CLIATL)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('ATL_HIGH', 'Daily maximum temperature, ATLANTA INTL ARPT (KATL)', 'KATL') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTATL', 'ATL_HIGH', 'Atlanta Max Temperature') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KAUS', 'Austin Bergstrom Intl', 30.183, -97.6799, 146.0, 'America/Chicago', -6, 'CLIAUS', 'AUS', 'TX_ASOS',
        ARRAY['(CLIAUS)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('AUS_HIGH', 'Daily maximum temperature, Austin Bergstrom Intl (KAUS)', 'KAUS') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHAUS', 'AUS_HIGH', 'Highest temperature in Austin') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KBOS', 'BOSTON/LOGAN INTL', 42.3606, -71.0097, 9.0, 'America/New_York', -5, 'CLIBOS', 'BOS', 'MA_ASOS',
        ARRAY['(CLIBOS)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('BOS_HIGH', 'Daily maximum temperature, BOSTON/LOGAN INTL (KBOS)', 'KBOS') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTBOS', 'BOS_HIGH', 'Boston Maximum Daily Temperature') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KDCA', 'WASHINGTON/NATIONAL', 38.8472, -77.0346, 20.0, 'America/New_York', -5, 'CLIDCA', 'DCA', 'VA_ASOS',
        ARRAY['(CLIDCA)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('DCA_HIGH', 'Daily maximum temperature, WASHINGTON/NATIONAL (KDCA)', 'KDCA') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTDC', 'DCA_HIGH', 'Washington DC Daily Max Temp') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KDEN', 'DENVER INTNL ARPT', 39.8328, -104.6575, 1656.0, 'America/Denver', -7, 'CLIDEN', 'DEN', 'CO_ASOS',
        ARRAY['(CLIDEN)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('DEN_HIGH', 'Daily maximum temperature, DENVER INTNL ARPT (KDEN)', 'KDEN') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHDEN', 'DEN_HIGH', 'Highest temperature in Denver') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KDFW', 'DALLAS/FT WORTH', 32.8968, -97.038, 182.0, 'America/Chicago', -6, 'CLIDFW', 'DFW', 'TX_ASOS',
        ARRAY['(CLIDFW)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('DFW_HIGH', 'Daily maximum temperature, DALLAS/FT WORTH (KDFW)', 'KDFW') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTDAL', 'DFW_HIGH', 'Dallas Maximum Temperature') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KEWR', 'Newark Intl', 40.6827, -74.1693, 2.0, 'America/New_York', -5, 'CLIEWR', 'EWR', 'NJ_ASOS',
        ARRAY['(CLIEWR)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('EWR_HIGH', 'Daily maximum temperature, Newark Intl (KEWR)', 'KEWR') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTEWR', 'EWR_HIGH', 'Newark, NJ (EWR) Daily Max Temp') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KHOU', 'HOUSTON/WILL HOBBY', 29.6375, -95.2824, 14.0, 'America/Chicago', -6, 'CLIHOU', 'HOU', 'TX_ASOS',
        ARRAY['(CLIHOU)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('HOU_HIGH', 'Daily maximum temperature, HOUSTON/WILL HOBBY (KHOU)', 'KHOU') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTHOU', 'HOU_HIGH', 'Daily High Temperature Houston') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KLAS', 'Las Vegas - Harry Reid Intl', 36.0719, -115.1634, 663.0, 'America/Los_Angeles', -8, 'CLILAS', 'LAS', 'NV_ASOS',
        ARRAY['(CLILAS)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('LAS_HIGH', 'Daily maximum temperature, Las Vegas - Harry Reid Intl (KLAS)', 'KLAS') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTLV', 'LAS_HIGH', 'Las Vegas Max Daily Temperature') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KLAX', 'LOS ANGELES INTL', 33.9382, -118.3865, 32.0, 'America/Los_Angeles', -8, 'CLILAX', 'LAX', 'CA_ASOS',
        ARRAY['(CLILAX)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('LAX_HIGH', 'Daily maximum temperature, LOS ANGELES INTL (KLAX)', 'KLAX') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHLAX', 'LAX_HIGH', 'Highest temperature in Los Angeles') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KMDW', 'CHICAGO', 41.786, -87.7524, 188.0, 'America/Chicago', -6, 'CLIMDW', 'MDW', 'IL_ASOS',
        ARRAY['(CLIMDW)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('MDW_HIGH', 'Daily maximum temperature, CHICAGO (KMDW)', 'KMDW') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHCHI', 'MDW_HIGH', 'Highest temperature in Chicago') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KMIA', 'MIAMI INTL AIRPORT', 25.788, -80.3169, 4.0, 'America/New_York', -5, 'CLIMIA', 'MIA', 'FL_ASOS',
        ARRAY['(CLIMIA)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('MIA_HIGH', 'Daily maximum temperature, MIAMI INTL AIRPORT (KMIA)', 'KMIA') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHMIA', 'MIA_HIGH', 'Highest temperature in Miami') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KMSP', 'MINNEAPOLIS', 44.8854, -93.2313, 265.0, 'America/Chicago', -6, 'CLIMSP', 'MSP', 'MN_ASOS',
        ARRAY['(CLIMSP)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('MSP_HIGH', 'Daily maximum temperature, MINNEAPOLIS (KMSP)', 'KMSP') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTMIN', 'MSP_HIGH', 'Minneapolis Daily High Temperature') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KMSY', 'New Orleans - Louis Armstrong', 29.9933, -90.2511, 0.0, 'America/Chicago', -6, 'CLIMSY', 'MSY', 'LA_ASOS',
        ARRAY['(CLIMSY)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('MSY_HIGH', 'Daily maximum temperature, New Orleans - Louis Armstrong (KMSY)', 'KMSY') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTNOLA', 'MSY_HIGH', 'New Orleans Max temp Daily') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KNYC', 'NEW YORK CITY', 40.779, -73.9692, 27.0, 'America/New_York', -5, 'CLINYC', 'NYC', 'NY_ASOS',
        ARRAY['(CLINYC)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('NYC_HIGH', 'Daily maximum temperature, NEW YORK CITY (KNYC)', 'KNYC') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHNY', 'NYC_HIGH', 'Highest temperature in NYC') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KOKC', 'Oklahoma City', 35.3889, -97.6006, 397.0, 'America/Chicago', -6, 'CLIOKC', 'OKC', 'OK_ASOS',
        ARRAY['(CLIOKC)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('OKC_HIGH', 'Daily maximum temperature, Oklahoma City (KOKC)', 'KOKC') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTOKC', 'OKC_HIGH', 'Oklahoma City Maximum High Temperature') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KPHL', 'Philadelphia Intl', 39.8734, -75.2266, 2.0, 'America/New_York', -5, 'CLIPHL', 'PHL', 'PA_ASOS',
        ARRAY['(CLIPHL)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('PHL_HIGH', 'Daily maximum temperature, Philadelphia Intl (KPHL)', 'KPHL') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHPHIL', 'PHL_HIGH', 'Highest temperature in Philadelphia') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KPHX', 'PHOENIX/SKY HARBOR', 33.4343, -112.0116, 337.0, 'America/Phoenix', -7, 'CLIPHX', 'PHX', 'AZ_ASOS',
        ARRAY['(CLIPHX)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('PHX_HIGH', 'Daily maximum temperature, PHOENIX/SKY HARBOR (KPHX)', 'KPHX') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTPHX', 'PHX_HIGH', 'Phoenix High Temperature Daily') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KSAN', 'SAN DIEGO/LINDBERG', 32.7339, -117.1845, 9.0, 'America/Los_Angeles', -8, 'CLISAN', 'SAN', 'CA_ASOS',
        ARRAY['(CLISAN)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('SAN_HIGH', 'Daily maximum temperature, SAN DIEGO/LINDBERG (KSAN)', 'KSAN') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTSAN', 'SAN_HIGH', 'High Temperature San Diego') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KSAT', 'San Antonio', 29.53, -98.4673, 236.0, 'America/Chicago', -6, 'CLISAT', 'SAT', 'TX_ASOS',
        ARRAY['(CLISAT)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('SAT_HIGH', 'Daily maximum temperature, San Antonio (KSAT)', 'KSAT') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTSATX', 'SAT_HIGH', 'San Antonio Daily Maximum Temperature') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KSDF', 'Louisville Intl', 38.1774, -85.7308, 146.0, 'America/New_York', -5, 'CLISDF', 'SDF', 'KY_ASOS',
        ARRAY['(CLISDF)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('SDF_HIGH', 'Daily maximum temperature, Louisville Intl (KSDF)', 'KSDF') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTSDF', 'SDF_HIGH', 'HIGHEST Temperature Louisville') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KSEA', 'SEATTLE-TACOMA INTL', 47.4447, -122.3144, 137.0, 'America/Los_Angeles', -8, 'CLISEA', 'SEA', 'WA_ASOS',
        ARRAY['(CLISEA)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('SEA_HIGH', 'Daily maximum temperature, SEATTLE-TACOMA INTL (KSEA)', 'KSEA') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTSEA', 'SEA_HIGH', 'Seattle Maximum Temperature Daily') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KSFO', 'SAN FRANCISCO INTL', 37.619, -122.3749, 5.0, 'America/Los_Angeles', -8, 'CLISFO', 'SFO', 'CA_ASOS',
        ARRAY['(CLISFO)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('SFO_HIGH', 'Daily maximum temperature, SAN FRANCISCO INTL (KSFO)', 'KSFO') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTSFO', 'SFO_HIGH', 'San Francisco High Temperature Daily') ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('KTTN', 'TRENTON/MERCER CO.', 40.2768, -74.8159, 65.0, 'America/New_York', -5, 'CLITTN', 'TTN', 'NJ_ASOS',
        ARRAY['(CLITTN)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('TTN_HIGH', 'Daily maximum temperature, TRENTON/MERCER CO. (KTTN)', 'KTTN') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHTTTN', 'TTN_HIGH', 'Trenton, NJ Daily Max Temp') ON CONFLICT DO NOTHING;
