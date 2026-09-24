from app.document import chunk_text, sanitize_text
from app.main import requests_salary_table

def test_chunking():
    chunks=chunk_text('a'*2500,size=1000,overlap=100)
    assert len(chunks)>=3
    assert ''.join(chunks) != ''

def test_sanitize_text_replaces_unencodable_surrogates():
    assert sanitize_text('Convention Eneo 2023 OCR\udcff.pdf') == 'Convention Eneo 2023 OCR?.pdf'

def test_sanitize_text_removes_database_unsafe_controls():
    assert sanitize_text('Convention\x00 Eneo\x01.pdf') == 'Convention Eneo.pdf'

def test_salary_grid_wording_is_detected():
    assert requests_salary_table('quelle est la grille des salaires à eneo')
