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

Never point migration verification at production. For a local isolated test,
create a disposable PostgreSQL database, apply the SQL twice, and run the
PostgreSQL acceptance tests described in the repository's task report.
