# Gregoire group, Caltech / JCAP (published-data source)

Rohr, Stein, Guevarra, Wang, Haber, Aykol, Suram and Gregoire released the measured
datasets behind their sequential-learning benchmark: co-deposited metal-oxide composition
libraries screened for the oxygen evolution reaction on a scanning droplet cell. Each
library spans a six-cation space on a 10 at.% grid, restricted to compositions with at
most four cations present — **2,121 compositions, measured exhaustively**, with one
figure of merit per composition: the overpotential needed to reach 3 mA/cm², in volts,
where lower is better.

- Paper: Rohr et al., *Chem. Sci.* 11, 2696-2706 (2020), doi:10.1039/C9SC05999G, CC-BY-4.0
- Data: CaltechDATA `7b106-nf257`, doi:10.22002/D1.1345, **CC-BY-4.0**
  (`tri_data_share.pck`, 764,549 B)
- Benchmark code: https://github.com/SantoshSuram-TRI/ACE-I, CC-BY-4.0

Autonomous Science Bench replays this group's published measurements; it cannot
send jobs to the droplet cell. Campaigns built on this source support the replay backend
only.

Four properties of the released data shape how it can be used. Each is a measured number,
not a reading of the paper.

**The grid is exhaustive**, so no composition was chosen by anyone's model and there is
no acquisition-strategy artifact to detect.

**The libraries are separate experiments, not one chemical space.** The released file
carries five plates on the same composition grid; Table 1 of the paper names the element
system of four of them. Comparing *chemically identical* compositions measured on two
plates gives Spearman −0.439 (Mn-Fe-Co-Ni-La-Ce against Mn-Fe-Co-Cu-Sn-Ta) and −0.484
(against Ca-Mn-Co-Ni-Sn-Sb); 1,149 composition vectors appear on more than one plate with
a median absolute discrepancy of 0.0986 V against a within-library spread of 0.029-0.060
V. A model fitted across the libraries is therefore *worse* than one fitted per library,
and a campaign must treat the library as part of a design's identity.

**Labels are reliable within a library, but the extreme tail is not.** Plate 3875 agrees
with plate 3860 at Spearman 0.910 and ICC(2,1) 0.752, the best replicate agreement in
this registry. Independently of that pair, every library is spatially smooth: a
composition's overpotential against the mean of its ~14 one-step grid neighbours gives
r = 0.84-0.94, putting 6-16% of the variance in noise. But the two agreeing plates share
only 14 of their best 50 compositions (28%) against 133 of their best 212 (63%, chance
10%), so a top-2% target would largely grade noise. Campaigns on this source should set
discovery thresholds at the 5-15% level.

**Overpotentials are not comparable across libraries.** Per-library top-100 cutoffs span
0.362-0.419 V, so an absolute threshold puts nearly every "hit" in one library and
measures nothing about the screen. Campaigns on this source define their target per
library.

All measurements are public, so a campaign built on them is a development replay rather
than a secret leaderboard test.

## Campaigns

| Campaign | Backend | Budget | Deliverable |
|---|---|---|---|
| [oer-composition-screen](../../tasks/materials/gregoire-lab/oer-composition-screen/README.md) | replay | 4 × 48 compositions from a 3,419-design shared pool | JS predictor ranking 5,039 four-cation compositions that can never be ordered |
