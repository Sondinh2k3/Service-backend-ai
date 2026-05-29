USE statistic;

-- AI Service tables (area registry, artifacts, cross configs, sync events, audit)

CREATE TABLE IF NOT EXISTS area_registry (
    area_id INTEGER PRIMARY KEY,
    area_name VARCHAR(255) NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    controller_visible BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS area_artifact (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    area_id INTEGER NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    config_version VARCHAR(64) NOT NULL,
    policy_path VARCHAR(512) NOT NULL,
    meta_path VARCHAR(512) NOT NULL,
    network_path VARCHAR(512),
    checksum VARCHAR(128),
    status VARCHAR(32) NOT NULL DEFAULT 'invalid',
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    activated_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_artifact_version UNIQUE (area_id, policy_version, config_version),
    FOREIGN KEY (area_id) REFERENCES area_registry(area_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS area_cross_config (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    area_id INTEGER NOT NULL,
    cross_id INTEGER NOT NULL,
    config_version VARCHAR(64) NOT NULL DEFAULT '1',
    config_payload_json TEXT NOT NULL,
    checksum VARCHAR(128),
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_area_cross UNIQUE (area_id, cross_id),
    FOREIGN KEY (area_id) REFERENCES area_registry(area_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS real_network_snapshot (
    area_id INTEGER PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    network_id VARCHAR(128) NOT NULL,
    schema_version VARCHAR(32) NOT NULL DEFAULT 'real-network/v1',
    source_version VARCHAR(128),
    payload_json TEXT NOT NULL,
    checksum VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (area_id) REFERENCES area_registry(area_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_event (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    source_event_id VARCHAR(128) NOT NULL UNIQUE,
    source_system VARCHAR(64) NOT NULL DEFAULT 'central-backend',
    event_type VARCHAR(64) NOT NULL,
    payload_hash VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'applied',
    error_message TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inference_audit (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    request_id VARCHAR(64) NOT NULL,
    area_id INTEGER,
    policy_version VARCHAR(64),
    config_version VARCHAR(64),
    num_crosses INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'ok',
    error_code VARCHAR(64),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed: Area 1 with policy.onnx artifact
INSERT IGNORE INTO area_registry (area_id, area_name, is_active, controller_visible)
    VALUES (1, 'Area 1', 1, 1);

INSERT IGNORE INTO area_artifact
    (area_id, policy_version, config_version, policy_path, meta_path, network_path, status, is_active)
    VALUES (1, '1.0', '1.0', 'models/area_1/policy.onnx', 'models/area_1/policy_meta.json', 'models/area_1/network.json', 'ready', 1);
