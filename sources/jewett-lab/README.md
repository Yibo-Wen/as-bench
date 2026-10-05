# Jewett lab (published-data source)

The Jewett group (Stanford University; the work was done at Northwestern University), with
the Shukla group (University of Illinois Urbana-Champaign) and the Lucks group
(Northwestern), engineered the allosteric transcription factor PbrR into a cell-free
biosensor for lead in drinking water. Wild-type PbrR is the wrong sensor for the job: it
responds about 13-fold to zinc and only about 1.2-fold to lead at 1 µM. The campaign
screened 2,024 variants in plate-scale cell-free gene expression reactions, reading out a
fluorescent reporter against lead, against zinc, and with no added metal, and ended with a
freeze-dried diagnostic that detects lead at about 5.7 ppb. The measurements are public
under CC-BY-4.0 in the paper's Source Data:

- Paper: Wang et al., *Nature Communications* 17, 261 (2025), doi:10.1038/s41467-025-66964-6
- Earlier library: Ekas et al., *ACS Synth. Biol.* (2024), doi:10.1021/acssynbio.4c00456
- Data: the article's Source Data and Supplementary Data 1 spreadsheets

AS-Bench replays this group's published measurements; it cannot
send jobs to the group's lab. Campaigns built on this source support the replay backend
only, because every value is a recorded measurement and the study ships no surrogate model.

Campaigns here use the study's **unbiased** round-0 library — the alanine scan, the
site-saturation library, and the combinatorial variants, all screened as one plate set. The
study's later machine-learning-designed rounds are deliberately excluded: they are the
authors' own shortlist, they were run on different plates with wild-type denominators that
differ by up to threefold, and a predictor that merely knows which six positions those
designs recombine outranks any model that learns from measurements. Each campaign's
`authoring/provenance/DATA_PROVENANCE.md` records the numbers behind that decision.

## Campaigns

| Campaign | Backend | Budget | Deliverable |
|---|---|---|---|
| [biosensor-active-learning](../../tasks/biology/jewett-lab/biosensor-active-learning/README.md) | replay | 3 × 16 variants from 677 PbrR substitution variants | JS predictor ranking 332 held-out variants by lead-selective response |
