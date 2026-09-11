from __future__ import annotations

import base64
import gzip
import io
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'dados' / 'colecoes'
OUT = ROOT / 'imagens' / 'home-finish'
MANIFEST = ROOT / 'dados' / 'biblioteca-imagens.json'

# BIO Habitat que permaneciam apenas com hotlink no catálogo.
# A origem é exclusivamente a página/JPG oficial da Home Finish.
TARGETS = [
    '101041', '101042', '101043', '101044', '101036', '101016', '101014',
    '101022', '101019', '101020', '101011', '101010', '101038', '101032',
    '101033', '101034', '101035', '101029', '101030', '101028', '101027',
    '101026', '101025', '101018', '101040', '101039', '101045', '101046',
]

UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152 Safari/537.36'
)


def norm(ref):
    return re.sub(r'^(?:BH|MI)', '', str(ref).strip(), flags=re.I)


def normalize_collection(data):
    if data.get('records'):
        return data['records']
    vendor = data.get('fornecedor')
    col = data.get('colecao')
    slug = data.get('slug')
    return [
        {'f': vendor, 'c': col, 's': slug, 'r': item.get('r') or item.get('referencia'), 'u': item.get('u')}
        for item in data.get('itens', [])
    ]


def load_home_finish_records():
    by_norm = {}
    for p in sorted(DATA_DIR.glob('*.json')):
        data = json.loads(p.read_text(encoding='utf-8'))
        for rec in normalize_collection(data):
            if rec.get('f') == 'Home Finish' and rec.get('r'):
                by_norm[norm(rec['r'])] = rec
    for p in sorted(DATA_DIR.glob('*.json.gz.b64')):
        raw = gzip.decompress(base64.b64decode(p.read_text(encoding='ascii'))).decode('utf-8')
        data = json.loads(raw)
        for rec in normalize_collection(data):
            if rec.get('f') == 'Home Finish' and rec.get('r'):
                by_norm[norm(rec['r'])] = rec
    return by_norm


def official_candidates(session, target):
    page_url = f'https://homefinish.com.br/papel-de-parede/{target}/'
    response = session.get(page_url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    out = []

    def add(url):
        if not url:
            return
        full = urljoin(page_url, url)
        if 'homefinish.com.br' not in full:
            return
        if full not in out:
            out.append(full)

    # Prioridade absoluta: o link oficial BAIXAR JPG da própria página da referência.
    for a in soup.find_all('a', href=True):
        text = a.get_text(' ', strip=True).upper()
        href = a.get('href')
        if 'BAIXAR JPG' in text:
            add(href)

    # Também aceita imagens oficiais da própria página que tragam a referência no URL.
    for tag in soup.find_all(['a', 'img']):
        for attr in ('href', 'src', 'data-src', 'data-lazy-src'):
            value = tag.get(attr)
            if value and target in value and re.search(r'\.(?:jpe?g|png)(?:\?|$)', value, re.I):
                add(value)

    # Fallbacks estritamente no domínio oficial, para páginas antigas cujo botão não seja parseado.
    bases = [
        'https://homefinish.com.br/wp-content/uploads',
        'https://www.homefinish.com.br/wp-content/uploads',
    ]
    months = ['2023/08', '2024/04', '2024/09', '2025/01', '2025/04']
    stems = [
        f'papel-parede-nacional-home-finish-bio-habitat-{target}',
        f'papel-parede-nacional-homefinish-bio-habitat-{target}',
    ]
    suffixes = ['.jpg', '-1.jpg', '-2.jpg', '-sem-marca.jpg', '-sem-marca-1.jpg', '.png']
    for base in bases:
        for month in months:
            for stem in stems:
                for suffix in suffixes:
                    add(f'{base}/{month}/{stem}{suffix}')
    return page_url, out


def download_official(session, target):
    page_url, candidates = official_candidates(session, target)
    errors = []
    for url in candidates:
        try:
            r = session.get(url, timeout=60)
            if r.status_code != 200:
                errors.append(f'{r.status_code}:{url}')
                continue
            raw = r.content
            if len(raw) < 20_000:
                errors.append(f'too-small-bytes:{len(raw)}:{url}')
                continue
            im = Image.open(io.BytesIO(raw))
            im.load()
            im = im.convert('RGB')
            if min(im.size) < 700:
                errors.append(f'too-small-image:{im.size}:{url}')
                continue
            return page_url, url, raw, im
        except Exception as exc:
            errors.append(f'{type(exc).__name__}:{url}')
    raise RuntimeError(f'official-jpg-not-found:{target}:' + ' | '.join(errors[-8:]))


def save_pair(raw, im, rec):
    ref = str(rec['r'])
    base = OUT / rec['s']
    od = base / 'originals'
    td = base / 'thumbnails'
    od.mkdir(parents=True, exist_ok=True)
    td.mkdir(parents=True, exist_ok=True)
    op = od / f'{ref}.jpg'
    tp = td / f'{ref}.jpg'

    # Preserva o arquivo oficial quando ele já for JPEG. Caso contrário converte para JPEG.
    try:
        probe = Image.open(io.BytesIO(raw))
        fmt = (probe.format or '').upper()
    except Exception:
        fmt = ''
    if fmt in {'JPEG', 'JPG'}:
        op.write_bytes(raw)
    else:
        im.save(op, 'JPEG', quality=95, optimize=True, progressive=True)

    ImageOps.fit(im, (520, 520), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)).save(
        tp, 'JPEG', quality=86, optimize=True, progressive=True
    )
    return op, tp


