from pathlib import Path
from typing import Callable, Optional

def print_latex(path: Path, preprocess: Optional[Callable[[str], str]] = None) -> None:
    tex = path.read_text(encoding="utf-8").strip()

    if preprocess:
        tex = preprocess(tex)

    print("::: {.scroll-math}")
    print("$$")
    print(tex)
    print("$$")
    print(":::")