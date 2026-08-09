-- You2 brain substrate. One SQLite file; "copy the file" is the whole
-- backup story, which is worth a great deal on a device like this.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

-- ---------------------------------------------------------------- neurons

CREATE TABLE IF NOT EXISTS neurons (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT    NOT NULL,
    content          TEXT    NOT NULL DEFAULT '',
    key              TEXT,              -- stable identity, e.g. user/coffee_preference
    gist             TEXT,              -- survives cold-tier compaction

    created_at       REAL    NOT NULL,
    last_access      REAL    NOT NULL,
    access_count     INTEGER NOT NULL DEFAULT 0,

    valid_at         REAL,              -- bitemporal
    invalid_at       REAL,
    superseded_by    INTEGER REFERENCES neurons(id) ON DELETE SET NULL,

    storage_strength REAL    NOT NULL DEFAULT 1.0,   -- monotone, never decreases

    salience         REAL    NOT NULL DEFAULT 0.0,
    surprise         REAL    NOT NULL DEFAULT 0.0,
    arousal          REAL    NOT NULL DEFAULT 0.0,
    valence          REAL    NOT NULL DEFAULT 0.0,
    confidence       REAL    NOT NULL DEFAULT 1.0,
    pinned           INTEGER NOT NULL DEFAULT 0,

    tier             TEXT    NOT NULL DEFAULT 'hot',
    absorbed         INTEGER NOT NULL DEFAULT 0,
    times_replayed   INTEGER NOT NULL DEFAULT 0,

    session_id       TEXT,
    source_ref       TEXT,
    meta             TEXT
);

CREATE INDEX IF NOT EXISTS ix_neurons_kind        ON neurons(kind);
CREATE INDEX IF NOT EXISTS ix_neurons_tier        ON neurons(tier);
CREATE INDEX IF NOT EXISTS ix_neurons_created     ON neurons(created_at);
CREATE INDEX IF NOT EXISTS ix_neurons_access      ON neurons(last_access);
CREATE INDEX IF NOT EXISTS ix_neurons_session     ON neurons(session_id);
CREATE INDEX IF NOT EXISTS ix_neurons_kind_tier   ON neurons(kind, tier);
-- Fact identity: the lookup that makes supersession possible.
CREATE INDEX IF NOT EXISTS ix_neurons_key         ON neurons(key) WHERE key IS NOT NULL;
-- Replay sampling scans this.
CREATE INDEX IF NOT EXISTS ix_neurons_replay      ON neurons(kind, times_replayed, salience);

-- Entity aliases, so "Dave" and "David Anderson" are the same neuron.
CREATE TABLE IF NOT EXISTS aliases (
    alias     TEXT    NOT NULL,
    neuron_id INTEGER NOT NULL REFERENCES neurons(id) ON DELETE CASCADE,
    PRIMARY KEY (alias, neuron_id)
);
CREATE INDEX IF NOT EXISTS ix_aliases_neuron ON aliases(neuron_id);

-- ------------------------------------------------------------------ edges

CREATE TABLE IF NOT EXISTS edges (
    src        INTEGER NOT NULL REFERENCES neurons(id) ON DELETE CASCADE,
    dst        INTEGER NOT NULL REFERENCES neurons(id) ON DELETE CASCADE,
    type       TEXT    NOT NULL,
    weight     REAL    NOT NULL DEFAULT 0.1,
    created_at REAL    NOT NULL DEFAULT 0.0,
    last_fired REAL    NOT NULL DEFAULT 0.0,
    fire_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (src, dst, type)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_edges_dst    ON edges(dst);
CREATE INDEX IF NOT EXISTS ix_edges_weight ON edges(weight);

-- ---------------------------------------------------------------- vectors

-- int8 with a per-vector scale: 4x smaller than float32, negligible recall
-- loss at this precision, and the index only picks cue seeds anyway.
CREATE TABLE IF NOT EXISTS vectors (
    neuron_id INTEGER PRIMARY KEY REFERENCES neurons(id) ON DELETE CASCADE,
    dim       INTEGER NOT NULL,
    scale     REAL    NOT NULL,
    vec       BLOB    NOT NULL
);

-- --------------------------------------------------------------- sessions

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at   REAL,
    summary    TEXT,
    turns      INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------- journal/audit

-- Everything the night does, and everything surfaced to the user, is logged
-- here. When retrieval quality moves, this is the first thing you read.
CREATE TABLE IF NOT EXISTS journal (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_journal_ts   ON journal(ts);
CREATE INDEX IF NOT EXISTS ix_journal_kind ON journal(kind);

-- The rating loop, built on day one because there is no benchmark for
-- "does this feel like it knows me after two years" and this is the only
-- instrument that ever produces one.
CREATE TABLE IF NOT EXISTS ratings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    neuron_id    INTEGER REFERENCES neurons(id) ON DELETE SET NULL,
    surfaced_as  TEXT    NOT NULL,   -- contradiction | recurrence | loop | bridge
    verdict      INTEGER,            -- +1 useful, -1 noise, NULL unrated
    nonobvious   REAL,
    grounded     REAL,
    actionable   REAL,
    novel        REAL
);
CREATE INDEX IF NOT EXISTS ix_ratings_kind ON ratings(surfaced_as);

-- Prosodic arousal is speaker-relative and means nothing without a running
-- baseline. Seven floats per turn; not audio, and not reconstructible as audio.
CREATE TABLE IF NOT EXISTS prosody_baseline (
    speaker    TEXT NOT NULL,
    feature    TEXT NOT NULL,
    mean       REAL NOT NULL,
    var        REAL NOT NULL,
    -- REAL, not INTEGER: this is an exponentially-decayed effective count,
    -- and rounding it to an integer each update pins it at 1 forever.
    n          REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (speaker, feature)
);
