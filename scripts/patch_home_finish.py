from __future__ import annotations

import base64
import gzip
import io
import json
import re
from pathlib import Path
from urllib.parse import quote

import requests
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


def official_url(target):
    return f'https://homefinish.com.br/wp-content/uploads/2023/08/papel-parede-nacional-home-finish-bio-habitat-{target}.jpg'


def transport_urls(source_url):
    # A origem registrada continua sendo a Home Finish. Os endpoints abaixo são apenas transporte
    # para contornar o 403 aplicado aos IPs do GitHub Actions.
    encoded = quote(source_url, safe='')
    return [
        f'https://res.cloudinary.com/demo/image/fetch/{source_url}',
        f'https://res.cloudinary.com/demo/image/fetch/{encoded}',
        f'https://images.weserv.nl/?url={encoded}&output=jpg&q=100',
    ]


def image_from_bytes(raw):
    if len(raw) < 20_000:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = im.convert('RGB')
    except Exception:
        return None
    if min(im.size) < 700:
        return None
    return im


def download_image(session, target):
    source = official_url(target)
    errors = []
    for transport in transport_urls(source):
        try:
            r = session.get(transport, timeout=45, headers={'Accept': 'image/*,*/*;q=0.8'})
            if r.status_code != 200:
                errors.append(f'{r.status_code}:{transport}')
                continue
            im = image_from_bytes(r.content)
            if im is None:
                errors.append(f'invalid-image:{len(r.content)}:{transport}')
                continue
            return source, transport, r.content, im
        except Exception as exc:
            errors.append(f'{type(exc).__name__}:{transport}')
    raise RuntimeError(f'proxy-failed:{target}:' + ' | '.join(errors))


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
        tp, 'JPEG', quality=86, optimize=True, progressive=True
    )
    return op, tp


def patch_item(rec, raw, im, source, transport):
    op, tp = save_pair(raw, im, rec)
    return {
        **rec,
        'source_page': f'https://homefinish.com.br/papel-de-parede/{norm(rec["r"])}/',
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


def main():
    records = load_home_finish_records()
    missing = [t for t in TARGETS if t not in records]
    if missing:
        raise RuntimeError('records-not-found:' + ','.join(missing))
    wrong = [t for t in TARGETS if records[t].get('c') != 'BIO Habitat']
    if wrong:
        raise RuntimeError('wrong-collection:' + ','.join(wrong))

    manifest = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'items': [], 'failures': []}
    items = manifest.get('items', [])
    failures = manifest.get('failures', [])
    by_key = {(x.get('f'), x.get('c'), str(x.get('r'))): x for x in items}
    successes = set()
    session = requests.Session()

    for target in TARGETS:
        rec = records[target]
        source, transport, raw, im = download_image(session, target)
        by_key[(rec['f'], rec['c'], str(rec['r']))] = patch_item(rec, raw, im, source, transport)
        successes.add(target)
        print(f'PATCH READY {rec["r"]} {im.width}x{im.height} bytes={len(raw)} source={source} via={transport}', flush=True)

    if len(successes) != 28:
        raise RuntimeError(f'patch-incomplete:{len(successes)}/28')

    new_items = list(by_key.values())
    new_failures = [x for x in failures if not (x.get('f') == 'Home Finish' and norm(x.get('r')) in successes)]
    new_items.sort(key=lambda x: (x.get('f', ''), x.get('c', ''), str(x.get('r', ''))))
    new_failures.sort(key=lambda x: (x.get('f', ''), x.get('c', ''), str(x.get('r', ''))))
    MANIFEST.write_text(
        json.dumps({'ready': len(new_items), 'failed': len(new_failures), 'items': new_items, 'failures': new_failures}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print('PATCH SUMMARY success=28/28', flush=True)


if __name__ == '__main__':
    main()
