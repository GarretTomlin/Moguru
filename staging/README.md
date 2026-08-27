# staging/ — Phase B drafts awaiting approval (spec §5.2)

The "self-evolving" layer may **draft** new `SKILL.md` files or tool stubs,
but generated artifacts land **here** — never in `plugins/` or `skills/` —
and require explicit human approval before mounting.

Approval flow:

1. A generated draft appears here (`staging/<name>/…` + `manifest.json`).
2. A human reviews it.
3. On approval: `mv staging/<name> plugins/<name>` (or `skills/<name>`) and
   restart — the registry picks it up.
4. On rejection: delete the draft. Nothing outside staging/ ever changed.

Rails (enforced by the registry, not by convention):
- A plugin with `provides_ground_truth: false` may never shadow a
  dictionary/parser tool.
- Generated skills cannot alter the Phase-0 principles.
- Version and changelog everything.

*(Empty by design — Phase B authoring is not active yet.)*
