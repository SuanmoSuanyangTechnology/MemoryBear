#!/usr/bin/env python3
"""
Font Subsetting Script for MemoryBear
======================================

This script extracts all characters used in the project (from i18n files and source code)
and creates subsetted font files that only contain those characters.

Usage:
    python scripts/subset_fonts.py

Requirements:
    pip install fonttools brotli

Output:
    Subsetted font files in src/assets/font/MiSans/subset/
"""

import os
import re
import subprocess
from pathlib import Path
from collections import Counter
import json

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
FONT_DIR = PROJECT_ROOT / "src/assets/font/MiSans"
OUTPUT_DIR = FONT_DIR / "subset"

# Fonts to subset (only the ones we actually use)
FONTS_TO_SUBSET = [
    "MiSans-Bold.woff2",
    "MiSans-Demibold.woff2",
    "MiSans-Semibold.woff2",
    "MiSans-Heavy.woff2",
]

# Common Chinese characters that might not be in i18n files
COMMON_CN_CHARS = """
的一是不了在人有我他这个们中来上大为和国地到以说时要就出会可也你对生能而子那得于着下自之年过发把好心理想家天起心动已成全多开创本正业进阶技持整改步规法算设证认清务决执准保产争亲交位任引计录让变存做作使像回果注海点思内因特外直情才必少真再三二从向次通任等比各开长知本样制正新式法此行化开才全理代才等月会会区原明根花更及真员九书交管县七府区正其立世头正南根月来重部基口公方业无家量物必叫专通及约林清少达公水研合给任通即象近重油布争管研形制力论识志论者区行求世行开题前认技第办计层命好学过系江南劳克派话交决前周拉之才先列七来车象六管识半史治文界办今计正铁该毛达林元工产育局听律感走义全那把划市参专科便必术名取元任儿照办切作按工取复正办号断精存由打备太毛技条场办律业地才据程分团求门里委青收改引管相使七白术展义基先况元属般司织运义更争往七克织观系采识十道身元
"""

def extract_characters_from_ts_file(file_path):
    """Extract Chinese characters from TypeScript/JavaScript files."""
    chars = set()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Extract string literals (single and double quotes, template literals)
            # Match t('...'), t("..."), t(`...`), and regular strings
            patterns = [
                r't\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\s*\)',  # i18n t() calls
                r'[\'"`]([^\'"`\n]+)[\'"`]',  # regular string literals
            ]
            for pattern in patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    # Extract Chinese characters, numbers, and common symbols
                    for char in match:
                        if '\u4e00' <= char <= '\u9fff' or char.isalnum() or char in '，。！？、：；（）「」『』【】—…':
                            chars.add(char)
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}")
    return chars

def extract_characters_from_all_sources():
    """Extract all characters from source files."""
    all_chars = set()
    
    # Extract from i18n files
    print("Extracting characters from i18n files...")
    i18n_dir = SRC_DIR / "i18n"
    for ts_file in i18n_dir.rglob("*.ts"):
        chars = extract_characters_from_ts_file(ts_file)
        all_chars.update(chars)
        print(f"  {ts_file.relative_to(SRC_DIR)}: {len(chars)} chars")
    
    # Extract from TSX files (for hardcoded strings)
    print("\nExtracting characters from TSX files...")
    tsx_count = 0
    for tsx_file in SRC_DIR.rglob("*.tsx"):
        chars = extract_characters_from_ts_file(tsx_file)
        all_chars.update(chars)
        tsx_count += 1
    print(f"  Processed {tsx_count} TSX files")
    
    # Add common Chinese characters
    print("\nAdding common Chinese characters...")
    for char in COMMON_CN_CHARS:
        if '\u4e00' <= char <= '\u9fff':
            all_chars.add(char)
    
    # Add ASCII printable characters
    print("Adding ASCII printable characters...")
    for i in range(32, 127):
        all_chars.add(chr(i))
    
    # Add common symbols
    all_chars.update('°±×÷=≠≈≤≥∞∂∏∑√∫≈≠≤≥±×÷°')
    
    return all_chars

