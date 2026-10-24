# Dependencies and integration boundaries

Actual Python versions were read from installed distribution metadata. Full resolution is in `requirements.lock`. License text in each package governs; this table is a discovery index, not a replacement for those notices. No third-party source patches are applied.

| Package | Version | Declared license | Upstream |
| --- | --- | --- | --- |
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.15.1 | MIT | https://anyio.readthedocs.io/en/latest/ |
| certifi | 2026.10.23 | MPL-2.0 | https://github.com/certifi/python-certifi |
| charset-normalizer | 3.5.2 | MIT | https://github.com/jawah/charset_normalizer/blob/master/CHANGELOG.md |
| click | 8.5.0 | BSD-3-Clause | https://click.palletsprojects.com/page/changes/ |
| fastapi | 0.162.0 | MIT | https://github.com/fastapi/fastapi |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httpcore | 1.0.9 | BSD-3-Clause | https://www.encode.io/httpcore |
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx/blob/master/CHANGELOG.md |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna/blob/master/HISTORY.md |
| iniconfig | 2.3.1 | MIT | https://github.com/pytest-dev/iniconfig |
| n2g | 0.3.3 | MIT | https://github.com/dmulyalin/N2G |
| netutils | 1.19.2 | Apache-2.0 | https://netutils.readthedocs.io |
| opentelemetry-api | 1.45.1 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://packaging.pypa.io/ |
| pluggy | 1.7.0 | MIT | https://github.com/pytest-dev/pluggy |
| pydantic | 2.14.1 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.50.1 | MIT | https://github.com/pydantic/pydantic |
| Pygments | 2.21.0 | BSD-2-Clause | https://pygments.org |
| pytest | 9.1.1 | MIT | https://docs.pytest.org/en/stable/changelog.html |
| python-multipart | 0.0.32 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| SQLAlchemy | 2.1.6 | MIT | https://www.sqlalchemy.org |
| starlette | 1.9.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| ttp | 0.10.1 | MIT | https://ttp.readthedocs.io/ |
| ttp_templates | 0.6.18 | MIT | https://dmulyalin.github.io/ttp_templates/ |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions/issues |
| uvicorn | 0.54.0 | BSD-3-Clause | https://uvicorn.dev/release-notes |

## Frontend

Exact direct and transitive versions and license identifiers are in `frontend/package-lock.json`; build output preserves upstream license comments. The Cytoscape ecosystem and React/Vite use permissive licenses. ELKjs is EPL-2.0 and remains a distinct bundled worker dependency; its source is available at https://github.com/kieler/elkjs. See the generated `frontend-dependencies.json` for installed dependency metadata.

## Boundaries

- TTP and ttp_templates are used by source-aware adapters; standard templates are cross-checked and unsupported semantics produce diagnostics.
- netutils normalizes verified Cisco interface name forms; the original name remains.
- N2G creates editable draw.io XML with explicit node coordinates and parallel edge identities.
- Batfish and ttp_sros_parser were evaluated as optional candidates in the plan but are not runtime dependencies and no capability claim is made for their untested APIs. Nokia uses a context-aware app adapter.
- Cytoscape owns the canvas, edgehandles provides connection gestures, undo-redo stores geometry operations, fCoSE handles force layout, ELK runs in a cancellable Worker.
