# coding=utf-8
# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php.

from abc import ABCMeta, abstractmethod

import simplejson as json
from io import StringIO
from pyqrllib.pyqrllib import sha2_256, getAddress, bin2hstr, str2bin

from qrl.core import helper, config, logger
from qrl.core.Transaction_subtypes import *
from qrl.crypto.hashchain import hashchain_reveal
from qrl.crypto.misc import sha256
from qrl.crypto.xmss import XMSS
from qrl.generated import qrl_pb2


class Transaction(object, metaclass=ABCMeta):
    """
    Abstract Base class to be derived by all other transactions
    """

    def __init__(self, protobuf_transaction=None):
        self._data = protobuf_transaction  # This object cointains persistable data
        if protobuf_transaction is None:
            self._data = qrl_pb2.Transaction()

    @property
    def pbdata(self):
        """
        Returns a protobuf object that contains persistable data representing this object
        :return: A protobuf Transaction object
        :rtype: qrl_pb2.Transaction
        """
        return self._data

    @property
    def subtype(self):
        return self._data.type

    @property
    def nonce(self):
        return self._data.nonce

    @property
    def txfrom(self):
        return self._data.addr_from

    @property
    def txto(self):
        return self._data.addr_to

    @property
    def pubhash(self):
        # FIXME: Review this. Leon?
        # FIXME: Avoid recalculating
        self._data.public_hash = bytes(sha256(bytes(self.PK) + str(self.ots_key).encode()))
        return self._data.public_hash

    @property
    def txhash(self):
        return self._data.transaction_hash

    @property
    def ots_key(self):
        return self._data.ots_key

    @property
    def amount(self):
        return self._data.amount

    @property
    def fee(self):
        return self._data.fee

    @property
    def PK(self):
        return self._data.public_key

    @property
    def signature(self):
        return self._data.signature

    @staticmethod
    def tx_id_to_name(id):
        # FIXME: Move to enums
        id_name = {
            qrl_pb2.Transaction.TRANSFER: 'TX',
            qrl_pb2.Transaction.STAKE: 'STAKE',
            qrl_pb2.Transaction.COINBASE: 'COINBASE',
            qrl_pb2.Transaction.LATTICE: 'LATTICE',
            qrl_pb2.Transaction.DUPLICATE: 'DUPLICATE'
        }
        return id_name[id]

    @staticmethod
    def from_txdict(dict_tx):
        # FIXME: Remove once we move completely to protobuf
        # TODO: This would probably make more sense in a factory. Wait for protobuf3
        type_to_txn = {
            qrl_pb2.Transaction.TRANSFER: SimpleTransaction,
            qrl_pb2.Transaction.STAKE: StakeTransaction,
            qrl_pb2.Transaction.COINBASE: CoinBase,
            qrl_pb2.Transaction.LATTICE: LatticePublicKey,
            qrl_pb2.Transaction.DUPLICATE: DuplicateTransaction
        }

        tmp_subtype = dict_tx['subtype']

        return type_to_txn[tmp_subtype]()._dict_to_transaction(dict_tx)

    @abstractmethod
    def _get_hashable_bytes(self):
        """
        This method should return bytes that are to be hashed and represent the transaction
        :return: hashable bytes
        :rtype: bytes
        """
        return bytes()

    def calculate_txhash(self):
        return bytes(sha2_256(self._get_hashable_bytes() + self.pubhash))

    def sign(self, xmss):
        self._data.signature = xmss.SIGN(self.txhash)

    def _validate_signed_hash(self, height=config.dev.xmss_tree_height):
        if self.subtype != TX_SUBTYPE_COINBASE and getAddress('Q', self.PK) != self.txfrom:
            logger.warning('Public key verification failed')
            return False

        if not XMSS.VERIFY(message=self.txhash,
                           signature=self.signature,
                           pk=self.PK,
                           height=height):
            logger.warning('xmss_verify failed')
            return False

        return True

    def _dict_to_transaction(self, dict_tx):
        # FIXME: Remove once we move completely to protobuf
        self._data.type = dict_tx['subtype']
        self._data.nonce = dict_tx['nonce']

        self._data.ots_key = int(dict_tx['ots_key'])
        self._data.nonce = int(dict_tx['nonce'])
        self._data.addr_from = bytes(dict_tx['txfrom'].encode())

        self._data.public_hash = bytes(dict_tx['pubhash'])
        self._data.transaction_hash = bytes(dict_tx['txhash'])

        self._data.public_key = bytes(dict_tx['PK'])
        self._data.signature = bytes(dict_tx['signature'])

        return self

    def _validate_subtype(self, subtype, expected_subtype):
        if subtype != expected_subtype:
            logger.warning('Invalid subtype')
            logger.warning('Found: %s Expected: %s', subtype, expected_subtype)
            return False

        return True

    def get_message_hash(self):
        # FIXME: refactor, review that things are not recalculated too often, cache, etc.
        return self.calculate_txhash()

    def transaction_to_json(self):
        # FIXME: Remove once we move completely to protobuf
        return json.dumps(self.__dict__)

    def json_to_transaction(self, dict_tx):
        # FIXME: Remove once we move completely to protobuf
        return self._dict_to_transaction(json.loads(dict_tx))


