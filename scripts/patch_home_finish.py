from __future__ import annotations

import base64
import gzip
import io
import json
import re
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'dados' / 'colecoes'
OUT = ROOT / 'imagens' / 'home-finish'
MANIFEST = ROOT / 'dados' / 'biblioteca-imagens.json'

TARGETS = [
    '101041', '101042', '101043', '101044', '101036', '101016', '101014',
    '101022', '101019', '101020', '101011', '101010', '101038', '101032',
    '101033', '101034', '101035', '101029', '101030', '101028', '101027',
    '101026', '101025', '101018', '101040', '101039', '101045', '101046',
]

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36'


def norm(ref):
    return re.sub(r'^(?:BH|MI)', '', str(ref or '').strip(), flags=re.I)


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


def product_page(target):
    return f'https://homefinish.com.br/papel-de-parede/{target}/'


def guessed_sources(target):
    bases = [
        'https://homefinish.com.br/wp-content/uploads',
        'https://www.homefinish.com.br/wp-content/uploads',
    ]
    months = ['2023/08', '2024/09', '2024/04']
    stems = [
        f'papel-parede-nacional-home-finish-bio-habitat-{target}',
        f'papel-parede-nacional-homefinish-bio-habitat-{target}',
    ]
    suffixes = ['.jpg', '-1.jpg', '-2.jpg', '-sem-marca.jpg', '-sem-marca-1.jpg', '_1.jpg', '_2.jpg', '.png']
    out = []
    for base in bases:
        for month in months:
            for stem in stems:
                for suffix in suffixes:
                    u = f'{base}/{month}/{stem}{suffix}'
                    if u not in out:
                        out.append(u)
    return out


def page_sources(session, target):
    page = product_page(target)
    found = []
    try:
        r = session.get(page, timeout=25, headers={'User-Agent': UA, 'Accept': 'text/html,*/*;q=0.8'})
        if r.status_code == 200 and r.text:
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                label = ' '.join(a.stripped_strings).lower()
                if target in href and ('wp-content/uploads' in href or 'baixar' in label):
                    found.append(href)
            for img in soup.find_all('img'):
                for attr in ('src', 'data-src', 'data-lazy-src'):
                    href = img.get(attr)
                    if href and target in href and 'wp-content/uploads' in href:
                        found.append(href)
    except Exception as exc:
        print(f'PAGE WARN {target} {type(exc).__name__}', flush=True)
    dedup = []
    for u in found:
        if u.startswith('//'):
            u = 'https:' + u
        if u.startswith('/'):
            u = 'https://homefinish.com.br' + u
        if u.startswith('http') and u not in dedup:
            dedup.append(u)
    return dedup


def source_candidates(session, target):
    out = []
    for u in page_sources(session, target) + guessed_sources(target):
        if u not in out:
            out.append(u)
    return out


def transport_urls(source_url):
    encoded = quote(source_url, safe='')
    parsed = urlparse(source_url)
    wp_path = parsed.netloc + parsed.path
    return [
        f'https://external-content.duckduckgo.com/iu/?u={encoded}&f=1&nofb=1',
        source_url,
        f'https://i0.wp.com/{wp_path}',
        f'https://i1.wp.com/{wp_path}',
        f'https://i2.wp.com/{wp_path}',
        f'https://images.weserv.nl/?url={encoded}&output=jpg&q=100',
    ]


def image_from_bytes(raw):
    if len(raw) < 15_000:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = im.convert('RGB')
    except Exception:
        return None
    w, h = im.size
    if w < 500 or h < 500 or (w * h) < 350_000:
        return None
    return im


def existing_image(rec):
    ref = str(rec['r'])
    op = OUT / rec['s'] / 'originals' / f'{ref}.jpg'
    if not op.exists():
        return None
    try:
        raw = op.read_bytes()
        im = image_from_bytes(raw)
    except Exception:
        return None
    if im is None:
        return None
    return raw, im, op


