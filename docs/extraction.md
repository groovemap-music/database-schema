# History-preserving extraction

Source repository: `SimplicityGuy/discogsography`

Source branch: `main`

Source head at extraction: `204f49e2429f074546dfc67e6354be2529a983ac`

The destination was prepared in a new local clone. The original monorepo was never
rewritten or used as the filter-repo working directory.

```bash
git clone --no-local --single-branch --branch main \
  /Users/Robert/workspaces/github/SimplicityGuy/discogsography \
  /Users/Robert/workspaces/github/groovemap/database-schema

git filter-repo --force \
  --path schema-init/ \
  --path tests/schema-init/ \
  --path LICENSE \
  --path-rename schema-init/: \
  --path-rename tests/schema-init/:tests/
```

The filtered history contains 79 commits. The destination migration promotes the schema
definitions into the `groovemap_schema` package and adds the versioned compatibility
contract prepared in bead `discogsography-2kpm.2`. The one-shot runner, container build,
and its orchestration test are excluded from the current tree because live application is
owned by `deployment`; their earlier revisions remain in filtered history.
