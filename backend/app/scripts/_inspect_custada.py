"""Quick inspection of CUSTADA .xls files. Read-only; safe to run repeatedly."""
from pathlib import Path
import pandas as pd

INPUT_DIR = Path("data/unenriched custada files")


def inspect_file(path: Path) -> None:
    print(f"\n=== {path.name} ===")
    df = pd.read_excel(path, engine="xlrd", header=None)
    print(f"shape: {df.shape}")
    print("first 30 rows (raw, no header):")
    for i, row in df.head(30).iterrows():
        non_null = [(j, str(v)[:80]) for j, v in row.items() if pd.notna(v)]
        print(f"  [{i}]: {non_null}")
    # try with header=auto
    print("\n-- attempt with header=0 --")
    df2 = pd.read_excel(path, engine="xlrd")
    print(f"columns: {list(df2.columns)}")
    print(df2.head(5))


def main():
    files = sorted(INPUT_DIR.glob("*.xls"))
    print(f"Found {len(files)} files")
    for f in files[:1]:  # just first one for now
        inspect_file(f)


if __name__ == "__main__":
    main()
