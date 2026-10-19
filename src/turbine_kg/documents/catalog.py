"""Source identities and explicit derivative links, independent of any book."""
import csv
from pathlib import Path
from turbine_kg.settings import PROJECT_ROOT


def rows(name: str) -> list[dict]:
    lines = (PROJECT_ROOT / 'config' / name).read_text(encoding='utf-8-sig').splitlines()
    return list(csv.DictReader((line for line in lines if line and not line.startswith('#')), delimiter='\t'))


def document_id(path: Path, settings) -> str:
    from turbine_kg.runtime import identity
    relative = path.resolve().relative_to(settings.source_root).as_posix()
    found = next((row for row in rows('document_identity.tsv') if row['relative_path'] == relative), None)
    return found['document_logical_id'] if found else identity('document', relative)


def derivative(path: Path, settings) -> Path | None:
    relative = path.resolve().relative_to(settings.source_root).as_posix()
    for row in rows('derived_asset_links.tsv'):
        if row['source_relative_path'] == relative:
            candidate = settings.ocr_derived_root / Path(row['derived_relative_path']).name
            if candidate.is_file(): return candidate
    return None


def verified_source(path: Path, settings, digest: str) -> None:
    relative = path.resolve().relative_to(settings.source_root).as_posix()
    declared = next((row for row in rows('source_allowlist.tsv') if row['path'] == relative), None)
    if not declared or declared['sha256'] != digest or int(declared['size_bytes']) != path.stat().st_size:
        raise ValueError('来源路径、大小或哈希不符合资料白名单')
