# Polymarket Reference Archive Note

Polymarket is a reference/process library for TradingView Bot v2, not a parallel active bot to keep expanding by default.

Patterns to port:

- inventory-aware simulation mindset
- event-reconstructed state caveats
- fill realism and passive/next-tick discipline
- per-window strategy reports
- explicit separation between raw data, derived features, decisions, and PnL claims

Do not stop or disable any Polymarket services without explicit approval after inventorying active services, canonical DBs, row counts, and latest timestamps.
