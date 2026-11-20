-- =============================================================================
-- Homelab Core Database Initialization Script
-- Executed automatically on first start when postgres_data volume is empty
-- =============================================================================

-- Database for Nextcloud storage & collaboration
CREATE DATABASE nextcloud OWNER fadb;
GRANT ALL PRIVILEGES ON DATABASE nextcloud TO fadb;

-- Database for Personal API Platform (PAP) Core Platform
CREATE DATABASE pap_core OWNER fadb;
GRANT ALL PRIVILEGES ON DATABASE pap_core TO fadb;

-- Database for Personal Server Manager (PSM) Operations & Deployments
CREATE DATABASE psm OWNER fadb;
GRANT ALL PRIVILEGES ON DATABASE psm TO fadb;

-- Database for IoT Eternal Project
CREATE DATABASE eternal_iot OWNER fadb;
GRANT ALL PRIVILEGES ON DATABASE eternal_iot TO fadb;
