import json
from pathlib import Path


LANG_CODES = ["en", "es", "zh-CN", "zh-TW", "ko", "ms", "th", "vi", "ja"]
PROTECTED_KEYS = ["HotelCode", "HotelChain", "Hotelname", "Module", "CountryCode", "Chart Formula"]


def _init_group():
    return {
        "UI labels": set(),
        "Switch labels": set(),
        "Filter labels": set(),
        "KPI labels": set(),
        "Executive Charts labels": set(),
        "Core Charts labels": set(),
    }


def _featured_by_page(page):
    if page == "CORP":
        return [
            ("Cumulative Hotel Jobs by Week (Donut Race)", "Animated cumulative weekly donut race of hotel job totals. Impact: persistent growth gap between hotels indicates long-term load imbalance. Resolution: rebalance structural staffing, budget, and support allocation based on cumulative trajectory."),
            ("Comparison: Hotel JO Volume", "Compare JO volume across hotels."),
            ("2-Axis: JO vs SLA by Hotel", "Volume bars with SLA% line overlay."),
            ("Status -> Department (Drilldown)", "Click a status slice to inspect department distribution under that status."),
            ("JO Count and Quantity Trend", "Cumulative running totals of JO count and requested quantity over time to track growth trajectory."),
            ("Semi Gauge: SLA Compliance %", "Semicircular KPI gauge for SLA compliance health."),
            ("World Map Distribution", "Global map with explicit Macau markers for WM/WP visibility near Hong Kong."),
        ]
    return [
        ("Cumulative Weekly Service Category Share (Donut Race)", "Animated cumulative weekly donut race showing long-run share shifts by service category. Impact: sustained cumulative dominance reveals structural demand pressure points. Resolution: rebalance capacity plans, inventory, and preventive actions toward categories with persistent cumulative growth."),
        ("SLA vs Jobs by week", "Week-ascending workload bars with SLA compliance line."),
        ("Closing Rate vs Jobs by week", "Week-ascending workload bars with closing rate line."),
        ("Status -> Service Category (Drilldown)", "Click a status slice to drill down into service category mix."),
    ]


def generate_inventory_and_en(root: Path):
    groups = {"Corp": _init_group(), "OPS": _init_group(), "GM": _init_group()}

    ui_base = [
        "FCS1 Job Order",
        "Executive Analytics",
        "Core Dashboard Charts",
        "Top 20 JO",
        "KPI & Chart Dictionary",
        "Loading dashboard...",
        "NOTE",
        "FORMULA",
        "Job Order",
        "Created",
        "Status",
        "Department",
        "Service Item",
        "Resolution Min",
        "SLA Breach Min",
    ]
    switch_base = ["Switch:", "Language:", "Toggle Dark/Light", "Export (PDF)", "CORP", "OPS-WM", "OPS-WP", "GM-WM", "GM-WP"]
    filter_base = ["Filters", "All Hotels", "From:", "To:", "Apply", "Reset", "1 day", "1 week", "2 weeks", "1 month", "3 months", "6 months", "1 year"]

    for grp in groups.values():
        grp["UI labels"].update(ui_base)
        grp["Switch labels"].update(switch_base)
        grp["Filter labels"].update(filter_base)
        grp["UI labels"].update(PROTECTED_KEYS)

    for chain_dir in [p for p in root.iterdir() if p.is_dir()]:
        for jp in chain_dir.glob("*.json"):
            try:
                data = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                continue
            page = (data.get("page") or "").upper()
            if page not in {"CORP", "OPS", "GM"}:
                continue
            key = "Corp" if page == "CORP" else page
            title = (data.get("title") or "").strip()
            if title:
                groups[key]["UI labels"].add(title)
            for kpi in data.get("kpis", []):
                if kpi.get("label"):
                    groups[key]["KPI labels"].add(str(kpi["label"]).strip())
                if kpi.get("sub"):
                    groups[key]["KPI labels"].add(str(kpi["sub"]).strip())
            for ch in data.get("charts", []):
                if ch.get("title"):
                    groups[key]["Core Charts labels"].add(str(ch["title"]).strip())
                if ch.get("note"):
                    groups[key]["Core Charts labels"].add(str(ch["note"]).strip())

    for page in ("CORP", "OPS", "GM"):
        key = "Corp" if page == "CORP" else page
        for title, note in _featured_by_page(page):
            groups[key]["Executive Charts labels"].add(title)
            groups[key]["Executive Charts labels"].add(note)

    inv_path = root / "en_label_inventory.md"
    lines = [
        "# EN Label Inventory",
        "",
        "Coverage: all generated Corp/OPS/GM dashboard labels grouped by UI categories.",
        "",
    ]
    ordered = []
    seen = set()

    for page in ("Corp", "OPS", "GM"):
        lines.append(f"## {page}")
        lines.append("")
        for section in ("UI labels", "Switch labels", "Filter labels", "KPI labels", "Executive Charts labels", "Core Charts labels"):
            labels = sorted([x for x in groups[page][section] if x], key=lambda s: s.lower())
            lines.append(f"### {section}")
            lines.append(f"Total labels: {len(labels)}")
            lines.append("")
            for i, label in enumerate(labels, 1):
                lines.append(f"{i}. {label}")
                if label not in seen:
                    seen.add(label)
                    ordered.append(label)
            lines.append("")
    inv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    en_map = {k: k for k in ordered}
    for k in PROTECTED_KEYS:
        en_map[k] = k
    (root / "en_lang.json").write_text(json.dumps(en_map, ensure_ascii=False, indent=2), encoding="utf-8")
    return ordered


def validate_lang_files(root: Path, required_keys):
    missing = {}
    required = set(required_keys)
    for code in LANG_CODES:
        fp = root / f"{code}_lang.json"
        if not fp.exists():
            missing[code] = ["<missing file>"]
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        have = set(data.keys())
        miss = sorted(required - have)
        if miss:
            missing[code] = miss
    if missing:
        sample = []
        for code in LANG_CODES:
            if code in missing:
                m = missing[code]
                head = ", ".join(m[:8])
                tail = "" if len(m) <= 8 else f" ... (+{len(m)-8} more)"
                sample.append(f"{code}: {head}{tail}")
        raise RuntimeError("Missing translation keys detected:\n" + "\n".join(sample))


def enforce(root: Path):
    keys = generate_inventory_and_en(root)
    validate_lang_files(root, keys)


if __name__ == "__main__":
    enforce(Path(__file__).resolve().parents[1])
    print("I18n enforcement passed")
