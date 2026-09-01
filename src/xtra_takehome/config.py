from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    ticker: str = "BZ=F"
    start: str = "2010-01-01"
    end: str = "2026-09-01"  # exclusive; fixed for reproducibility
    seed: int = 42
    horizon: int = 252
    n_paths: int = 1000
    max_acf_lag: int = 20
    output_dir: Path = Path("reports")
