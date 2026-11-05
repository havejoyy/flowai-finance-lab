"""User-triggered public-data refresh, separate from strategy screening."""
import json
import uuid
from engine import encode, timestamp
from market_store import MarketStore
from data_recovery import error_info
from report_scope import quote_gap_reason


class DataUpdates:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS data_updates(id TEXT PRIMARY KEY, created_at TEXT, body TEXT)')

    def recover(self):
        with self.store.connect() as db:
            for row in db.execute('SELECT body FROM data_updates').fetchall():
                item = json.loads(row['body'])
                if item['state'] == 'RUNNING':
                    item.update(state='INTERRUPTED', message='上次更新中断，可重新获取最新数据。', finished_at=timestamp())
                    db.execute('UPDATE data_updates SET body=? WHERE id=?', (encode(item), item['id']))

    def save(self, item):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO data_updates VALUES(?,?,?)',
                       (item['id'], item['created_at'], encode(item)))

    def get(self, identifier=''):
        with self.store.connect() as db:
            row = db.execute('SELECT body FROM data_updates WHERE id=?', (identifier,)).fetchone() if identifier else db.execute(
                'SELECT body FROM data_updates ORDER BY created_at DESC LIMIT 1').fetchone()
        if identifier and not row:
            raise ValueError('更新任务不存在')
        return json.loads(row['body']) if row else None

    def start(self):
        item = dict(id=str(uuid.uuid4()), created_at=timestamp(), state='RUNNING',
                    message='正在获取公开行情…', as_of='', issues=[], stats={})
        self.save(item)
        return item

    def execute(self, item, snapshot_getter=None, industry_getter=None):
        manager = None

        def progress(message):
            item['message'] = message
            if manager:
                item['stats'] = dict(manager.stats)
                item['recovery_events'] = list(manager.recovery_events)
            self.save(item)

        try:
            manager = MarketStore(self.store, item['id'], progress, force=True)
            snapshot = manager.snapshot(snapshot_getter, refresh=True)
            item.update(as_of=snapshot['as_of'], source=snapshot['source'], source_count=len(snapshot['rows']))
            for stock in snapshot['rows']:
                if 'ST' in stock['name'].upper() or '退' in stock['name']:
                    continue
                reason = quote_gap_reason(stock, snapshot['as_of'])
                if reason:
                    item['issues'].append(dict(code=stock['code'], name=stock['name'], reason=reason))
            progress('行情已保存，正在更新行业分类…')
            try:
                snapshot = manager.industry(snapshot, industry_getter, refresh=True)
                meta = snapshot['industry_data']
                item['industry_date'] = meta['observed_date']
                item['industry_matched'] = meta['matched']
                excluded = {s['code'] for s in snapshot['rows'] if 'ST' in s['name'].upper() or '退' in s['name']}
                item['issues'].extend(dict(row, reason='行业分类：' + row['reason'])
                                      for row in meta.get('unknown', []) + meta.get('conflicts', [])
                                      if row['code'] not in excluded)
                for code, error in meta.get('failed_groups', {}).items():
                    item['issues'].append(dict(code='', name='行业 '+code, reason=str(error.get('reason', error))))
            except Exception as exc:
                item['issues'].append(dict(code='', name='行业分类', reason=error_info(exc)['message']))
            item['state'] = 'PARTIAL' if item['issues'] else 'COMPLETED'
            item['message'] = ('部分更新完成' if item['issues'] else '更新完成') + '，行情日期 ' + item['as_of']
        except Exception as exc:
            item.update(state='FAILED', message='更新失败：' + error_info(exc)['message'], error=error_info(exc))
        finally:
            if manager:
                item['stats'] = dict(manager.stats)
                item['recovery_events'] = list(manager.recovery_events)
            item['finished_at'] = timestamp()
            self.save(item)
        return item
