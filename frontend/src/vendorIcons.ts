/** Original vector identifiers, not downloaded vendor artwork. */
const svg = (body: string) =>
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">${body}</svg>`,
  );
export const vendors = [
  {
    key: "Huawei",
    name: "Huawei",
    color: "#d94b4b",
    icon: svg(
      '<g fill="#d94b4b"><ellipse cx="32" cy="22" rx="5" ry="18"/><ellipse cx="21" cy="25" rx="5" ry="18" transform="rotate(-26 21 25)"/><ellipse cx="43" cy="25" rx="5" ry="18" transform="rotate(26 43 25)"/><ellipse cx="13" cy="34" rx="5" ry="17" transform="rotate(-52 13 34)"/><ellipse cx="51" cy="34" rx="5" ry="17" transform="rotate(52 51 34)"/><path d="M6 47h23L13 56H6zm52 0H35l16 9h7z"/></g>',
    ),
  },
  {
    key: "Cisco",
    name: "Cisco",
    color: "#2585b5",
    icon: svg(
      '<g stroke="#2585b5" stroke-width="5" stroke-linecap="round"><path d="M7 29v10m8-18v23m9-30v35m8-42v44m8-37v35m9-28v23m8-15v10"/></g>',
    ),
  },
  {
    key: "Juniper",
    name: "Juniper",
    color: "#3b9d72",
    icon: svg(
      '<g fill="none" stroke="#3b9d72" stroke-width="4" stroke-linecap="round"><path d="M32 56V12M32 44C14 44 9 35 9 26m23 9c16 0 23-11 23-21M32 25c-9 0-15-8-15-16"/></g><g fill="#3b9d72"><circle cx="9" cy="22" r="5"/><circle cx="17" cy="7" r="5"/><circle cx="55" cy="10" r="5"/><circle cx="32" cy="8" r="5"/></g>',
    ),
  },
  {
    key: "Nokia",
    name: "Nokia",
    color: "#4659b9",
    icon: svg(
      '<path d="M10 52V12h10l24 28V12h10v40H44L20 24v28z" fill="#4659b9"/>',
    ),
  },
  {
    key: "Other",
    name: "其他厂商",
    color: "#718797",
    icon: svg(
      '<g fill="none" stroke="#718797" stroke-width="3"><ellipse cx="32" cy="18" rx="23" ry="10"/><path d="M9 18v25c0 14 46 14 46 0V18M20 14l24 8m0-8-24 8"/></g>',
    ),
  },
];
export function vendorStyle(vendor: unknown) {
  const value = String(vendor || "").toLowerCase();
  return (
    vendors.find(
      (v) => v.key !== "Other" && value.includes(v.key.toLowerCase()),
    ) || vendors[4]
  );
}
