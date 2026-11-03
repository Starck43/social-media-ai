# P13-T02: personal data encrypted at rest, searchable by blind index, safe in logs.

Registry: [ROADMAP](../ROADMAP.md); card: [P13-T02](tasks/p13-t02.md);
owner decision D8: [Q-18](tasks/q-18.md).

# --- What this protects and what it does not ---

Personal values (email, phone, username) are stored as `pii:v1:` Fernet
ciphertext under `PII_ENCRYPTION_KEYS`; a separate HMAC blind index under
`PII_INDEX_KEY` makes exact lookup and deduplication possible without storing
plaintext or a reversible index. `PIIRedactionFilter` can redact recognizable email and phone patterns from log messages and tracebacks; do not log personal data or secrets in the first place.

Not protected today:

- **Partial and fuzzy search.** You can find a contact by its exact normalized
  email or phone; you cannot `LIKE '%anna%'`, sort by, or range-search a
  personal field. If a module needs that, it stores a separate non-personal key
  (for example a domain or a company name), never a weakened encryption.
- **Volumetric analysis.** Two rows with the same email have the same index, and
  an attacker with database access can group by it. That is inherent to exact
  matching; `PII_INDEX_KEY` is what makes it useless outside this database.
- **Memory, backups and replicas.** Decrypted values exist in process memory and
  in any backup you take; encryption at rest does not cover those.
- **Existing credential tables.** `user_credentials` and
  `workspace_connection_secrets` stay under `CREDENTIALS_KEY` (owned by
  P12-T14). D8 forbids mixing the two key families.

# --- Keys (D8) ---

| Setting | Format | Role |
| --- | --- | --- |
| `PII_ENCRYPTION_KEYS` | comma-separated Fernet keys, **first = active** | encrypt now, decrypt all generations |
| `PII_INDEX_KEY` | urlsafe-base64, ≥ 32 bytes | HMAC-SHA256 key for blind indexes; **kept stable** |

Both are marked sensitive in the settings views and never appear in `repr` or
configuration dumps. The index key, each encryption key (including retired keys), and
`CREDENTIALS_KEY` must contain distinct key material; reuse is refused at runtime.
This prevents one leaked key from opening both the credential vault and personal records.

Missing, invalid or non-matching keys fail closed with `PIIError` — there is no
plaintext fallback, and error messages never echo a value or a key.

Generate keys:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"      # per encryption key
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"        # PII_INDEX_KEY
```

# --- Registering columns in a business module ---

```python
from app.models.types.encrypted import EncryptedText, INDEX_COLUMN_LENGTH, set_pii_pair

email_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
email_index: Mapped[str | None] = mapped_column(String(INDEX_COLUMN_LENGTH), index=True)

set_pii_pair(contact, "email", raw_email)   # writes both columns from one value
```

Rules:

- Always write the pair through `set_pii_pair`; assigning `foo_index` by hand from
  something other than the plaintext is how a row becomes unsearchable.
- Query by `Contact.objects.filter(email_index=index_for("email", raw))`; the
  tenant scope still applies on top — the index does not weaken tenant isolation.
- Never put ciphertext or the index in API responses, admin lists or `repr`.
- A module owning encrypted columns adds its table/column to
  `REGISTRY` in `cli/commands/pii.py`; nothing is auto-discovered.
- The table carries an integer marker column (`ROTATION_MARKER_COLUMN`, default
  `pii_updated_at`) and **every writer bumps it** on insert and update. Rotation
  refuses a table without it: a ciphertext comparison alone cannot tell a row
  whose value was rewritten during the run from one that was merely skipped.
- Kinds are closed (`email`, `phone`, `username`). Adding one means adding a
  normalizer and its tests, not a free-form string.

# --- Rotation ---

Encryption keys rotate; the index does not.

1. Prepend the new key: `PII_ENCRYPTION_KEYS=<new>,<old>`; restart the runtime.
   New writes use `<new>`, existing rows still decrypt with `<old>`.
2. `python -m cli.main pii rotate` (dry run: lists registered tables), then
   `python -m cli.main pii rotate --yes [--table T] [--limit N]`. Rows already on
   the active key are skipped, so a rerun is idempotent and `--limit` is resumed
   by simply running again.
3. Only after every writer uses the new active key, rerun the command and verify
   zero rewrites and zero conflicts across every registered column. After a backup
   and independent verification, remove `<old>` and restart. The dry run only
   lists targets; it does not prove all rows have been rotated.

Rotation uses a conditional update against the ciphertext **and** the marker it
read. A concurrent write either changes both or is counted as a conflict, so the
command is never fooled by a rewrite that stored byte-identical ciphertext; a
conflict makes it exit non-zero, and operators must rerun and verify before
retiring any key. The guarantee is: no write is overwritten, and every write that
landed during the run is reported. It is not a row lock - a writer that neither
changes the value nor bumps the marker is out of contract, which is why the marker
is mandatory. Earlier committed
batches remain committed. This does not protect against a stale writer later
using the old active key; update or stop all old-key writers before rotation.
Empty-string sentinels are skipped. The guard is verified on PostgreSQL by
`python scripts/ci/pii_rotation_pg.py` (disposable schema, two connections: clean
run, rerun after retirement, different-value and identical-ciphertext races, and
a table without the marker refused). Run it after changing the command.

`PII_INDEX_KEY` stays unchanged. Rotating it would change every index value and
requires an approved rebuild of every indexed column; the command refuses index
columns on purpose.

Keep the previous key until step 2 has run on every environment. Losing all keys
means the data is unrecoverable — store keys in the same secret store as
`CREDENTIALS_KEY`, but as separate secrets.

# --- Logs ---

Attach `PIIRedactionFilter()` to each output handler used by business adapters.
It formats the message before replacing recognizable emails and phone-shaped
runs, and sanitizes formatted exception/stack text. It does not sanitize arbitrary
structured fields, usernames, secrets, or all possible personal-data formats;
never log those values. Redaction is defense in depth, not an authorization or
complete data-loss-prevention boundary. Filtering one handler does not protect
other handlers; configure every output sink. No business adapters are enabled by
this task.
