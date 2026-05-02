import sys
from pathlib import Path

# Add repo root to path so tests can import the script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detect_new_fields import extract_table_columns, detect_new_fields_in_file, missing_data_type_annotations_in_file


def test_extract_create_table():
    sql = '''
    CREATE TABLE users (
      id serial PRIMARY KEY,
      name text,
      email text
    );
    '''
    tables = extract_table_columns(sql)
    assert 'users' in tables
    assert tables['users'] == {'id', 'name', 'email'}


def test_extract_alter_add_column():
    sql = "ALTER TABLE users ADD COLUMN age integer;"
    tables = extract_table_columns(sql)
    assert 'users' in tables
    assert 'age' in tables['users']


def test_detect_new_fields(tmp_path, monkeypatch):
    base_sql = "CREATE TABLE items (id int, name text);"
    head_sql = "CREATE TABLE items (id int, name text, description text);"
    base_file = tmp_path / "schema.sql"
    base_file.write_text(base_sql)
    head_file = tmp_path / "schema.sql"
    head_file.write_text(head_sql)

    # Mock git show to return base or head content depending on ref
    def fake_run(cmd, check=True, capture_output=True):
        class R:
            def __init__(self, out):
                self.stdout = out.encode('utf-8')
        ref_path = cmd[2]
        if ref_path.startswith('origin') or ref_path.startswith('main'):
            return R(base_sql)
        return R(head_sql)

    monkeypatch.setattr('subprocess.run', fake_run)

    new = detect_new_fields_in_file('origin/main', str(base_file))
    assert 'items' in new
    assert 'description' in new['items']


def test_missing_data_type_annotation(tmp_path, monkeypatch):
    base_sql = "CREATE TABLE items (id int, name text);"
    head_sql = "CREATE TABLE items (id int, name text, description text);"
    base_file = tmp_path / "schema.sql"
    base_file.write_text(base_sql)
    head_file = tmp_path / "schema.sql"
    head_file.write_text(head_sql)

    def fake_run(cmd, check=True, capture_output=True):
        class R:
            def __init__(self, out):
                self.stdout = out.encode('utf-8')
        ref_path = cmd[2]
        if ref_path.startswith('origin') or ref_path.startswith('main'):
            return R(base_sql)
        return R(head_sql)

    monkeypatch.setattr('subprocess.run', fake_run)

    missing = missing_data_type_annotations_in_file('origin/main', str(base_file))
    assert 'items' in missing
    assert 'description' in missing['items']


def test_present_data_type_annotation(tmp_path, monkeypatch):
    base_sql = "CREATE TABLE items (id int, name text);"
    # add inline comment with data_type annotation
    head_sql = "CREATE TABLE items (id int, name text, description text -- data_type: pii);"
    base_file = tmp_path / "schema.sql"
    base_file.write_text(base_sql)
    head_file = tmp_path / "schema.sql"
    head_file.write_text(head_sql)

    def fake_run(cmd, check=True, capture_output=True):
        class R:
            def __init__(self, out):
                self.stdout = out.encode('utf-8')
        ref_path = cmd[2]
        if ref_path.startswith('origin') or ref_path.startswith('main'):
            return R(base_sql)
        return R(head_sql)

    monkeypatch.setattr('subprocess.run', fake_run)

    missing = missing_data_type_annotations_in_file('origin/main', str(base_file))
    assert missing == {}
