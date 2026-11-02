# Experimental RF library fixtures

**Demonstration only. These artifacts are not manufacturing releases or
qualified RF/impedance designs.**

RF-L2 revision 1 hands off on `In1.Cu`; RF-L3 revision 1 hands off on `In2.Cu`.
Both allow CLOCK and DATA independently of routing-layer choice, matching both
example workbook A and workbook B. They pin the exact experimental
`Example:GPPO_B012_L13_01_EXPERIMENTAL` source bytes derived from the supplied
OL-B012-L1X-01 revision B drawing. The source has a plated center pin, so these
artifacts add no duplicate signal via. They add nine ground through vias,
one tuned signal-copper enlargement, four antipads and an exit corridor.
No signal trace is generated.

The files are actual outputs from the independent RF Transition Designer's
`read_base_part`, `default_recipe`, `make_artifact` and `publish_bundle` APIs.
Each includes the complete recipe, source pin, exact declarative payload,
capabilities and integrity hashes. `manifest.json` indexes the immutable files.
The source central footprint is unchanged.

`tests/test_gppo_contract.py` reproduces these outputs, checks the shipped
files, consumes them through Workflow, then saves/reloads actual native boards
on the selected KiCad runtimes. The fixture covers all 12 GPPOs and the selected
800-pad interposer, A→B assignment/class changes, an explicit per-connector layer
override, native geometry/net/layer projections and no-op preservation. These
checks do not certify a completed routed board, DRC, fabrication or RF response.
