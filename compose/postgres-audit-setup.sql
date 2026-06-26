-- PostgreSQL audit logging setup
-- Enables pgaudit for comprehensive audit trail

-- Create audit schema
CREATE SCHEMA IF NOT EXISTS audit;

-- Create audit log table
CREATE TABLE IF NOT EXISTS audit.audit_log (
  id BIGSERIAL PRIMARY KEY,
  timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
  username TEXT,
  database_name TEXT,
  object_type TEXT,
  object_name TEXT,
  statement TEXT,
  action TEXT,
  result TEXT,
  application_name TEXT
);

-- Create index for common queries
CREATE INDEX idx_audit_log_timestamp ON audit.audit_log(timestamp DESC);
CREATE INDEX idx_audit_log_username ON audit.audit_log(username);
CREATE INDEX idx_audit_log_action ON audit.audit_log(action);

-- Enable pgaudit extension
CREATE EXTENSION IF NOT EXISTS pgaudit;

-- Configure pgaudit to log all DDL
ALTER SYSTEM SET pgaudit.log = 'DDL, DML, ROLE';
ALTER SYSTEM SET pgaudit.log_statement = ON;
ALTER SYSTEM SET pgaudit.log_statement_once = OFF;

-- Apply configuration changes
SELECT pg_reload_conf();

-- Grant audit schema permissions
GRANT USAGE ON SCHEMA audit TO public;
GRANT SELECT ON audit.audit_log TO public;
