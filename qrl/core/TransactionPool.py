# coding=utf-8
# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php.

import heapq

from pyqrllib.pyqrllib import QRLHelper, sha2_256

from qrl.core import config
from qrl.core.Block import Block
from qrl.core.Transaction import Transaction


class TransactionPool:
    # FIXME: Remove tx pool from all method names
    def __init__(self):
        self.pending_tx_pool = []
        self.pending_tx_pool_hash = set()
        self.transaction_pool = []  # FIXME: Everyone is touching this
        self.address_ots_hash = set()

    @property
    def transactions(self):
        return heapq.nlargest(len(self.transaction_pool), self.transaction_pool)

    @staticmethod
    def calc_addr_ots_hash(tx):
        addr = tx.master_addr
        if not addr:
            addr = bytes(QRLHelper.getAddress(tx.PK))

        addr_ots_hash = sha2_256(addr + tx.ots_key.to_bytes(8, byteorder='big', signed=False))

        return addr_ots_hash

    def append_addr_ots_hash(self, tx: Transaction):
        addr_ots_hash = self.calc_addr_ots_hash(tx)
        self.address_ots_hash.add(addr_ots_hash)

    def get_pending_transaction(self):
        if len(self.pending_tx_pool_hash) == 0:
            return None
        pending_tx_set = heapq.heappop(self.pending_tx_pool)
        pending_tx = pending_tx_set[1]
        self.pending_tx_pool_hash.remove(pending_tx.txhash)

        addr_ots_hash = self.calc_addr_ots_hash(pending_tx.tx)
        self.address_ots_hash.remove(addr_ots_hash)

        return pending_tx

    def is_full_transaction_pool(self) -> bool:
        if len(self.transaction_pool) + len(self.pending_tx_pool) >= config.dev.transaction_pool_size:
            return True

        return False

    def update_pending_tx_pool(self, tx: Transaction, ip) -> bool:
        if self.is_full_transaction_pool():
            return False

        addr_ots_hash = self.calc_addr_ots_hash(tx)

        if addr_ots_hash in self.address_ots_hash:
            return False

        idx = self.get_tx_index_from_pool(tx.txhash)
        if idx > -1:
            return False

        if tx.txhash in self.pending_tx_pool_hash:
            return False

        self.address_ots_hash.add(addr_ots_hash)

        # Since its a min heap giving priority to lower number
        # So -1 multiplied to give higher priority to higher txn
        heapq.heappush(self.pending_tx_pool, [tx.fee * -1, tx, ip])
        self.pending_tx_pool_hash.add(tx.txhash)

        return True

    def add_tx_to_pool(self, tx_class_obj) -> bool:
        if self.is_full_transaction_pool():
            return False

        heapq.heappush(self.transaction_pool, [tx_class_obj.fee, tx_class_obj])
        return True

    def get_tx_index_from_pool(self, txhash):
        for i in range(len(self.transaction_pool)):
            txn = self.transaction_pool[i][1]
            if txhash == txn.txhash:
                return i

        return -1

    def remove_tx_from_pool(self, tx: Transaction):
        idx = self.get_tx_index_from_pool(tx.txhash)
        if idx > -1:
            del self.transaction_pool[idx]

            addr_ots_hash = self.calc_addr_ots_hash(tx)
            self.address_ots_hash.remove(addr_ots_hash)

            heapq.heapify(self.transaction_pool)

    def remove_tx_in_block_from_pool(self, block_obj: Block):
        for protobuf_tx in block_obj.transactions:
            tx = Transaction.from_pbdata(protobuf_tx)
            idx = self.get_tx_index_from_pool(tx.txhash)
            if idx > -1:
                del self.transaction_pool[idx]

                addr_ots_hash = self.calc_addr_ots_hash(tx)
                self.address_ots_hash.remove(addr_ots_hash)

        heapq.heapify(self.transaction_pool)
