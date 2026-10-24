# Synthetic configurations

`demo/` contains eight independently importable configurations for the demo topology. These are artificial interface-focused examples, not production-ready device configurations. Addresses use private/documentation ranges. The sample names label devices; they are not identities used for deduplication.

The backbone contains two parallel CORE-BJ-01 ↔ CORE-SH-02 subnets, dual-stack edge transits, four shared services members, west/east branches, and an access router. BLUE and VPRN contexts remain isolated until the user explicitly maps them into a shared domain.

`fixtures/` contains additional dialect samples used by tests. [The parser support matrix](../docs/SUPPORT_MATRIX.md) describes accepted fields and limitations.
