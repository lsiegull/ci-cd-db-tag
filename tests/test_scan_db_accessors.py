import os
from pathlib import Path

from scan_db_accessors import scan_repo, snake_to_camel


def test_snake_to_camel():
    assert snake_to_camel('description') == 'Description'
    assert snake_to_camel('created_at') == 'CreatedAt'
    assert snake_to_camel('id') == 'ID'


def test_scan_repo_detects_field_usages(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    # create SQL schema
    sql = """
    CREATE TABLE items (
      id int,
      description text
    );
    """
    (root / 'schema.sql').write_text(sql)

    # create a Go file that accesses the generated struct field
    go = '''package main

type Item struct {
    ID int
    Description string
}

func main() {
    var it Item
    _ = it.Description
}
'''
    (root / 'main.go').write_text(go)

    res = scan_repo(str(root))
    # description column should be found and have at least one usage
    assert 'description' in res
    usages = res['description']['usages']
    assert any(u['file'].endswith('main.go') for u in usages)
    # annotation should be absent by default
    assert res['description'].get('annotated') is False


def test_scan_repo_detects_annotations(tmp_path):
    root = tmp_path / 'repo2'
    root.mkdir()
    sql = """
    CREATE TABLE items (
      id int,
      description text -- data_type: pii
    );
    """
    (root / 'schema.sql').write_text(sql)

    go = '''package main

type Item struct {
    ID int
    Description string
}

func main() {
    var it Item
    _ = it.Description
}
'''
    (root / 'main.go').write_text(go)

    res = scan_repo(str(root))
    assert 'description' in res
    assert res['description'].get('annotated') is True
