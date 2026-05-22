# Kalshi Reference Archive Note

Kalshi is a reference/process library for TradingView Bot v2, not a parallel active bot to keep expanding by default.

Patterns to port:

- canonical `feed/`, `runs/`, `logs/` separation
- replay CLI over an append-only feed DB
- immutable run artifacts with config, metrics, results DB, report
- train/validation/test discipline
- feed freshness watchdogs

Do not stop or disable any Kalshi services without explicit approval after inventorying active services, canonical DBs, row counts, and latest timestamps.
