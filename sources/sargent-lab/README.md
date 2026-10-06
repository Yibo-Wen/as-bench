# Sargent lab (published-data source)

The Sargent group (Northwestern University), with the Sinton and Hattrick-Simpers groups
(University of Toronto), screened multi-element alloy electrocatalysts for CO2
electroreduction to C3 hydrocarbons. The platform combines robotic powder synthesis,
parallel 1 cm² membrane-electrode-assembly testing at 100, 200, and 300 mA cm⁻², and
automated gas chromatography. Their 300-composition dataset spans 15 metals and is
public under CC-BY-4.0:

- Paper: Kim et al., *Joule* 9, 102213 (2025), doi:10.1016/j.joule.2025.102213
- Data: doi:10.5281/zenodo.15107045

Autonomous Science Bench replays this group's published measurements; it
cannot send jobs to the group's lab. Campaigns built on this source support the replay
and twin backends. The twin is the study's own virtual screening space: measured values
where the study measured, its surrogate model elsewhere, always labelled as simulated.

## Campaigns

| Campaign | Backend | Budget | Deliverable |
|---|---|---|---|
| [propylene-active-learning](../../tasks/chemistry/sargent-lab/propylene-active-learning/README.md) | twin | 3 × 20 designs from 736 dilute Cu alloys | JS predictor ranking 244 held-out designs by `j_propylene` |
