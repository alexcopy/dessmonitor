# Adaptive decision journal migration

Apply `001_adaptive_decision_journal.sql` manually to a disposable or approved
target database before enabling `ADAPTIVE_OUTPUT_LEARNING_ENABLED`. The worker
never applies schema changes on startup. The migration is repeatable for the
declared journal schema and aborts transactionally if an existing table has
incompatible columns, types, defaults, nullability, primary key, hardware-write
guard, or evaluation index.

The migration role needs `CREATE` on the target schema when the table is new.
For an existing table, it also needs ownership or equivalent rights to inspect
and add the hardware-write guard and evaluation index. It must be able to create
the table and index and alter the table. The runtime role needs `SELECT` on
`inverter_metrics`, `SELECT`/`INSERT`/`DELETE` on `adaptive_decision_journal`,
and permission to call PostgreSQL advisory transaction locks. `DELETE` is used
for the journal's 30-day retention.

The `hardware_write_attempted = FALSE` check protects against mistakes made by
the application role. This table is an operational journal, not immutable or
tamper-proof audit storage: the table owner, a database administrator, or a
superuser can update or delete rows, change the schema, or remove the check.
Use a separately controlled append-only audit destination if tamper evidence
is required.

Never point migration verification at production or at an existing shared
database. PostgreSQL tests have no default DSN; run them only through the
provisioner, which creates and afterwards removes a new uniquely named container
and issues a per-run capability (private token and password in a 0600 file; the
DSN in the environment carries no password):

    EASUN_TEST_POSTGRES=1 JOURNAL_V2_LOCAL_POSTGRES=1 ... \
        python tests/disposable_postgres.py run -- python -m unittest discover -s tests

`tests/pg_isolation.py` refuses, before connecting, any target, connection option or
process it cannot prove belongs to that run, and re-verifies server identity, the
ownership record and the absence of competing sessions on every connection (see its
docstring for the enforced threat boundary). Docker is required.

## 002 — Adaptive Decision Journal v2 (additive)

`002_adaptive_decision_journal_v2.sql` adds the v2 tables, a control row in mode
`v1`, and a guard trigger that rejects INSERT/UPDATE on `adaptive_decision_journal`
only while the mode is `v2`. Applying it changes no behavior; it is repeatable and
never alters or deletes v1 rows. Activate v2 only with
`scripts/adaptive_journal_v2.py cutover --confirm`, and return with `rollback --confirm`.
`002_adaptive_decision_journal_v2_down.sql` removes the v2 objects and refuses while
v2 is active. The worker role needs `SELECT/INSERT/UPDATE/DELETE` on the v2 tables, and
the web role needs `SELECT`. See `app/control/ADAPTIVE_DECISION_JOURNAL_V2_DESIGN.md`
for the full procedure.
