# DeepMind protein design (published-data source)

Google DeepMind, with Triplebar, ran a four-round machine-learning-guided engineering
campaign on NucB, a 142-residue biofilm-degrading nuclease from *Bacillus licheniformis*.
Each round designed a variant library, expressed it, and sorted it in microfluidic
droplets against a fluorogenic DNA substrate, calling each variant's activity against
control sequences carried on the same chip. The released landscape covers 55,760 distinct
amino-acid sequences with an ordered four-level activity class, alongside raw sequencing
counts, per-round enrichment factors, library-design provenance, a homolog alignment, and
45 purified-protein confirmations.

- Paper: Thomas et al., *Cell Systems* 6, 101236 (2025), doi:10.1016/j.cels.2025.101236 (CC BY 4.0)
- Preprint: doi:10.1101/2024.03.21.585615
- Data and code: https://github.com/google-deepmind/nuclease_design, bucket `gs://nuclease_design`
  (software Apache-2.0, all other materials CC-BY-4.0)

Autonomous Science Bench replays this group's published activity calls; it cannot
send jobs to the group's lab. Campaigns built on this source support the replay backend
only.

Two properties of the released data shape how it can be used. The activity call is an
ordered class, not a continuous rate, so campaigns score classes rather than pretend each
variant has a precise activity value. And because all 55,760 labels are public, a campaign
built on them is a development replay: the agent phase runs under an egress allowlist so
the labels cannot be fetched inside the sandbox, but a genuinely secret leaderboard test
would need fresh measurements.

## Campaigns

| Campaign | Backend | Budget | Deliverable |
|---|---|---|---|
| [nuclease-active-learning](../../tasks/biology/deepmind/nuclease-active-learning/README.md) | replay | 4 × 96 designs from a 20,000-variant shared pool | JS predictor ranking 5,000 held-out variants by activity class |
