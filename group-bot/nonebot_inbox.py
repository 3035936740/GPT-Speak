"""Bounded business event inbox, independent of the OneBot transport."""
from collections import deque
import uuid


class Inbox:
    def __init__(self, limit=8192):
        self.rows = deque(maxlen=limit)
        self.sequence = 0
        self.epoch = uuid.uuid4().hex
        self.online = False

    def reset(self, online):
        self.rows.clear()
        self.epoch = uuid.uuid4().hex
        self.online = online

    def append(self, event):
        self.sequence += 1
        self.rows.append((self.sequence, event))

    def read(self, epoch, cursor):
        if not self.online:
            raise ConnectionError('QQ reverse connection is offline')
        # A new worker or connection takes a baseline. No replay after reconnect.
        if epoch != self.epoch:
            return dict(epoch=self.epoch, cursor=self.sequence, messages=[])
        if self.rows and cursor < self.rows[0][0] - 1:
            raise OverflowError('Reverse event inbox overflow; establish a new baseline')
        return dict(epoch=self.epoch, cursor=self.sequence,
                    messages=[row for seq, row in self.rows if seq > cursor])