def patch_item(rec, raw, im, page_url, source_url):
    op, tp = save_pair(raw, im, rec)
    return {
        **rec,
        'source_page': page_url,
        'source_resolved': source_url,
        'original': str(op.relative_to(ROOT)),
        'thumbnail': str(tp.relative_to(ROOT)),
        'width': im.width,
        'height': im.height,
        'status': 'ready',
        'patched': True,
        'patch_type': 'official-home-finish-download',
    }


def main():
    records = load_home_finish_records()
    missing_records = [t for t in TARGETS if t not in records]
    if missing_records:
        raise RuntimeError('records-not-found:' + ','.join(missing_records))

    wrong_collection = [t for t in TARGETS if records[t].get('c') != 'BIO Habitat']
    if wrong_collection:
        raise RuntimeError('wrong-collection:' + ','.join(wrong_collection))

    manifest = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'items': [], 'failures': []}
    items = manifest.get('items', [])
    failures = manifest.get('failures', [])
    by_key = {(x.get('f'), x.get('c'), str(x.get('r'))): x for x in items}
    successes = set()

    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8'})

    for target in TARGETS:
        rec = records[target]
        page_url, source_url, raw, im = download_official(session, target)
        by_key[(rec['f'], rec['c'], str(rec['r']))] = patch_item(rec, raw, im, page_url, source_url)
        successes.add(target)
        print(
            f'PATCH READY Home Finish {rec["r"]} {im.width}x{im.height} '
            f'bytes={len(raw)} source={source_url}',
            flush=True,
        )

    if successes != set(TARGETS):
        raise RuntimeError(f'patch-incomplete:{len(successes)}/{len(TARGETS)}')

    new_items = list(by_key.values())
    new_failures = [
        x for x in failures
        if not (x.get('f') == 'Home Finish' and norm(x.get('r')) in successes)
    ]
    new_items.sort(key=lambda x: (x.get('f', ''), x.get('c', ''), str(x.get('r', ''))))
    new_failures.sort(key=lambda x: (x.get('f', ''), x.get('c', ''), str(x.get('r', ''))))

    MANIFEST.write_text(
        json.dumps(
            {'ready': len(new_items), 'failed': len(new_failures), 'items': new_items, 'failures': new_failures},
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    print('PATCH SUMMARY ' + ','.join(TARGETS) + f' success={len(successes)}/{len(TARGETS)}', flush=True)


if __name__ == '__main__':
    main()
