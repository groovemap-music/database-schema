"""Measurement harness for spike gm-database-schema-gkt.1.

Throwaway. Nothing here ships, no product code imports it, and it is checked in
only so the numbers in ``../gm-database-schema-gkt.1-procedural-pathfinder.md``
can be reproduced.

Its shape is adapted from the ``investigations/`` harness on the owner's earlier
``db-alternatives`` branch, named by branch rather than by repository because
this repository's distribution contract forbids the retired project name in
published source: a hardware
calibration step, workloads declared as data with their own iteration counts, a
runner that reports p50/p95, a comparison step, a generated report, and the same
``small``/``large`` scale pair driven from one script in either a local Docker
mode or a cloud mode.

Two things differ from that harness, deliberately, and the spike document says so
rather than leaving a reader to discover it:

* Its cloud mode is Ansible against Hetzner Cloud. This one is Terraform against
  IBM Cloud VPC, which is what the bead asks for. The playbook-per-engine
  convergence loop becomes ``terraform apply`` and a remote-exec driver.
* Its workloads are engine-agnostic because every engine it measures speaks a
  graph query language. Here one engine is PostgreSQL running a PL/pgSQL
  function and the other is Neo4j running Cypher, so a workload names a *case*
  and each engine adapter supplies its own statement for it.
"""
