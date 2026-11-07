# Regression tooling improvement follow-up — 2026-11-07

Documentation-only tracker for our own AI-EDA workflow. Local implementation and build evidence are not automatically published by this PR.

## P1: trustworthy per-scope acceptance

- [ ] Replace the historical planner's constant fully_completed=0 with an explicit non-measurement marker or computed, documented acceptance definition. Do not relabel it as an observed failure count.
- [ ] Report functional, code coverage, UVM, SVA, Minisim support and independent crosscheck separately.
- [ ] Distinguish attempted, timeout, resource_limited, unsupported, no_test and waiver. A stopped container's running checkpoint is not an active job.
- [ ] Preserve historical source/tool revisions and results when adding later measurements.

Acceptance: fixture reports cannot promote empty denominators, missing XML, pending obligations, vacuous samples or partial coverage to completed.

## P1: durable coverage checkpoint and controlled resume

- [ ] Persist a completed VIP row via flush/fsync and atomic replacement; retain previous rows and reject mismatched source/scope.
- [ ] Exercise interruption before completion, after a completed row and during replacement.
- [ ] Normalize every incoming parallel-build flag, log effective argv, and verify generated make really uses one job in a low-memory profile.
- [ ] Check host/WSL resources and other jobs before a bounded single-target attempt; never restart the whole environment or unrelated containers.
- [ ] Keep compile SIGKILL distinct from a functional mismatch and from demonstrated OOM-killer evidence.

Acceptance: a real completed single VIP survives process interruption with an intact checkpoint; rejected/partial attempts cannot fabricate completed rows.

## P1: coverage convergence evidence

- [ ] Require identical DUT raw-point keys and denominators, pinned source hashes, fixed seeds and positive functional checks before merging.
- [ ] Record added bins per seed, plateau, remaining holes and targeted supplements.
- [ ] Reject changed denominators, missing DUT sources, basename collisions, empty samples and changed source hashes.
- [ ] Keep mutants and failed runs outside normal coverage; verify scoreboard rejection using an actual DUT mutant.
- [ ] Retain structural/tool-attribution gaps without deleting the denominator or auto-waiving.

Local evidence pointers (not uploaded artifacts): build/portfolio-extended-20261101/closure-ledger-audit-20261107-0914.json checked nine linked report SHA values; i2c-ack-setup-20261107 contains four fixed seeds / 388 checks, but only 38/2080 toggle points. These are external Verilator measurements, not Minisim acceptance. The original I2C master-SDA observer scheduling failure is retained; a full resolved-bus observer and its negative tests remain pending.

Follow-up priority: resolved-bus monitor negative cases; generated-code counter attribution; checkpoint interruption integration; supported-tool/resource-controlled retests. Each implementation PR must identify its exact measured scope, tests and remaining limitations.
