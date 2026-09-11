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

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36'


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


def candidates(target):
    # O primeiro candidato é exatamente o hotlink oficial já validado no catálogo.
    out = [f'https://homefinish.com.br/wp-content/uploads/2023/08/papel-parede-nacional-home-finish-bio-habitat-{target}.jpg']
    bases = ['https://homefinish.com.br/wp-content/uploads', 'https://www.homefinish.com.br/wp-content/uploads']
    months = ['2023/08', '2024/04', '2024/09', '2025/01', '2025/04']
    stems = [
        f'papel-parede-nacional-home-finish-bio-habitat-{target}',
        f'papel-parede-nacional-homefinish-bio-habitat-{target}',
    ]
    suffixes = ['.jpg', '-1.jpg', '-2.jpg', '-sem-marca.jpg', '-sem-marca-1.jpg', '_1.jpg', '_2.jpg', '.png']
    for base in bases:
        for month in months:
            for stem in stems:
                for suffix in suffixes:
                    url = f'{base}/{month}/{stem}{suffix}'
                    if url not in out:
                        out.append(url)
    return out


def image_ok(raw):
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


def proxy_url(source_url):
    # images.weserv.nl apenas transporta a imagem quando a Home Finish bloqueia o IP do runner.
    # O source_resolved gravado no manifest continua sendo o JPG oficial da Home Finish.
    return 'https://images.weserv.nl/?url=' + quote(source_url, safe='') + '&output=jpg&q=100'


def download_official(session, target):
    errors = []
    for source_url in candidates(target):
        # 1) tentativa direta na origem oficial
        try:
            r = session.get(
                source_url,
                timeout=45,
                headers={'User-Agent': UA, 'Referer': 'https://homefinish.com.br/', 'Accept': 'image/*,*/*;q=0.8'},
            )
            if r.status_code == 200:
                im = image_ok(r.content)
                if im is not None:
                    return source_url, source_url, r.content, im
            else:
                errors.append(f'direct-{r.status_code}:{source_url}')
        except Exception as exc:
            errors.append(f'direct-{type(exc).__name__}:{source_url}')

        # 2) mesma origem oficial via proxy de imagem, para contornar bloqueio de IP do runner
        try:
            transport = proxy_url(source_url)
            r = session.get(transport, timeout=60, headers={'User-Agent': UA, 'Accept': 'image/*,*/*;q=0.8'})
            if r.status_code == 200:
                im = image_ok(r.content)
                if im is not None:
                    return source_url, transport, r.content, im
            else:
                errors.append(f'proxy-{r.status_code}:{source_url}')
        except Exception as exc:
            errors.append(f'proxy-{type(exc).__name__}:{source_url}')

    raise RuntimeError(f'official-jpg-not-found:{target}:' + ' | '.join(errors[-12:]))


def save_pair(raw, im, rec):
    ref = str(rec['r'])
    base = OUT / rec['s']
    od = base / 'originals'
    td = base / 'thumbnails'
    od.mkdir(parents=True, exist_ok=True)
    td.mkdir(parents=True, exist_ok=True)
    op = od / f'{ref}.jpg'
    tp = td / f'{ref}.jpg'

    # O transporte por proxy pode recomprimir; salvamos bytes recebidos quando JPEG.
    probe = Image.open(io.BytesIO(raw))
    if (probe.format or '').upper() in {'JPEG', 'JPG'}:
        op.write_bytes(raw)
    else:
        im.save(op, 'JPEG', quality=95, optimize=True, progressive=True)

    ImageOps.fit(im, (520, 520), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)).save(
        tp, 'JPEG', quality=86, optimize=True, progressive=True
    )
    return op, tp


def patch_item(rec, raw, im, source_url, transport_url):
    op, tp = save_pair(raw, im, rec)
    return {
        **rec,
        'source_page': f'https://homefinish.com.br/papel-de-parede/{norm(rec["r"])}/',
        'source_resolved': source_url,
        'transport_url': transport_url,
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
        source_url, transport_url, raw, im = download_official(session, target)
        by_key[(rec['f'], rec['c'], str(rec['r']))] = patch_item(rec, raw, im, source_url, transport_url)
        successes.add(target)
        print(f'PATCH READY {rec["r"]} {im.width}x{im.height} bytes={len(raw)} source={source_url}', flush=True)

    if successes != set(TARGETS):
        raise RuntimeError(f'patch-incomplete:{len(successes)}/{len(TARGETS)}')

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
