# RF authoring contract

The physical contract between footprints, transitions and the Workflow, including
agreed behaviour not yet implemented, is in
`docs/rf-transition-contract.md`. This file covers the artifact format.
The format is changed directly when the contract changes: there are no
migrations, and an artifact of an earlier envelope or payload schema is
rejected and regenerated from its source.

The artifact file is the boundary between independently distributed librarian
and layout tools. Neither code import nor a running authoring plugin is needed
for placement. The saved exact payload is authoritative. Recipe reopening is a
separate operation that requires a known generator/schema and verifies exact
regeneration; consuming saved output must not execute authoring code.

`gppo.artifact/2.x` carries an immutable safe artifact identity, positive integer
revision, kind, payload schema, full recipe, exact payload, compatibility,
required native capabilities and provenance. `gppo.rf-recipe/3.0` fixes the
generator identity/version, all parameters, units and dependency descriptor.
Compatibility is the footprint identity and hash, mount type, mount side,
allowed netclasses and `copper_layer_count`. Stackup identity is that count:
layer names inside a recipe and payload are KiCad's literal names for it, and
nothing compares a name list. The base-part descriptor names no stackup, so a
changed layer count never forces the source to be inspected again.
Clones use a new identity/revision 1; revisions retain identity and increment
revision. Both record the parent identity, revision and complete-artifact hash.

Canonical JSON is UTF-8 with `sort_keys=True`, `separators=(',', ':')`,
`ensure_ascii=False`, `allow_nan=False`. SHA-256 hashes cover the recipe and
payload independently, and the complete envelope excluding its `hashes` member.
Compatibility/provenance changes therefore invalidate the envelope hash.
Object iteration follows explicit stackup order, so round-tripping the sorted
JSON object keys does not change generated object-list ordering. These are
integrity checks, not signatures or proof of librarian approval.

The payload `gppo.rf/2.0` fixes millimetres and `x-right-y-up-ccw`; the anchors
are signal [0,0], local exit +X and the handoff at the end of the exit trace.
Its net roles are `signal`/`ground`; layer roles identify literal mount/signal
(exit) layers. Clock is a separately recorded default pose so consumers do not
rotate local geometry twice. Objects have stable local IDs and are actual
vias, net copper circles, polygon pour keepouts and the one exit trace. Every
via carries `"fill": "copper"`; a consumer must mark it filled. Copper and
keepout objects carry a `purpose`. No embedded executable generators belong in
this payload. The required capabilities `native-tracks` and `filled-vias` make
a consumer that cannot create the trace or set the fill reject the artifact
rather than drop either.

Validation raises on the first blocking finding. Non-blocking findings are
returned beside the payload by `review_recipe` and are not saved in the
artifact; they never change the payload. A structured list of all blocking
findings is future work.

Approved base geometry remains unchanged. A circular enlargement is emitted
only above the minimum base/via land size. This preserves the distinction
between omitting an RF overlay and removing a source part's minimum copper.
A through-hole pad set to remove unused layers and keep start and end has its
land on both outer layers and on the exit layer, where the exit trace
connects; the model treats every other inner layer as the bare barrel.
No footprint mirror is created because it would not represent actual routing
vias. Project manifests, rather than library pad UUIDs or footprint fields,
remain the consumer's ownership authority.

The exit test uses complete layer-specific ground envelopes, including drilled
barrels, native lands and overlays. Floating-point contact tolerance is 1e-9 mm;
true tangency is accepted. Antipad polygon edges circumscribe the intended
circle with <=0.001 mm radial overshoot. Native consumers must preserve these
exact saved vertices rather than retessellate a differently sized opening.

The wx dialog performs read/preview/publish library actions only. It does not
create board geometry, mutate the current board, invoke Workflow, regenerate
interposer parts or silently revise placed instances. Native source inspection
uses pcbnew for actual pad geometry and data-only S-expression inspection for
the exact annular custom ground pad, because the binding's primitive
shared-pointer vector cannot be iterated reliably. A ground pad of any other
shape and unsupported signal constructions block explicitly. The ground pad's
zone connection is resolved through KiCad's own inheritance: pad, then
footprint; a chain that ends at the zone default is rejected.