class SimpleTransaction(Transaction):
    """
    SimpleTransaction for the transaction of QRL from one wallet to another.
    """

    def __init__(self, protobuf_transaction=None):
        super(SimpleTransaction, self).__init__(protobuf_transaction)
        self._data.type = qrl_pb2.Transaction.TRANSFER

    def _dict_to_transaction(self, dict_tx):
        # FIXME: Remove once we move completely to protobuf
        super(SimpleTransaction, self)._dict_to_transaction(dict_tx)
        self._data.addr_to = bytes(dict_tx['txto'].encode())
        self._data.amount = int(dict_tx['amount'])
        self._data.fee = int(dict_tx['fee'])
        return self

    def pre_condition(self, tx_state):
        # if state.uptodate() is False:
        #	logger.info(( 'Warning state not updated to allow safe tx validation, tx validity could be unreliable..'))
        #	return False
        tx_balance = tx_state[1]

        if self.amount < 0:
            # FIXME: logging txhash here is not useful as this changes when signing
            logger.info('State validation failed for %s because: Negative send', self.txhash)
            return False

        if tx_balance < self.amount:
            # FIXME: logging txhash here is not useful as this changes when signing
            logger.info('State validation failed for %s because: Insufficient funds', self.txhash)
            logger.info('balance: %s, amount: %s', tx_balance, self.amount)
            return False

        return True

    def _get_hashable_bytes(self):
        """
        This method should return bytes that are to be hashed and represent the transaction
        :return: hashable bytes
        :rtype: bytes
        """
        tmptxhash = self.txfrom + \
                    self.txto + \
                    str(self.amount).encode() + \
                    str(self.fee).encode()
        return bytes(sha256(tmptxhash))

    @staticmethod
    def create(addr_from, addr_to, amount, fee, xmss_pk, xmss_ots_index):
        transaction = SimpleTransaction()

        transaction._data.addr_from = bytes(addr_from.encode())
        transaction._data.addr_to = bytes(addr_to.encode())
        transaction._data.amount = int(amount)              # FIXME: Review conversions for quantities
        transaction._data.fee = int(fee)                    # FIXME: Review conversions for quantities
        transaction._data.public_key = bytes(xmss_pk)
        transaction._data.ots_key = xmss_ots_index
        transaction._data.transaction_hash = transaction.calculate_txhash()

        return transaction

    def validate_tx(self):
        if self.subtype != TX_SUBTYPE_TX:
            return False

        # FIXME: what does this comment means?
        # sanity check: this is not how the economy is supposed to work!
        if self.amount <= 0:
            logger.info('State validation failed for %s because negative or zero', self.txhash)
            logger.info('Amount %d', self.amount)
            return False

        # cryptographic checks
        if self.txhash != self.calculate_txhash():
            return False

        if not self._validate_signed_hash():
            return False

        return True

    # checks new tx validity based upon node statedb and node mempool.
    def state_validate_tx(self, tx_state, transaction_pool):

        if not self.pre_condition(tx_state):
            return False

        pubhash = self.pubhash

        tx_pubhashes = tx_state[2]

        if pubhash in tx_pubhashes:
            logger.info('State validation failed for %s because: OTS Public key re-use detected', self.txhash)
            return False

        for txn in transaction_pool:
            if txn.txhash == self.txhash:
                continue

            if txn.pubhash == pubhash:
                logger.info('State validation failed for %s because: OTS Public key re-use detected', self.txhash)
                return False

        return True


