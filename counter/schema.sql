-- Two running totals: installs and words dictated. No IDs, IP addresses, names, devices or text.
DROP TABLE IF EXISTS active;
DROP TABLE IF EXISTS installs;
CREATE TABLE IF NOT EXISTS totals (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO totals (name, value) VALUES ('installs', 0), ('words', 0);
