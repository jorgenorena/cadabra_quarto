import re
from pathlib import Path


def fix_cadabra_latex(tex: str) -> str:
    """
    Clean LaTeX produced by Cadabra.

    Fixes:
    - removes \\discretionary{}{}{}
    - converts index separators like '\\,_', '\\,^', ' _', ' ^'
      into '{}_' and '{}^'
    - preserves normal TeX commands and spacing as much as possible
    """

    # 1) Remove Cadabra/TeX discretionary breaks entirely
    tex = re.sub(r'\\discretionary\s*\{\}\s*\{\}\s*\{\}', '', tex)

    # 2) Replace explicit thin-space separators before indices
    #    e.g. T^{b}\,_{c} -> T^{b}{}_{c}
    #         \Gamma^{b}\,^{d} -> \Gamma^{b}{}^{d}
    tex = re.sub(r'\\,\s*(?=[_^])', r'{}', tex)

    # 3) Replace plain whitespace before indices
    #    e.g. T^{b} _{c} -> T^{b}{}_{c}
    #         \Gamma^{b} ^{d} -> \Gamma^{b}{}^{d}
    tex = re.sub(r'(?<=[}\w])\s+(?=[_^])', r'{}', tex)

    # 4) Collapse accidental repeated empty groups
    #    e.g. {}{}_{a} -> {}_{a}
    tex = re.sub(r'(?:\{\}){2,}', r'{}', tex)

    # 5) Optional small cleanup of excessive spaces
    tex = re.sub(r'[ \t]+', ' ', tex)

    return tex.strip()


def fix_cadabra_file(input_file: str | Path, output_file: str | Path | None = None) -> Path:
    """
    Read a LaTeX file, clean it, and write the result.
    If output_file is None, overwrite the input file.
    """
    input_path = Path(input_file)
    output_path = Path(output_file) if output_file is not None else input_path

    tex = input_path.read_text(encoding="utf-8")
    fixed = fix_cadabra_latex(tex)
    output_path.write_text(fixed, encoding="utf-8")

    return output_path