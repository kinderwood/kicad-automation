# Experimental RF library fixtures

**Demonstration only. These artifacts are not manufacturing releases or
qualified RF/impedance designs.**

RF-L2 revision 1 exits on `In1.Cu`; RF-L3 revision 1 exits on `In2.Cu`.
Both allow CLOCK and DATA independently of routing-layer choice, matching both
example workbook A and workbook B. They pin the exact experimental
`Example:GPPO_B012_L13_01_EXPERIMENTAL` source bytes derived from the supplied
OL-B012-L1X-01 revision B drawing. The source has a plated center pin, so these
artifacts add no duplicate signal via and no ground disc, and the ground ring
runs through the whole board. They use the Designer's defaults: stacked
microvias with a 5 mil drill and 10 mil pad, marked copper plated shut, placed
in the footprint's annular ground pad on its 43.75 mil (1.11125 mm)
mid-radius, at eleven positions from 30° to 330° on each of the three layer
pairs. They also add one tuned signal-copper enlargement on the exit layer, an
antipad on each of the four layers, the exit trace from the pin to the ground
ring radius, and an exit corridor that stops at the trace end. The trace end
is the handoff where designer routing starts.

The files are the output of `examples/gppo_demo/generate_fixtures.py`
(`rf_artifacts` and `write_rf_library`), which uses the independent RF
Transition Designer's `read_base_part`, `default_recipe`, `make_artifact` and
`publish_bundle` APIs. The Designer reports one warning for them: the source's
23 mil (0.5842 mm) land on its 15 mil (0.381 mm) drilled hole leaves a 4 mil
(0.1016 mm) annular ring, under the 5 mil limit for a mechanically drilled
hole. The
microvias' 2.5 mil ring is exactly the microvia limit and does not warn.
Each includes the complete recipe, source pin, exact declarative payload,
capabilities and integrity hashes. `manifest.json` indexes the immutable files.
Publishing leaves the source footprint unchanged. That footprint's annular
ground pad is set to a solid zone connection, which the Designer requires.

`tests/test_connector_interposer_contract.py` reproduces these outputs with the
same generator function, checks
the shipped files, consumes them through Workflow, then saves/reloads actual
native boards on the selected KiCad runtimes. The fixture covers all 28 GPPOs
and the selected 800-pad interposer, A→B assignment/class changes, an explicit per-connector layer
override, native geometry/net/layer projections and no-op preservation. These
checks do not certify a completed routed board, DRC, fabrication or RF response.
