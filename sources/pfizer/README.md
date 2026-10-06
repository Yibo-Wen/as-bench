# Pfizer flow screening platform (published-data source)

Perera, Tucker, Sach and colleagues at Pfizer built an automated nanomole-scale screening
platform that injects reagent segments into a flowing carrier stream and reads product
formation by in-line LC-MS/UV, so a full condition screen costs micrograms of material.
Their released Suzuki-Miyaura dataset is a **complete factorial**: 15 substrate pairs,
each crossed with all 12 ligands x 8 bases x 4 solvents, for 5,760 wells with Pd(OAc)2
throughout.

- Paper: Perera et al., *Science* 359, 429-434 (2018), doi:10.1126/science.aap9112
- Data: Open Reaction Database `ord_dataset-68cb8b4b2b384e3d85b5b1efae58b203`,
  doi:10.5281/zenodo.4321713, **CC-BY-SA-4.0**
  (https://github.com/open-reaction-database/ord-data, mirrored on Hugging Face)

Autonomous Science Bench replays this group's published yields; it cannot send
jobs to the platform. Campaigns built on this source support the replay backend only.

Three properties of the released data shape how it can be used.

**The grid is exhaustive**, so no well was chosen by anyone's model and there is no
acquisition-strategy artifact to detect — the usual failure mode of replaying a published
active-learning campaign.

**Yields are not comparable across substrates.** 93.5% of the wells above an absolute 80%
yield belong to six easy substrate pairs, so an absolute threshold is cleared by
preferring the more reactive halide and measures nothing about conditions. Campaigns on
this source define their target per substrate pair.

**Every well was measured once.** There are no replicates anywhere in the 5,760, and the
file's second readout (mass ion count) correlates only rho = 0.297 with the UV-area
channel used here, so measurement noise is unquantified and cannot be estimated from the
data. Campaigns should score coarse membership rather than small yield differences.

Because all 5,760 yields are public — and this is the standard benchmark dataset for
reaction-yield prediction — a campaign built on them is a development replay. The agent
phase declares an egress allowlist, but a genuinely secret leaderboard test would need
fresh measurements.

**Licence note.** This is the registry's first CC-BY-SA dataset. Share-alike applies to
adapted material, so the derived tables in a campaign carry CC-BY-SA-4.0 and say so in
that campaign's `LICENSE.md`, inside an otherwise Apache-2.0 repository.

## Campaigns

| Campaign | Backend | Budget | Deliverable |
|---|---|---|---|
| [suzuki-condition-screen](../../tasks/chemistry/pfizer/suzuki-condition-screen/README.md) | replay | 4 × 48 wells from a 2,764-well shared pool | JS predictor ranking 1,844 held-out wells by per-substrate normalised yield |
