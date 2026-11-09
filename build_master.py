"""Build the shared master company list once (or a few times a year when DOL publishes new data).

    1. Download the latest 4 quarterly LCA disclosure files (.xlsx) from
       https://www.dol.gov/agencies/eta/foreign-labor/performance  (section "LCA Programs (H-1B, H-1B1, E-3)")
    2. Put them in a folder, e.g. lca_files/
    3. Run:  python build_master.py lca_files
       It ranks every employer that sponsored entry-level roles, finds which have public job pages,
       and writes data/companies_master.csv. Every user's personal list is built from this file.
"""
import sys
from pathlib import Path

from agent.company_lists import build_master

if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "lca_files")
    files = [str(p) for p in sorted(folder.glob("*")) if p.suffix.lower() in (".xlsx", ".csv")]
    if not files:
        sys.exit(f"No .xlsx or .csv files found in {folder}")
    print(f"Using {len(files)} files: " + ", ".join(Path(f).name for f in files))
    last = [""]

    def say(f, m):
        if m != last[0]:
            print(f"[{f:4.0%}] {m}", flush=True)
            last[0] = m

    m = build_master(files, progress=say)
    ok = m[m["ats"] != ""]
    print(f"\nMaster list: {len(m):,} companies, {len(ok):,} with a job page the agent can watch.")
    print(ok["size"].value_counts().to_string())
