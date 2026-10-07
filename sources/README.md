# Published-data source registry

A **source** is a research group whose published measurements back a campaign. It has
a card (`<id>/source.toml`) and a `README.md`, and its campaigns live under
`tasks/<domain>/<source>/<campaign>/` with `[metadata] source = "<id>"`. A source
replays its data (or serves a clearly labeled twin). Its card declares which backends
exist and must name at least one dataset with a DOI and license.

| Source | Domain | Fields | Backends | Campaigns |
|---|---|---|---|---|
| [deepmind](deepmind/README.md) | biology | protein-engineering, directed-evolution | replay | 1 |
| [gregoire-lab](gregoire-lab/README.md) | materials | electrocatalysis, high-throughput-experimentation | replay | 1 |
| [kusne-lab](kusne-lab/README.md) | materials | magnetic-materials, combinatorial-thin-films | replay | 1 |
| [jewett-lab](jewett-lab/README.md) | biology | cell-free-expression, biosensor-engineering | replay | 1 |
| [pfizer](pfizer/README.md) | chemistry | reaction-screening, high-throughput-experimentation | replay | 1 |
| [sargent-lab](sargent-lab/README.md) | chemistry | electrocatalysis, co2-reduction | replay, twin | 1 |

## Adding a source

1. Create `sources/<id>/source.toml` using `sargent-lab/source.toml` as the template. The `id` is lowercase kebab-case and matches the directory name.
2. List the publication(s) and dataset(s) with DOI and license. Check that the license allows redistribution of derived tables, and add the attribution to each campaign's `LICENSE.md`.
3. Set `[backends] replay` (and `twin` if a surrogate exists). Leave `live` off: the live backend is not implemented yet.
4. Add a row to the table above.