def download_image(session, target):
    errors = []
    for source in source_candidates(session, target):
        for transport in transport_urls(source):
            try:
                r = session.get(transport, timeout=30, headers={
                    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                    'User-Agent': UA,
                    'Referer': product_page(target),
                })
                if r.status_code != 200:
                    errors.append(f'{r.status_code}:{transport}')
                    continue
                im = image_from_bytes(r.content)
                if im is None:
                    errors.append(f'invalid:{len(r.content)}:{transport}')
                    continue
                return source, transport, r.content, im
            except Exception as exc:
                errors.append(f'{type(exc).__name__}:{transport}')
        if len(errors) > 36:
            break
    raise RuntimeError(f'not-resolved:{target}:' + ' | '.join(errors[-12:]))


def save_pair(raw, im, rec):
    ref = str(rec['r'])
    base = OUT / rec['s']
    od = base / 'originals'
    td = base / 'thumbnails'
    od.mkdir(parents=True, exist_ok=True)
    td.mkdir(parents=True, exist_ok=True)
    op = od / f'{ref}.jpg'
    tp = td / f'{ref}.jpg'
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
        tp, 'JPEG', quality=88, optimize=True, progressive=True
    )
    return op, tp


def patch_item(rec, raw, im, source, transport):
    op, tp = save_pair(raw, im, rec)
    return {
        **rec,
        'source_page': product_page(norm(rec['r'])),
        'source_resolved': source,
        'transport_url': transport,
        'original': str(op.relative_to(ROOT)),
        'thumbnail': str(tp.relative_to(ROOT)),
        'width': im.width,
        'height': im.height,
        'status': 'ready',
        'patched': True,
        'patch_type': 'official-home-finish-via-image-transport',
    }


def patch_existing(rec, raw, im, op):
    _, tp = save_pair(raw, im, rec)
    return {
        **rec,
        'source_page': product_page(norm(rec['r'])),
        'source_resolved': 'existing-local-official-copy',
        'original': str(op.relative_to(ROOT)),
        'thumbnail': str(tp.relative_to(ROOT)),
        'width': im.width,
        'height': im.height,
        'status': 'ready',
        'patched': True,
    }


def main():
    records = load_home_finish_records()
    missing_records = [t for t in TARGETS if t not in records]
    if missing_records:
        raise RuntimeError('records-not-found:' + ','.join(missing_records))

    manifest = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'items': [], 'failures': []}
    items = manifest.get('items', [])
    failures = manifest.get('failures', [])
    by_key = {(x.get('f'), x.get('c'), norm(x.get('r'))): x for x in items}
    successes = set()
    unresolved = []
    session = requests.Session()

    for target in TARGETS:
        rec = records[target]
        try:
            cached = existing_image(rec)
            if cached:
                raw, im, op = cached
                by_key[(rec['f'], rec['c'], target)] = patch_existing(rec, raw, im, op)
                successes.add(target)
                print(f'PATCH EXISTING {target} {im.width}x{im.height}', flush=True)
                continue

            source, transport, raw, im = download_image(session, target)
            by_key[(rec['f'], rec['c'], target)] = patch_item(rec, raw, im, source, transport)
            successes.add(target)
            print(f'PATCH READY {target} {im.width}x{im.height} bytes={len(raw)} via={transport}', flush=True)
        except Exception as exc:
            unresolved.append(target)
            print(f'PATCH UNRESOLVED {target}: {exc}', flush=True)

    new_items = list(by_key.values())
    new_failures = [
        x for x in failures
        if not (x.get('f') == 'Home Finish' and x.get('c') == 'BIO Habitat' and norm(x.get('r')) in successes)
    ]
    new_items.sort(key=lambda x: (x.get('f', ''), x.get('c', ''), norm(x.get('r'))))
    new_failures.sort(key=lambda x: (x.get('f', ''), x.get('c', ''), norm(x.get('r'))))
    MANIFEST.write_text(
        json.dumps({'ready': len(new_items), 'failed': len(new_failures), 'items': new_items, 'failures': new_failures}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    print(f'PATCH SUMMARY success={len(successes)}/28 unresolved={len(unresolved)}', flush=True)
    if unresolved:
        print('PATCH REMAINING ' + ','.join(unresolved), flush=True)


if __name__ == '__main__':
    main()

# artifact-refresh 2026-09-15