class StakeTransaction(Transaction):
    """
    StakeTransaction performed by the nodes who would like
    to stake.
    """

    def __init__(self, protobuf_transaction=None):
        super(StakeTransaction, self).__init__(protobuf_transaction)
        self._data.type = qrl_pb2.Transaction.STAKE

    @property
    def epoch(self):
        return self._data.epoch

    @property
    def balance(self):
        return self._data.amount

    @property
    def slave_public_key(self):
        return self._data.public_key_slave

    @property
    def finalized_blocknumber(self):
        return self._data.finalized_blocknumber

    @property
    def finalized_headerhash(self):
        return self._data.finalized_headerhash

    @property
    def hash(self):
        return self._data.stake_hash

    @property
    def first_hash(self):
        # TODO: Review with cyyber
        return self._data.public_hash

    def _dict_to_transaction(self, dict_tx):
        # FIXME: Remove once we move completely to protobuf
        super(StakeTransaction, self)._dict_to_transaction(dict_tx)
        self._data.epoch = int(dict_tx['epoch'])
        self._data.amount = dict_tx['balance']

        self._data.public_key_slave = bytes(dict_tx['slave_public_key'])

        if 'finalized_blocknumber' not in dict_tx:
            # FIXME: Kept as warning to avoid crashes due to fork. Probably it should just fail
            logger.warning("finalized_blocknumber is not available")
        else:
            self._data.finalized_blocknumber = int(dict_tx['finalized_blocknumber'])

        if 'finalized_headerhash' not in dict_tx:
            # FIXME: Kept as warning to avoid crashes due to fork. Probably it should just fail
            logger.warning("finalized_headerhash is not available")
        else:
            self._data.finalized_headerhash = bytes(dict_tx['finalized_headerhash'])

        self._data.stake_hash[:] = [bytes(hash_item) for hash_item in dict_tx['hash']]

        # TODO: Review with cyyber
        self._data.public_hash = bytes(dict_tx['first_hash'])

        return self

    def _get_hashable_bytes(self):
        """
        This method should return bytes that are to be hashed and represent the transaction
        :return: hashable bytes
        :rtype: bytes
        """
        #FIXME: Avoid all intermediate conversions
        tmptxhash = ''.join([bin2hstr(b) for b in self.hash])
        tmptxhash = str2bin(tmptxhash
                            + bin2hstr(self.first_hash)
                            + bin2hstr(self.slave_public_key)
                            + bin2hstr(sha2_256(bytes(self.epoch)))
                            + bin2hstr(sha2_256(bytes(self.subtype)))
                            + bin2hstr(sha2_256(bytes(self.finalized_blocknumber)))
                            + bin2hstr(self.finalized_headerhash))
        return bytes(tmptxhash)

    @staticmethod
    def create(blocknumber,
               xmss,
               slave_public_key,
               finalized_blocknumber,
               finalized_headerhash,
               hashchain_terminator=None,
               first_hash=None,
               balance=None):
        """
        >>> s = StakeTransaction()
        >>> slave = XMSS(4)
        >>> isinstance(s.create(0, XMSS(4), slave.pk(), 0, bytes((0, 1)), None, slave.pk(), 10), StakeTransaction)
        True
        """
        if not balance:
            logger.info('Invalid Balance %d', balance)
            raise Exception

        transaction = StakeTransaction()

        transaction._data.addr_from = bytes(xmss.get_address().encode())
        transaction._data.public_key_slave = bytes(slave_public_key)
        transaction._data.finalized_blocknumber = finalized_blocknumber
        transaction._data.finalized_headerhash = bytes(finalized_headerhash)
        transaction._data.epoch = blocknumber // config.dev.blocks_per_epoch  # in this block the epoch is..
        transaction._data.amount = balance

        if first_hash is None:
            transaction._data.public_hash = bytes()
        else:
            transaction._data.public_hash = first_hash

        if hashchain_terminator is None:
            transaction._data.stake_hash[:] = hashchain_reveal(xmss.get_seed_private(), epoch=transaction.epoch + 1)
        else:
            transaction._data.stake_hash[:] = hashchain_terminator

        transaction._data.public_key = bytes(xmss.pk())
        transaction._data.ots_key = xmss.get_index()

        transaction._data.transaction_hash = transaction.calculate_txhash()

        return transaction

    def validate_tx(self):
        # FIX: Directly combine all this
        txhash = self.calculate_txhash()

        if txhash != self.txhash:
            logger.info('Invalid Transaction hash')
            return False

        if not self._validate_subtype(self.subtype, TX_SUBTYPE_STAKE):
            return False

        if not helper.isValidAddress(self.txfrom):
            logger.info('Invalid From Address %s', self.txfrom)
            return False

        if self.first_hash:
            hashterminator = sha256(self.first_hash)
            if hashterminator != self.hash[-1]:
                logger.info('First_hash doesnt stake to hashterminator')
                return False

        if not self._validate_signed_hash():
            return False

        return True

    def state_validate_tx(self, tx_state):
        if self.subtype != TX_SUBTYPE_STAKE:
            return False

        state_balance = tx_state[1]
        state_pubhashes = tx_state[2]

        if self.balance > state_balance:
            logger.info('Stake Transaction Balance exceeds maximum balance')
            logger.info('Max Balance Expected %d', state_balance)
            logger.info('Balance found %d', self.balance)
            return False

        # TODO no need to transmit pubhash over the network
        # pubhash has to be calculated by the receiver
        if self.pubhash in state_pubhashes:
            logger.info('State validation failed for %s because: OTS Public key re-use detected', self.hash)
            return False

        return True


