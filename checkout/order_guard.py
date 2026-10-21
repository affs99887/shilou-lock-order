"""Durable one-shot submission ledger. No order numbers or personal data."""
import json
import os
import time
import uuid

class OrderGuard:
    def __init__(self, path):
        self.path = path
        self.record = json.loads(path.read_text()) if path.exists() else {}

    @property
    def blocked(self):
        return bool(self.record.get('status') in ('dispatching', 'unknown', 'awaiting_payment'))

    def write(self, status, **fields):
        record = {**self.record, **fields, 'status': status, 'updated_at': time.time()}
        tmp = self.path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, self.path)
        self.record = record

    def begin(self):
        if self.blocked:
            raise ValueError('上次订单已发出或结果未明；请先在 Apple 核对订单，不能重复提交')
        nonce = uuid.uuid4().hex
        self.write('dispatching', nonce=nonce)
        return nonce

    def acknowledge(self):
        self.write('acknowledged')
