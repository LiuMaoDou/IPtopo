"""Adapter registry: simulation code never imports this package."""


def extract_routing(dialect: str, text: str, parsed: dict) -> dict:
    from ..model import RoutingFacts,Issue,SourceRef
    try:
        return _extract_routing(dialect,text,parsed)
    except (ValueError,TypeError,IndexError):
        # A malformed protocol leaf must not discard the base configuration or
        # turn a partial routing parse into a usable graph.
        return RoutingFacts(adapter_versions={dialect+'_igp':'1.0.0'},adapter_devices=[parsed['device']['id']],issues=[Issue(
            code='malformed_routing_configuration',message='协议配置存在无效值，需修正后重新导入。',device_id=parsed['device']['id'],
            source_refs=[SourceRef(source_file_id=parsed['device']['source_file_id'],line=1)])]).model_dump()


def _extract_routing(dialect: str, text: str, parsed: dict) -> dict:
    from ..model import RoutingFacts
    if dialect == "huawei_vrp":
        from .huawei import extract
        return extract(text, parsed).model_dump()
    if dialect in ("cisco_ios", "cisco_xr", "cisco_nxos"):
        from .cisco import extract
        return extract(text, parsed, dialect).model_dump()
    if dialect in ("junos_set", "junos_hierarchical"):
        from .juniper import extract
        return extract(text, parsed, dialect).model_dump()
    if dialect in ("nokia_classic", "nokia_md_flat", "nokia_md_hierarchical"):
        from .nokia import extract
        return extract(text, parsed, dialect).model_dump()
    return RoutingFacts().model_dump()
