"""Copy a frozen SQLite snapshot into an empty, already-migrated MySQL schema.

Run from the one-shot migration service. Credentials come from Django settings/secrets.
Every source field is compared after copying; IDs and migration-independent data are preserved.
Never use a live SQLite file. The supplied SHA256 makes that requirement verifiable.
"""
import argparse
import datetime as dt
import decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time


def identifier(name):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        raise ValueError('Unexpected database identifier')
    return name


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def temporal(value, kind):
    if value is None:
        return None
    if kind.startswith(('datetime', 'timestamp')):
        parsed = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(dt.timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    if kind == 'date':
        return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    if kind.startswith('time'):
        if isinstance(value, dt.timedelta):
            return value
        parsed = value if isinstance(value, dt.time) else dt.time.fromisoformat(str(value))
        return dt.timedelta(hours=parsed.hour, minutes=parsed.minute, seconds=parsed.second, microseconds=parsed.microsecond)
    return value


def converted(value, kind):
    if value is None:
        return None
    if kind.startswith(('datetime', 'timestamp', 'date', 'time')):
        return temporal(value, kind)
    if kind.startswith(('decimal', 'numeric')):
        return decimal.Decimal(str(value))
    if isinstance(value, memoryview):
        return value.tobytes()
    return value


def typed_json(value):
    if isinstance(value, dict):
        return ('object', tuple((key, typed_json(item)) for key, item in sorted(value.items())))
    if isinstance(value, list):
        return ('array', tuple(typed_json(item) for item in value))
    if isinstance(value, float):
        return ('float', value.hex())
    return (type(value).__name__, value)


def comparable(value, kind):
    if kind == 'json':
        # SQL NULL and JSON null must remain distinct for is-null filters.
        return ('sql-null',) if value is None else ('json', typed_json(json.loads(value)))
    return converted(value, kind)


def save_state(path, state):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    tmp.replace(path)


def table_info(source, target, table):
    source_columns = source.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = [identifier(row[1]) for row in source_columns]
    primary = [row[1] for row in source_columns if row[5]]
    if len(primary) != 1:
        raise RuntimeError(f'{table}: a single-column primary key is required')
    target.execute(f'SHOW COLUMNS FROM `{table}`')
    target_columns = {row[0]: row for row in target.fetchall()}
    missing = set(columns) - set(target_columns)
    if missing:
        raise RuntimeError(f'{table}: target is missing columns {sorted(missing)}')
    for name, row in target_columns.items():
        if name not in columns and row[2] == 'NO' and row[4] is None and 'GENERATED' not in row[5]:
            raise RuntimeError(f'{table}: target requires an unmapped column {name}')
    return columns, primary[0], [target_columns[name][1].lower() for name in columns]


def source_batches(source, table, columns, primary, batch_size, after=None):
    names = ','.join(f'"{name}"' for name in columns)
    while True:
        where = '' if after is None else f' WHERE "{primary}" > ?'
        params = (batch_size,) if after is None else (after, batch_size)
        rows = source.execute(f'SELECT {names} FROM "{table}"{where} ORDER BY "{primary}" LIMIT ?', params).fetchall()
        if not rows:
            return
        yield rows
        after = rows[-1][columns.index(primary)]


def validate_references(connection):
    with connection.cursor() as cursor:
        cursor.execute('''SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                          FROM information_schema.KEY_COLUMN_USAGE
                          WHERE CONSTRAINT_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL''')
        relations = cursor.fetchall()
        for table, column, parent, parent_column in relations:
            for name in (table, column, parent, parent_column):
                identifier(name)
            cursor.execute(f'''SELECT c.`{column}` FROM `{table}` c LEFT JOIN `{parent}` p
                               ON p.`{parent_column}`=c.`{column}`
                               WHERE c.`{column}` IS NOT NULL AND p.`{parent_column}` IS NULL LIMIT 1''')
            if cursor.fetchone():
                raise RuntimeError(f'Foreign-key verification failed: {table}.{column} -> {parent}.{parent_column}')
    return len(relations)


def migrate(source_path, expected_sha, target_name, checkpoint, batch_size=2000, verify_only=False):
    sys.path.insert(0, '/label-studio/label_studio')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.label_studio')
    import django
    django.setup()
    from django.db import connection, transaction
    if connection.vendor != 'mysql' or connection.settings_dict['NAME'] != target_name:
        raise RuntimeError('The active database does not match the explicit MySQL target')
    if file_hash(source_path) != expected_sha:
        raise RuntimeError('SQLite snapshot SHA256 mismatch')
    source = sqlite3.connect(source_path.as_uri() + '?mode=ro&immutable=1', uri=True)
    source.execute('PRAGMA query_only=ON')
    if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise RuntimeError('SQLite integrity check failed')
    tables = [identifier(row[0]) for row in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name") if row[0] != 'django_migrations']
    state = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {
        'source_sha256': expected_sha, 'target_database': target_name,
        'started_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'phase': 'preparing', 'tables': {},
    }
    if state['source_sha256'] != expected_sha or state['target_database'] != target_name:
        raise RuntimeError('Checkpoint belongs to a different snapshot or database')
    with connection.cursor() as cursor:
        if not verify_only:
            cursor.execute('SELECT CONNECTION_ID()')
            own_connection = cursor.fetchone()[0]
            cursor.execute('SHOW PROCESSLIST')
            if any(row[0] != own_connection and row[3] == target_name for row in cursor.fetchall()):
                raise RuntimeError('Stop application writers connected to the target before copying')
        cursor.execute('SHOW TABLES')
        if set(tables) - {row[0] for row in cursor.fetchall()}:
            raise RuntimeError('Target schema is incomplete; run Django migrations first')
        descriptions = {table: table_info(source, cursor, table) for table in tables}
        if state['phase'] == 'preparing':
            if verify_only:
                raise RuntimeError('No completed copy is recorded for verification')
            for table in ('task', 'project', 'htx_user'):
                cursor.execute(f'SELECT COUNT(*) FROM `{table}`')
                if cursor.fetchone()[0]:
                    raise RuntimeError(f'Refusing to replace a nonempty target business table: {table}')
            cursor.execute('SET FOREIGN_KEY_CHECKS=0')
            try:
                for table in tables:
                    cursor.execute(f'DELETE FROM `{table}`')
            finally:
                cursor.execute('SET FOREIGN_KEY_CHECKS=1')
            state['phase'] = 'copying'
            save_state(checkpoint, state)
    if state['phase'] == 'copying' and not verify_only:
        with connection.cursor() as cursor:
            cursor.execute('SET FOREIGN_KEY_CHECKS=0')
            cursor.execute('SET UNIQUE_CHECKS=0')
        try:
            for table in tables:
                progress = state['tables'].setdefault(table, {'copied': 0, 'last_pk': None, 'complete': False})
                if progress['complete']:
                    continue
                columns, primary, kinds = descriptions[table]
                with connection.cursor() as cursor:
                    # Discard only an uncheckpointed batch after interruption, never completed tables.
                    if progress['last_pk'] is None:
                        cursor.execute(f'DELETE FROM `{table}`')
                        progress['copied'] = 0
                    else:
                        cursor.execute(f'DELETE FROM `{table}` WHERE `{primary}` > %s', [progress['last_pk']])
                names = ','.join(f'`{name}`' for name in columns)
                sql = f'INSERT INTO `{table}` ({names}) VALUES ({",".join(["%s"] * len(columns))})'
                last_notice = time.monotonic()
                for rows in source_batches(source, table, columns, primary, batch_size, progress['last_pk']):
                    values = [tuple(converted(value, kind) for value, kind in zip(row, kinds)) for row in rows]
                    try:
                        with transaction.atomic(), connection.cursor() as cursor:
                            cursor.executemany(sql, values)
                    except Exception as error:
                        code = error.args[0] if error.args and isinstance(error.args[0], int) else 'unknown'
                        raise RuntimeError(f'Insert failed in {table} after {progress["copied"]} rows ({type(error).__name__}, code {code})') from None
                    progress['copied'] += len(rows)
                    last_pk = rows[-1][columns.index(primary)]
                    # Token/session keys can be primary keys. Never persist them in progress files.
                    # Incomplete string-keyed tables restart as a whole; integer task tables resume.
                    progress['last_pk'] = last_pk if isinstance(last_pk, int) else None
                    save_state(checkpoint, state)
                    if time.monotonic() - last_notice > 10:
                        print(f'COPY {table}: {progress["copied"]}', flush=True)
                        last_notice = time.monotonic()
                progress['complete'] = True
                save_state(checkpoint, state)
                print(f'COPIED {table}: {progress["copied"]}', flush=True)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SET UNIQUE_CHECKS=1')
                cursor.execute('SET FOREIGN_KEY_CHECKS=1')
        state['phase'] = 'verifying'
        save_state(checkpoint, state)
    if state['phase'] == 'copying':
        raise RuntimeError('Copy is incomplete; resume without --verify-only first')
    for table in tables:
        columns, primary, kinds = descriptions[table]
        expected_count = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT COUNT(*) FROM `{table}`')
            if cursor.fetchone()[0] != expected_count:
                raise RuntimeError(f'Row count mismatch in {table}')
            names = ','.join(f'`{name}`' for name in columns)
            checked = 0
            for rows in source_batches(source, table, columns, primary, batch_size):
                low, high = rows[0][columns.index(primary)], rows[-1][columns.index(primary)]
                cursor.execute(f'SELECT {names} FROM `{table}` WHERE `{primary}` >= %s AND `{primary}` <= %s ORDER BY `{primary}`', [low, high])
                target_rows = cursor.fetchall()
                if len(rows) != len(target_rows):
                    raise RuntimeError(f'Primary-key range mismatch in {table}')
                for original, copied in zip(rows, target_rows):
                    for column, kind, left, right in zip(columns, kinds, original, copied):
                        if comparable(left, kind) != comparable(right, kind):
                            raise RuntimeError(f'Value mismatch in {table}.{column}, row offset {checked}')
                checked += len(rows)
                if checked % (batch_size * 100) == 0:
                    print(f'CHECK {table}: {checked}/{expected_count}', flush=True)
            state['tables'][table]['verified'] = checked
            save_state(checkpoint, state)
            print(f'VERIFIED {table}: {checked}', flush=True)
    state['foreign_keys_verified'] = validate_references(connection)
    if not verify_only:
        # Preserve SQLite's high-water marks, including IDs deleted before the snapshot.
        sequences = dict(source.execute('SELECT name, seq FROM sqlite_sequence'))
        with connection.cursor() as cursor:
            for table, sequence in sequences.items():
                if table not in descriptions:
                    continue
                cursor.execute(f'SHOW COLUMNS FROM `{identifier(table)}`')
                if any('auto_increment' in row[5] for row in cursor.fetchall()):
                    cursor.execute(f'ALTER TABLE `{table}` AUTO_INCREMENT = {int(sequence) + 1}')
        state['auto_increment_high_water_marks_preserved'] = True
    state['phase'] = 'verified'
    state['completed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_state(checkpoint, state)
    print('All source tables and foreign keys verified. Django migration history retained from the MySQL schema.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--target-database', required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=2000)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 10000 or not re.fullmatch(r'[a-f0-9]{64}', args.source_sha256):
        parser.error('Invalid batch size or SHA256')
    migrate(args.source.resolve(strict=True), args.source_sha256, identifier(args.target_database), args.checkpoint,
            args.batch_size, args.verify_only)


if __name__ == '__main__':
    main()
