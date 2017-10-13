# coding=utf-8
# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php.

from abc import ABCMeta

import simplejson as json
from io import StringIO
from pyqrllib.pyqrllib import sha2_256, getAddress, bin2hstr, str2bin

from qrl.core import helper, config, logger
from qrl.core.Transaction_subtypes import *
from qrl.crypto.hashchain import hashchain_reveal
from qrl.crypto.misc import sha256
from qrl.crypto.xmss import XMSS


class Transaction(object, metaclass=ABCMeta):
    """
    Abstract Base class to be derived by all other transactions
    """

    # FIXME: Use metaclass and make this class abstract. Enforce same API in derived classes

    def __init__(self):
        # FIXME: at the moment, it is not possible to rename attributed because of the way things are serialized

        self.nonce = 0  # Nonce is set when block is being created
        self.ots_key = None
        self.txfrom = None  # FIXME: addr_from
        self.pubhash = None  # FIXME: public_hash
        self.txhash = None  # FIXME: transaction_hash

        self.PK = None  # FIXME: public_key
        self.signature = None  # FIXME: signature

    @staticmethod
    def tx_id_to_name(id):
        id_name = {
            1: 'TX',
            2: 'STAKE',
            3: 'COINBASE',
            4: 'LATTICE',
            5: 'DUPLICATE'
        }
        return id_name[id]

    @staticmethod
    def from_txdict(txdict):
        """
        :param txdict:
        :type txdict:
        :return:
        :rtype:
        """

        # TODO: This would probably make more sense in a factory. Wait for protobuf3
        # FIXME: Avoid dictionary lookups for a small fixed amount of keys
        type_to_txn = {
            TX_SUBTYPE_TX: SimpleTransaction,
            TX_SUBTYPE_STAKE: StakeTransaction,
            TX_SUBTYPE_COINBASE: CoinBase,
            TX_SUBTYPE_LATTICE: LatticePublicKey
        }

        subtype = txdict['subtype']

        return type_to_txn[subtype]()._dict_to_transaction(txdict)

    @staticmethod
    def generate_pubhash(pub, ots_key):
        # FIXME: Review this. Leon?
        return sha256(pub + tuple([int(char) for char in str(ots_key)]))

    def _get_pubhash(self):
        # FIXME: Review this. Leon?
        return self.generate_pubhash(self.PK, self.ots_key)

    def _get_txhash(self, tmptxhash, pubhash):
        # FIXME: Review this. Leon?
        self.pubhash = pubhash
        return sha2_256(tmptxhash + self.pubhash)

    def sign(self, xmss):
        self.signature = xmss.SIGN(self.txhash)

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
        self.subtype = dict_tx['subtype']

        self.ots_key = int(dict_tx['ots_key'])
        self.nonce = int(dict_tx['nonce'])
        self.txfrom = dict_tx['txfrom']

        self.pubhash = tuple(dict_tx['pubhash'])
        self.txhash = tuple(dict_tx['txhash'])

        self.PK = tuple(dict_tx['PK'])
        self.signature = tuple(dict_tx['signature'])

        return self

    def _reformat(self, srcList):
        destList = []
        if isinstance(srcList, list):
            for item in srcList:
                destList.append(self._reformat(item))
            return destList
        elif isinstance(srcList, str):
            return srcList

        return srcList

    def _validate_subtype(self, subtype, expected_subtype):
        if subtype != expected_subtype:
            logger.warning('Invalid subtype')
            logger.warning('Found: %s Expected: %s', subtype, expected_subtype)
            return False

        return True

    def get_message_hash(self):
        message = StringIO()
        # FIXME: This looks suspicious
        '''
        message.write(self.nonce)
        message.write(self.txfrom)
        message.write(self.txhash)
        message.write(self.signature)
        '''
        return message

    def transaction_to_json(self):
        return json.dumps(self.__dict__)

    def json_to_transaction(self, dict_tx):
        return self._dict_to_transaction(json.loads(dict_tx))


