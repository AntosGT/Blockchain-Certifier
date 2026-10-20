-- prima-certified 015: project of each registration. `source` already existed since 007 (default value 'portal'); from now on it can also be 'mcp'.
-- The project is the name of the client's folder where the original is kept; the certificate notification mentions it.

ALTER TABLE evidence_requests ADD COLUMN project VARCHAR(255);
