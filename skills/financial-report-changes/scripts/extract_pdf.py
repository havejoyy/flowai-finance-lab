#!/usr/bin/env python3
"""Extract PDF text with one-based physical pages, retaining file identity."""
import argparse
import hashlib
import json
from pathlib import Path
from pypdf import PdfReader


def extract(paths, engine='pypdf'):
    result = {}
    for path in paths:
        path = Path(path)
        if path.stem in result:
            raise ValueError('PDF filenames must have distinct stems')
        if engine == 'pypdfium2':
            import pypdfium2
            with pypdfium2.PdfDocument(path) as doc:
                pages = []
                for i in range(len(doc)):
                    page = doc[i]
                    textpage = page.get_textpage()
                    try:
                        pages.append(textpage.get_text_range())
                    finally:
                        textpage.close()
                        page.close()
        elif engine == 'pypdf':
            pages = [p.extract_text() or '' for p in PdfReader(path).pages]
        else:
            raise ValueError('unknown extraction engine')
        if sum(len(p.strip()) for p in pages) < 100:
            raise ValueError(f'{path.name}: no useful text; OCR and review required')
        result[path.stem] = {'path': str(path.resolve()), 'engine': engine,
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                             'pages': pages}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdfs', nargs='+')
    parser.add_argument('--output', required=True)
    parser.add_argument('--engine', choices=['pypdf', 'pypdfium2'], default='pypdf')
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(extract(args.pdfs, args.engine), ensure_ascii=False, indent=2), encoding='utf-8')