class SimpleTransaction(Transaction):
    """
    SimpleTransaction for the transaction of QRL from one wallet to another.
    """

    def __init__(self):  # nonce removed..
        super(SimpleTransaction, self).__init__()
        self.subtype = TX_SUBTYPE_TX

    def get_message_hash(self):
        message = super(SimpleTransaction, self).get_message_hash()
        # message.write(self.epoch)
        # message.write(self.txto)
        # message.write(self.amount)
        # message.write(self.fee)
        message.write(str(self.signature))
        message.write(str(self.txhash))
        return sha256(bytes(message.getvalue(), 'utf-8'))

    def _dict_to_transaction(self, dict_tx):
        super(SimpleTransaction, self)._dict_to_transaction(dict_tx)
        self.txto = dict_tx['txto']
        self.amount = int(dict_tx['amount'])
        self.fee = int(dict_tx['fee'])
        return self

    def pre_condition(self, tx_state):
        # if state_uptodate() is False:
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

    @staticmethod
    def create(addr_from, addr_to, amount, fee, xmss_pk, xmss_ots_key):
        transaction = SimpleTransaction()

        transaction.txfrom = addr_from
        transaction.txto = addr_to
        transaction.amount = int(amount)       # FIXME: Review conversions for quantities
        transaction.fee = int(fee)             # FIXME: Review conversions for quantities

        # FIXME: This is very confusing and can be a security risk
        # FIXME: Duplication. Risk of mismatch (create & verification)

        transaction.PK = xmss_pk
        transaction.ots_key = xmss_ots_key

        tmppubhash = transaction._get_pubhash()

        tmptxhash = sha256(bytes(''.join(transaction.txfrom +
                                         transaction.txto +
                                         str(transaction.amount) + str(transaction.fee)), 'utf-8'))

        transaction.txhash = transaction._get_txhash(tmptxhash, tmppubhash)

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

        # FIXME: Duplication. Risk of mismatch (create & verification)
        txhash = sha256(bytes(''.join(self.txfrom + self.txto + str(self.amount) + str(self.fee)), 'utf-8'))
        txhash = sha256(txhash + self.pubhash)

        # cryptographic checks
        if self.txhash != txhash:
            return False

        if not self._validate_signed_hash():
            return False

        return True

    # checks new tx validity based upon node statedb and node mempool.
    def state_validate_tx(self, tx_state, transaction_pool):

        if not self.pre_condition(tx_state):
            return False

        pubhash = self.generate_pubhash(self.PK, self.ots_key)

        tx_pubhashes = tx_state[2]

        if pubhash in tx_pubhashes:
            logger.info('1. State validation failed for %s because: OTS Public key re-use detected', self.txhash)
            return False

        for txn in transaction_pool:
            if txn.txhash == self.txhash:
                continue

            pubhashn = self.generate_pubhash(txn.PK, txn.ots_key)
            if pubhashn == pubhash:
                logger.info('2. State validation failed for %s because: OTS Public key re-use detected', self.txhash)
                return False

        return True


