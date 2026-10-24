# IPtopo workbench design

Primary screen concept: `frontend/design/concept.png` (1536 × 1024), generated with the built-in Image Gen tool before implementation. Brief: complete Chinese network-engineer topology editor; white surfaces, navy text, teal actions, dotted graph canvas, inventory at left, evidence inspector at right, diagnostics below. UI remains code-native.

Tokens: white #fff, canvas #f8fafb, ink #172b3a, muted #718091, teal #087f80, border #e4e9ed; 6px corner radius, no gradients, subtle toolbar shadows only. System sans Chinese typography, 13px controls and 12px metadata, 22px logo. Lucide outline icons 16–20px, 1.7px stroke. Header 64px; toolbar 54px; sidebar 250px; inspector 300px; collapsible diagnostics; full remaining height canvas. Mobile stacks panels behind explicit tabs and retains all controls.

Component families: header and toolbar, tab strip, list rows, compact property table, source code lines, modal forms, bordered buttons, graph device and subnet nodes. App composes Graph, Inspector, Inventory, Dialogs and Diagnostics; API mutations are serialized and refresh version, large graph algorithms are lazy loaded. Allowed copy is functional Chinese text from the plan and concept: IPtopo, 设备视图, 网段视图, 地址域, 自动布局, 保存视图, 导入配置, 导出, 设备, 文件, 质量报告, 修改记录, 设备详情, 接口, 来源, 修正. Required workflow controls extend the same design.

Intentional semantic deviations from generated concept: no operational `up` badge from configuration alone; show configured/enabled/unknown state. No invented management IP or role. Actual imported device names, counts, dates and evidence replace illustrative mockup content. Additional controls for required workflows remain in contextual menus and dialogs. No raster image is shipped as interactive UI.

## Render verification — 2026-10-24

The Browser Use plugin could not reach this host’s localhost (ERR_CONNECTION_REFUSED), so browser validation used bundled Playwright with installed Chrome against the real FastAPI server at `http://127.0.0.1:8765`. Compared the generated concept and final screenshot with `view_image`, including concept-native 1536 × 1024, tablet 900 × 800, and mobile 390 × 844.

| Comparison | Result |
| --- | --- |
| Overall composition | Preserved white header, two toolbar bands, inventory/canvas/inspector columns, collapsible diagnostics and footer. |
| Palette and surfaces | True white panels, cool dotted canvas, navy ink, teal primary/selection, thin blue-gray borders; no added gradients. |
| Typography | Explicit system-font sizes on app chrome, inputs, tables, forms and graph labels. Increased graph labels/node size after render review. |
| Functional copy | Concept action labels retained; added only plan-required controls. Actual imports replace illustration names/counts. Configuration state stays separate from unknown runtime state. |
| Icons and node treatment | Consistent thin outline UI icons, vendor-symbol nodes and rectangular shared segments; selection has a teal border. |
| Container geometry | Flat rails and compact lists, no decorative card grid. Inspector body scrolls independently with pinned action footer. |
| Responsive behavior | Mobile uses three explicit panels for devices/topology/details and scrollable toolbar. All tested widths had zero document overflow; edit form and inspector remain usable. |

Corrections made from browser evidence: replaced ELK bundled fake-worker initialization with the native worker protocol (all three algorithms now run); corrected saved-pan behavior on major width changes; increased graph text sizing; synchronized pin toggles before subsequent view settings changes. Native group-by-vendor and label visibility were exercised. Pin preservation during ELK layout passed; hiding/showing prefix labels passed. ELK layered/radial/mrtree, fCoSE, circle and grid all passed the browser interaction loop without app console/page errors. Build and TypeScript checks passed.

Intentional differences from the illustration: real eight-device dual-stack sample has more edges than the six-device concept; layout positions follow the selected algorithm and persisted view. Endpoint interface labels appear when an edge is selected to keep dense dual-stack graphs readable. The inspector uses vertical interface sections to fit all required address-role and source information. Mobile whole-network overview can be zoomed or inspected through the device list. Above-the-fold copy diff contained only these real-data substitutions and required workflow controls. No remaining material styling mismatch was observed; the implementation was faithfully verified against the primary-screen design with these documented functional differences.

Screenshots were kept outside the repository in the task visualization directory as `iptopo-desktop-final.png`, `iptopo-tablet-final.png`, and `iptopo-mobile-final.png`. The final workbench screenshot is also included as `docs/workbench.png`; the reproducible scale browser script is `frontend/tests/browser-scale.cjs`. Other temporary QA scripts/screenshots remain outside the repository.


## User-directed node and scale refinements

The final node design follows the later user request: no vendor word on a topology device; a distinct inline vector identifier for Huawei, Cisco, Juniper, Nokia or other vendors, with the hostname centered below. These are original vector identifiers inspired by recognizable vendor shapes, not downloaded brand artwork. A separate vendor legend explains the symbols. Link status retains its own legend. draw.io uses matching identifiers and editable labels/legend.

For large graphs, low zoom reduces devices to vendor-colored markers and suppresses overlapping labels; zooming or selecting restores detail. Existing geometry and pins are kept when new devices arrive. A quick connected-component placement gives an immediate overview, with cancellable worker layouts and one-hop focus for detailed work. Device, file and context lists use bounded visible rows. PNG exports have a bounded raster size. These are functional refinements requested after the original concept, and deliberately change its node cards.

Visual QA found that ELK radial can place neighbors close together on one four-node star. The tested local fCoSE layout separates that neighborhood more clearly; arbitrary topology readability is not guaranteed by an automatic layout. The quick overview and local-layout choices remain available.

Subnet nodes use a compact network icon (44×44) with the full prefix below it by default, following the device-name label behavior and visibility setting. Clicking opens the details panel; deselecting keeps the address visible. Overview mode uses 28×28 icons and suppresses labels until selection, like device nodes. The node legend identifies the IP subnet symbol. Static draw.io exports retain labeled rectangles so addresses remain visible outside the interactive app.

All device icons now have a persistent rounded frame and light background. Each frame matches its vendor icon color, including in draw.io exports. Selection changes the background to teal and enlarges the frame/icon by about 14 percent, with the same border; deselection restores the normal size. Large-network overview markers retain a compact frame and a proportional selection size. draw.io includes the normal device frame.

Parallel links between the same two visible devices collapse into a thicker edge with a link count. A single click opens the member list; a double-click expands the individual Bezier edges, and another double-click on a member collapses the pair. Selecting an individual member from the inspector expands its pair automatically. Bundling runs after filters, leaves subnet membership edges independent, preserves mixed review states, and never changes persisted link facts or device coordinates. Expansion is temporary for the current graph session. PNG reflects the current canvas; JSON/CSV/draw.io retain the individual underlying links.
