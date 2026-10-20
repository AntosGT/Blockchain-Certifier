-- prima-certified 004: administrator access to the dashboard. Only users with role = 'admin' and a password can log in.

ALTER TABLE users ADD COLUMN password_hash VARCHAR(255);
