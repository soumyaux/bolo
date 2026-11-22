-- Only a hash of a random ID made on the user's Mac, and the day. No IP, name, device or text.
CREATE TABLE IF NOT EXISTS installs (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS active (id TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY (id, day));
CREATE INDEX IF NOT EXISTS active_day ON active (day);
