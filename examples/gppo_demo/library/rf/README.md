# Experimental RF library fixtures

**Demonstration only. These artifacts are not manufacturing releases or
qualified RF/impedance designs.**

RF-L2 revision 1 exits on `In1.Cu`; RF-L3 revision 1 exits on `In2.Cu`.
Both allow CLOCK and DATA independently of routing-layer choice, matching both
example workbook A and workbook B. They pin the exact experimental
`Example:GPPO_B012_L13_01_EXPERIMENTAL` source bytes derived from the supplied
OL-B012-L1X-01 revision B drawing. The source has a plated center pin, so these
artifacts add no duplicate signal via and no ground disc. They add eight ground
through vias marked copper plated shut, one tuned signal-copper enlargement on
the exit layer, an antipad on each of the four layers, the exit trace from the
pin to the 2.0 mm ground ring radius, and an exit corridor that stops at the
trace end. The trace end is the handoff where designer routing starts. The
ring leaves the 180° position empty: in the rectangular revision B gang the
connector behind is 150 mil away on the exit axis, and a ground via there
would land on its neighbour's exit trace.

The files are the output of `examples/gppo_demo/generate_fixtures.py`
(`rf_artifacts` and `write_rf_library`), which uses the independent RF
Transition Designer's `read_base_part`, `default_recipe`, `make_artifact` and
`publish_bundle` APIs. The Designer reports one warning for them: the source's
0.508 mm land on a 0.381 mm hole leaves a 0.0635 mm annular ring, under 5 mil.
Each includes the complete recipe, source pin, exact declarative payload,
capabilities and integrity hashes. `manifest.json` indexes the immutable files.
The source central footprint is unchanged.

`tests/test_connector_interposer_contract.py` reproduces these outputs with the
same generator function, checks
the shipped files, consumes them through Workflow, then saves/reloads actual
native boards on the selected KiCad runtimes. The fixture covers all 28 GPPOs
and the selected 800-pad interposer, A→B assignment/class changes, an explicit per-connector layer
override, native geometry/net/layer projections and no-op preservation. These
checks do not certify a completed routed board, DRC, fabrication or RF response.