class StakeTransaction(Transaction):
    """
    StakeTransaction performed by the nodes who would like
    to stake.
    """

    def __init__(self):
        super(StakeTransaction, self).__init__()
        self.subtype = TX_SUBTYPE_STAKE

    def get_message_hash(self):
        """
        :return:
        :rtype:

        >>> s = StakeTransaction()
        >>> seed = [i for i in range(48)]
        >>> slave = XMSS(4, seed)
        >>> t = s.create(0, XMSS(4, seed), slave.pk(), None, slave.pk(), 10)
        >>> t.get_message_hash()
        (190, 216, 197, 106, 146, 168, 148, 15, 12, 106, 8, 196, 43, 74, 14, 144, 215, 198, 251, 97, 148, 8, 182, 151, 10, 227, 212, 134, 25, 11, 228, 245)
        """
        message = super(StakeTransaction, self).get_message_hash()
        # message.write(self.epoch)

        tmphash = ''.join([bin2hstr(b) for b in self.hash])
        message.write(tmphash)
        message.write(bin2hstr(self.first_hash))
        messagestr = message.getvalue()
        result = sha256(str2bin(messagestr))

        return result

    def _dict_to_transaction(self, dict_tx):
        super(StakeTransaction, self)._dict_to_transaction(dict_tx)
        self.epoch = int(dict_tx['epoch'])
        self.balance = dict_tx['balance']

        self.slave_public_key = tuple(dict_tx['slave_public_key'])

        self.hash = []

        for hash_item in dict_tx['hash']:
            self.hash.append(tuple(hash_item))

        self.first_hash = tuple(dict_tx['first_hash'])

        return self

    @staticmethod
    def create(blocknumber, xmss, slave_public_key, hashchain_terminator=None, first_hash=None, balance=None):
        """
        >>> s = StakeTransaction()
        >>> slave = XMSS(4)
        >>> isinstance(s.create(0, XMSS(4), slave.pk(), None, slave.pk(), 10), StakeTransaction)
        True
        """
        if not balance:
            logger.info('Invalid Balance %d', balance)
            raise Exception

        transaction = StakeTransaction()

        transaction.txfrom = xmss.get_address()

        transaction.slave_public_key = slave_public_key
        transaction.epoch = blocknumber // config.dev.blocks_per_epoch  # in this block the epoch is..
        transaction.balance = balance

        transaction.first_hash = first_hash
        if transaction.first_hash is None:
            transaction.first_hash = tuple()

        transaction.hash = hashchain_terminator
        if hashchain_terminator is None:
            transaction.hash = hashchain_reveal(xmss.get_seed_private(),
                                                epoch=transaction.epoch + 1)

        transaction.PK = xmss.pk()
        transaction.ots_key = xmss.get_index()

        tmppubhash = transaction._get_pubhash()

        tmptxhash = ''.join([bin2hstr(b) for b in transaction.hash])
        tmptxhash = str2bin(tmptxhash + bin2hstr(transaction.first_hash) + bin2hstr(transaction.slave_public_key))
        transaction.txhash = transaction._get_txhash(tmptxhash, tmppubhash)

        return transaction

    def validate_tx(self):
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

    def __init__(self):
        super(CoinBase, self).__init__()
        self.subtype = TX_SUBTYPE_COINBASE

    def _dict_to_transaction(self, dict_tx):
        super(CoinBase, self)._dict_to_transaction(dict_tx)
        self.txto = dict_tx['txto']
        self.amount = int(dict_tx['amount'])
        return self

    @staticmethod
    def create(blockheader, xmss):
        transaction = CoinBase()

        transaction.txfrom = blockheader.stake_selector
        transaction.txto = blockheader.stake_selector
        transaction.amount = blockheader.block_reward + blockheader.fee_reward

        transaction.PK = xmss.pk()
        transaction.ots_key = xmss.get_index()

        # FIXME: Duplication. Risk of mismatch (create & verification)
        tmppubhash = transaction._get_pubhash()

        tmptxhash = blockheader.prev_blockheaderhash + \
                    tuple([int(char) for char in str(blockheader.blocknumber)]) + \
                    blockheader.headerhash

        transaction.txhash = transaction._get_txhash(tmptxhash, tmppubhash)

        return transaction

    def validate_tx(self, chain, blockheader):
        sv_list = chain.block_chain_buffer.stake_list_get(blockheader.blocknumber)
        if blockheader.blocknumber > 1 and sv_list[self.txto].slave_public_key != self.PK:
            logger.warning('Stake validator doesnt own the Public key')
            logger.warning('Expected public key %s', sv_list[self.txto].slave_public_key)
            logger.warning('Found public key %s', self.PK)
            return False

        if self.txto != self.txfrom:
            logger.warning('Non matching txto and txfrom')
            logger.warning('txto: %s txfrom: %s', self.txto, self.txfrom)
            return False

        # FIXME: Duplication. Risk of mismatch (create & verification)
        txhash = blockheader.prev_blockheaderhash + \
                 tuple([int(char) for char in str(blockheader.blocknumber)]) + \
                 blockheader.headerhash

        # FIXME: This additional transformation happens in a base class
        txhash = sha256(txhash + self.pubhash)

        if self.txhash != txhash:
            logger.warning('Block_headerhash doesnt match')
            logger.warning('Found: %s', self.txhash)
            logger.warning('Expected: %s', txhash)
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

    def __init__(self):
        super(LatticePublicKey, self).__init__()
        self.subtype = TX_SUBTYPE_LATTICE
        self.kyber_pk = None
        self.tesla_pk = None

    def _dict_to_transaction(self, dict_tx):
        super(LatticePublicKey, self)._dict_to_transaction(dict_tx)
        return self

    @staticmethod
    def create(xmss, kyber_pk, tesla_pk):
        transaction = LatticePublicKey()

        transaction.txfrom = xmss.get_address()
        transaction.kyber_pk = kyber_pk
        transaction.tesla_pk = tesla_pk

        transaction.PK = xmss.pk()
        transaction.ots_key = xmss.get_index()

        # FIXME: Duplication. Risk of mismatch (create & verification)
        tmppubhash = transaction._get_pubhash()

        tmptxhash = sha256(transaction.kyber_pk + transaction.tesla_pk)

        transaction.txhash = transaction._get_txhash(tmptxhash, tmppubhash)

        return transaction

    def validate_tx(self):
        if not self._validate_subtype(self.subtype, TX_SUBTYPE_LATTICE):
            return False

        # FIXME: Duplication. Risk of mismatch (create & verification)
        txhash = sha256(self.kyber_pk + self.tesla_pk)
        txhash = sha256(txhash + self.pubhash)

        if self.txhash != txhash:
            logger.info('Invalid Txhash')
            logger.warning('Found: %s Expected: %s', self.txhash, txhash)
            return False

        if not self._validate_signed_hash():
            return False

        return True