class CoinBase(Transaction):
    """
    CoinBase is the type of transaction to credit the block_reward to
    the stake selector who created the block.
    """

    def __init__(self, protobuf_transaction=None):
        super(CoinBase, self).__init__(protobuf_transaction)
        self._data.type = qrl_pb2.Transaction.COINBASE

        # This attribute is not persistable
        self.blockheader = None

    def _dict_to_transaction(self, dict_tx):
        # FIXME: Remove once we move completely to protobuf
        super(CoinBase, self)._dict_to_transaction(dict_tx)
        self._data.addr_to = bytes(dict_tx['txto'].encode())
        self._data.amount = int(dict_tx['amount'])
        return self

    def _get_hashable_bytes(self):
        """
        This method should return bytes that are to be hashed and represent the transaction
        :return: hashable bytes
        :rtype: bytes
        """
        #FIXME: Avoid all intermediate conversions
        tmptxhash = bytes(self.blockheader.prev_blockheaderhash) + \
                    bytes(str(self.blockheader.blocknumber).encode()) + \
                    bytes(self.blockheader.headerhash)
        return bytes(sha256(tmptxhash))

    @staticmethod
    def create(blockheader, xmss):
        transaction = CoinBase()
        transaction.blockheader = blockheader

        transaction._data.addr_from = bytes(blockheader.stake_selector.encode())
        transaction._data.addr_to = bytes(blockheader.stake_selector.encode())
        transaction._data.amount = blockheader.block_reward + blockheader.fee_reward
        transaction._data.public_key = bytes(xmss.pk())
        transaction._data.ots_key = xmss.get_index()
        transaction._data.transaction_hash = transaction.calculate_txhash()

        return transaction

    def validate_tx(self, chain, blockheader):
        sv_list = chain.block_chain_buffer.stake_list_get(blockheader.blocknumber)
        if blockheader.blocknumber > 1 and sv_list[self.txto].slave_public_key != self.PK:
            logger.warning('Stake validator doesnt own the Public key')
            logger.warning('Expected public key %s', sv_list[self.txto].slave_public_key)
            logger.warning('Found public key %s', self.PK)
            return False

        self.blockheader = blockheader

        if self.txto != self.txfrom:
            logger.warning('Non matching txto and txfrom')
            logger.warning('txto: %s txfrom: %s', self.txto, self.txfrom)
            return False

        tmp_txhash = self.calculate_txhash()
        if self.txhash != self.calculate_txhash():
            logger.warning('Block_headerhash doesnt match')
            logger.warning('Found: %s', self.txhash)
            logger.warning('Expected: %s', tmp_txhash)
            return False

        # Slave XMSS is used to sign COINBASE txn having quite low XMSS height
        if not self._validate_signed_hash(height=config.dev.slave_xmss_height):
            return False

        return True


class LatticePublicKey(Transaction):
    """
    LatticePublicKey transaction to store the public key.
    This transaction has been designed for Ephemeral Messaging.
    """

    def __init__(self, protobuf_transaction=None):
        super(LatticePublicKey, self).__init__(protobuf_transaction)
        self._data.type = qrl_pb2.Transaction.LATTICE
        self._data.pk_kyber = None
        self._data.pk_tesla = None

    @property
    def kyber_pk(self):
        return self._data.pk_kyber

    @property
    def tesla_pk(self):
        return self._data.pk_tesla

    def _dict_to_transaction(self, dict_tx):
        # FIXME: Remove once we move completely to protobuf
        super(LatticePublicKey, self)._dict_to_transaction(dict_tx)
        return self

    def _get_hashable_bytes(self):
        """
        This method should return bytes that are to be hashed and represent the transaction
        :return: hashable bytes
        :rtype: bytes
        """
        tmptxhash = self.kyber_pk + \
                    self.tesla_pk

        return bytes(sha256(tmptxhash))

    @staticmethod
    def create(xmss, kyber_pk, tesla_pk):
        transaction = LatticePublicKey()

        transaction._data.txfrom = xmss.get_address()
        transaction._data.kyber_pk = kyber_pk
        transaction._data.tesla_pk = tesla_pk
        transaction._data.public_key = xmss.pk()
        transaction._data.ots_key = xmss.get_index()
        transaction._data.transaction_hash = transaction.calculate_txhash()

        return transaction

    def validate_tx(self):
        if not self._validate_subtype(self.subtype, TX_SUBTYPE_LATTICE):
            return False

        txhash = self.calculate_txhash()
        if self.txhash != txhash:
            logger.info('Invalid Txhash')
            logger.warning('Found: %s Expected: %s', self.txhash, txhash)
            return False

        if not self._validate_signed_hash():
            return False

        return True


