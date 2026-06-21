-- 001_initial.sql

-- Extensions
CREATE EXTENSION IF NOT EXISTS pgmq;

-- Runtime state (key‑value store)
CREATE TABLE IF NOT EXISTS runtime_state (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- World registry
CREATE TABLE IF NOT EXISTS world_registry (
    org_name        TEXT PRIMARY KEY,
    capabilities    TEXT[] NOT NULL DEFAULT '{}',
    and_profile     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Address pools
CREATE TABLE IF NOT EXISTS address_pools (
    profile    TEXT PRIMARY KEY,
    cidr       CIDR NOT NULL,
    allocated  CIDR[] NOT NULL DEFAULT '{}'
);

-- Address leases (row‑level locking)
CREATE TABLE IF NOT EXISTS address_leases (
    and_name    TEXT PRIMARY KEY,
    cidr        CIDR NOT NULL,
    assigned_at TIMESTAMPTZ DEFAULT NOW()
);

-- Domain records
CREATE TABLE IF NOT EXISTS domain_records (
    domain      TEXT PRIMARY KEY,
    org_name    TEXT REFERENCES world_registry(org_name),
    ns_records  TEXT[] NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Operator audit log
CREATE TABLE IF NOT EXISTS operator_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT,
    method      TEXT,
    path        TEXT,
    status      INT,
    request_body JSONB,
    response_body JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- pgmq queues
SELECT pgmq.create('dns_updates');
SELECT pgmq.create('dns_updates_dlq');
SELECT pgmq.create('oidc_provisioning');
SELECT pgmq.create('oidc_provisioning_dlq');
SELECT pgmq.create('and_provisioning');
SELECT pgmq.create('and_provisioning_dlq');

-- pgmq_send(queue_name, message)
CREATE OR REPLACE FUNCTION pgmq_send(queue_name text, message text)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    msg_id bigint;
BEGIN
    SELECT pgmq.send(queue_name, message) INTO msg_id;
    RETURN msg_id;
END;
$$;

-- pgmq_pop(queue_name, timeout)
CREATE OR REPLACE FUNCTION pgmq_pop(queue_name text, timeout int)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    msg JSONB;
BEGIN
    SELECT pgmq.pop(queue_name, timeout) INTO msg;
    RETURN msg;
END;
$$;

-- pgmq_delete(queue_name, msg_id)
CREATE OR REPLACE FUNCTION pgmq_delete(queue_name text, msg_id bigint)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pgmq.delete(queue_name, msg_id);
    RETURN TRUE;
END;
$$;