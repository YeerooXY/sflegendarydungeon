# Learned coefficients

These engine-0.4.0 policies were trained separately for budgets 0 and 500 under
`profiles/synthetic.profile`, using 64 generations, 64 candidates and 64 events
per candidate; training seed 76543. See the [pilot](../../docs/EVENT_PILOT.md) for
training/evaluation separation, artifact hashes and limitations.

The files contain linear action-score coefficients, not simulation outcomes or
hidden future state. Use `sfld event --policy PATH --allow-assumptions` with the
matching budget. They are examples and are not guaranteed optimal.
