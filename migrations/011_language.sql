-- prima-certified 011: client language. It is chosen on the home page (English by default, Spanish at /es), travels with the link
-- in the email and stays in the request, so that the notifications (and n8n) answer in that language.
-- Earlier requests were all in Spanish.

ALTER TABLE email_verifications ADD COLUMN lang VARCHAR(2) NOT NULL DEFAULT 'en' CHECK (lang IN ('en', 'es'));
ALTER TABLE evidence_requests ADD COLUMN lang VARCHAR(2) NOT NULL DEFAULT 'es' CHECK (lang IN ('en', 'es'));