class DuplicateTransaction:
    def __init__(self):
        self.blocknumber = 0
        self.prev_blockheaderhash = None

        self.coinbase1 = None
        self.headerhash1 = None

        self.coinbase2 = None
        self.headerhash2 = None

        self.subtype = TX_SUBTYPE_DUPLICATE

    def get_message_hash(self):
        return self.headerhash1 + self.headerhash2

    @staticmethod
    def create(block1, block2):
        transaction = DuplicateTransaction()

        transaction.blocknumber = block1.blockheader.blocknumber
        transaction.prev_blockheaderhash = block1.blockheader.prev_blockheaderhash

        transaction.coinbase1 = block1.transactions[0]
        transaction.headerhash1 = block1.blockheader.headerhash
        transaction.coinbase2 = block2.transactions[0]
        transaction.headerhash2 = block2.blockheader.headerhash

        return transaction

    def validate_tx(self):
        if self.headerhash1 == self.headerhash2 and self.coinbase1.signature == self.coinbase2.signature:
            logger.info('Invalid DT txn')
            logger.info('coinbase1 and coinbase2 txn are same')
            return

        if not self.validate_hash(self.headerhash1, self.coinbase1):
            return

        if not self.validate_hash(self.headerhash2, self.coinbase2):
            return

        return True

    def validate_hash(self, headerhash, coinbase):
        txhash = self.prev_blockheaderhash + tuple([int(char) for char in str(self.blocknumber)]) + headerhash
        txhash = sha256(txhash + coinbase.pubhash)

        if coinbase.txhash != txhash:
            logger.info('Invalid Txhash')
            logger.warning('Found: %s Expected: %s', coinbase.txhash, txhash)
            return False

        if not coinbase._validate_signed_hash(height=config.dev.slave_xmss_height):
            return False

        return True

    def to_json(self):
        return helper.json_encode_complex(self)

    def _dict_to_transaction(self, dict_tx):
        self.blocknumber = dict_tx['blocknumber']
        self.prev_blockheaderhash = tuple(dict_tx['prev_blockheaderhash'])

        self.coinbase1 = CoinBase()._dict_to_transaction(dict_tx['coinbase1'])
        self.headerhash1 = tuple(dict_tx['headerhash1'])

        self.coinbase2 = CoinBase()._dict_to_transaction(dict_tx['coinbase2'])
        self.headerhash2 = tuple(dict_tx['headerhash2'])

        return self

    def json_to_transaction(self, str_tx):
        return self._dict_to_transaction(json.loads(str_tx))

    def from_txdict(self, dict_tx):
        return self._dict_to_transaction(dict_tx)