class DuplicateTransaction(Transaction):
    def __init__(self, protobuf_transaction=None):
        super(DuplicateTransaction, self).__init__(protobuf_transaction)
        self._data.type = qrl_pb2.Transaction.DUPLICATE

        self._data.dup_block_number = 0
        self._data.dup_hash_header_prev = None

        self._data.dup_coinbase1 = None
        self._data.dup_coinbase1_hhash = None

        self._data.dup_coinbase2 = None
        self._data.dup_coinbase2_hhash = None

        # TODO: review, this is not persistable
        self.headerhash = None
        self.coinbase = None

    @property
    def headerhash1(self):
        return self._data.dup_coinbase1_hhash

    def headerhash2(self):
        return self._data.dup_coinbase2_hhash

    @property
    def coinbase1(self):
        return self._data.dup_coinbase1

    @property
    def coinbase2(self):
        return self._data.dup_coinbase2

    # def get_message_hash(self):
    #     return self.headerhash1 + self.headerhash2

    def _get_hashable_bytes(self):
        """
        This method should return bytes that are to be hashed and represent the transaction
        :return: hashable bytes
        :rtype: bytes
        """
        # FIXME: Avoid all intermediate conversions
        # TODO: Review get_message_hash is too different/inconsistent
        tmptxhash = bytes(self.prev_blockheaderhash) + \
                    bytes(str(self.blocknumber).encode()) + \
                    bytes(self.headerhash) + \
                    bytes(self.coinbase.pubhash)

        return bytes(sha256(tmptxhash))

    @staticmethod
    def create(block1, block2):
        transaction = DuplicateTransaction()

        transaction.blocknumber = block1.blockheader.blocknumber
        transaction.prev_blockheaderhash = block1.blockheader.prev_blockheaderhash

        transaction._data.dup_coinbase1 = block1.transactions[0]
        transaction._data.dup_coinbase1_hhash = block1.blockheader.headerhash
        transaction._data.dup_coinbase2 = block2.transactions[0]
        transaction._data.dup_coinbase2_hhash = block2.blockheader.headerhash

        return transaction

    def validate_tx(self):
        if self.headerhash1 == self.headerhash2 and self.coinbase1.signature == self.coinbase2.signature:
            logger.info('Invalid DT txn')
            logger.info('coinbase1 and coinbase2 txn are same')
            return False

        if not self.validate_hash(self.headerhash1, self.coinbase1):
            return False

        if not self.validate_hash(self.headerhash2, self.coinbase2):
            return False

        return True

    def validate_hash(self, headerhash, coinbase):
        self.headerhash = headerhash
        self.coinbase = coinbase

        txhash = self.calculate_txhash()

        if coinbase.txhash != txhash:
            logger.info('Invalid Txhash')
            logger.warning('Found: %s Expected: %s', coinbase.txhash, txhash)
            return False

        if not coinbase._validate_signed_hash(height=config.dev.slave_xmss_height):
            return False

        return True

    def from_txdict(self, dict_tx):
        # FIXME: Remove once we move completely to protobuf
        return self._dict_to_transaction(dict_tx)

    def _dict_to_transaction(self, dict_tx):
        # FIXME: Remove once we move completely to protobuf
        self.blocknumber = dict_tx['blocknumber']
        self.prev_blockheaderhash = bytes(dict_tx['prev_blockheaderhash'])

        self._data.dup_coinbase1 = CoinBase()._dict_to_transaction(dict_tx['coinbase1'])
        self._data.dup_coinbase1_hhash = bytes(dict_tx['headerhash1'])

        self._data.dup_coinbase2 = CoinBase()._dict_to_transaction(dict_tx['coinbase2'])
        self._data.dup_coinbase2_hhash = bytes(dict_tx['headerhash2'])

        return self

    def to_json(self):
        # FIXME: Remove once we move completely to protobuf
        return helper.json_encode_complex(self)

    def json_to_transaction(self, str_tx):
        # FIXME: Remove once we move completely to protobuf
        return self._dict_to_transaction(json.loads(str_tx))
