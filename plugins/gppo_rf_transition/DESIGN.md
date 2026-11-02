# RF authoring contract

The artifact file is the boundary between independently distributed librarian
and layout tools. Neither code import nor a running authoring plugin is needed
for placement. The saved exact payload is authoritative. Recipe reopening is a
separate operation that requires a known generator/schema and verifies exact
regeneration; consuming saved output must not execute authoring code.

`gppo.artifact/1.x` carries an immutable safe artifact identity, positive integer
revision, kind, payload schema, full recipe, exact payload, compatibility,
required native capabilities and provenance. `gppo.rf-recipe/1.0` fixes the
generator identity/version, all parameters, units and dependency descriptor.
Clones use a new identity/revision 1; revisions retain identity and increment
revision. Both record the parent identity, revision and complete-artifact hash.

Canonical JSON is UTF-8 with `sort_keys=True`, `separators=(',', ':')`,
`ensure_ascii=False`, `allow_nan=False`. SHA-256 hashes cover the recipe and
payload independently, and the complete envelope excluding its `hashes` member.
Compatibility/provenance changes therefore invalidate the envelope hash.
Object iteration follows explicit stackup order, so round-tripping the sorted
JSON object keys does not change generated object-list ordering. These are
integrity checks, not signatures or proof of librarian approval.

The payload fixes millimetres and `x-right-y-up-ccw`; the anchor is signal [0,0]
and local exit is +X. Its net roles are `signal`/`ground`; layer roles identify
literal stackup mount/signal layers. Clock is a separately recorded default pose
so consumers do not rotate local geometry twice. Objects have stable local IDs
and are actual vias, net copper circles and polygon pour keepouts. No signal
tracks or embedded executable generators belong in this payload.

Approved base geometry remains unchanged. A circular enlargement is emitted
only above the minimum base/via land size. This preserves the distinction
between omitting an RF overlay and removing a source part's minimum copper.
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
the supported exact annular custom shield, because the binding's primitive
shared-pointer vector cannot be iterated reliably. Unknown shields receive
conservative bounds, while unsupported signal constructions block explicitly.