def check_fonttools_installed():
    """Check if fonttools is installed."""
    try:
        import fontTools
        return True
    except ImportError:
        return False

def install_fonttools():
    """Install fonttools and brotli."""
    print("Installing fonttools and brotli...")
    subprocess.run(['pip3', 'install', 'fonttools', 'brotli'], check=True)

def create_charset_file(chars, output_path):
    """Create a text file with all characters for pyftsubset."""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(''.join(sorted(chars)))
    print(f"Charset file created: {output_path} ({len(chars)} chars)")

def subset_font(font_name, chars, output_dir):
    """Subset a single font file."""
    input_path = FONT_DIR / font_name
    output_path = output_dir / font_name
    
    if not input_path.exists():
        print(f"Warning: Font file not found: {input_path}")
        return None
    
    # Create temporary charset file
    charset_file = output_dir / "charset.txt"
    create_charset_file(chars, charset_file)
    
    try:
        # Use python -m fontTools.subset instead of pyftsubset command
        # This is more reliable as it doesn't depend on PATH
        cmd = [
            'python3', '-m', 'fontTools.subset',
            str(input_path),
            f'--text-file={charset_file}',
            '--output-file=' + str(output_path),
            '--flavor=woff2',
            '--layout-features=*',
            '--desubroutinize',
        ]
        
        print(f"\nSubsetting {font_name}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error subsetting {font_name}:")
            print(result.stderr)
            return None
        
        # Get file sizes
        original_size = input_path.stat().st_size
        new_size = output_path.stat().st_size
        reduction = (1 - new_size / original_size) * 100
        
        print(f"  Original: {original_size / 1024:.1f} KB")
        print(f"  New:      {new_size / 1024:.1f} KB")
        print(f"  Saved:    {reduction:.1f}%")
        
        return output_path
    except Exception as e:
        print(f"Error subsetting {font_name}: {e}")
        return None
    finally:
        # Clean up charset file
        if charset_file.exists():
            charset_file.unlink()

def main():
    print("=" * 60)
    print("Font Subsetting Script for MemoryBear")
    print("=" * 60)
    
    # Check fonttools
    if not check_fonttools_installed():
        print("\nfonttools not found. Installing...")
        install_fonttools()
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Extract all characters
    all_chars = extract_characters_from_all_sources()
    print(f"\nTotal unique characters: {len(all_chars)}")
    
    # Character distribution
    cn_count = sum(1 for c in all_chars if '\u4e00' <= c <= '\u9fff')
    print(f"  Chinese characters: {cn_count}")
    print(f"  Other characters: {len(all_chars) - cn_count}")
    
    # Subset each font
    print("\n" + "=" * 60)
    print("Subsetting fonts...")
    print("=" * 60)
    
    total_original = 0
    total_new = 0
    
    results = []
    for font_name in FONTS_TO_SUBSET:
        input_path = FONT_DIR / font_name
        if input_path.exists():
            original_size = input_path.stat().st_size
            result = subset_font(font_name, all_chars, OUTPUT_DIR)
            if result:
                new_size = result.stat().st_size
                total_original += original_size
                total_new += new_size
                results.append({
                    'font': font_name,
                    'original': original_size,
                    'new': new_size,
                    'reduction': (1 - new_size / original_size) * 100
                })
        else:
            print(f"Skipping {font_name} (not found)")
    
    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for r in results:
        print(f"{r['font']}: {r['original']/1024:.1f}KB → {r['new']/1024:.1f}KB ({r['reduction']:.1f}% reduction)")
    
    if total_original > 0:
        print(f"\nTotal: {total_original/1024:.1f}KB → {total_new/1024:.1f}KB")
        print(f"Saved: {(total_original-total_new)/1024:.1f}KB ({(1-total_new/total_original)*100:.1f}%)")
    
    print("\n✓ Font subsetting complete!")
    print(f"\nSubsetted fonts are in: {OUTPUT_DIR}")
    print("\nNext steps:")
    print("1. Test the subsetted fonts")
    print("2. Replace original fonts with subsetted versions")
    print("3. Update font.css if needed")

if __name__ == "__main__":
    main()