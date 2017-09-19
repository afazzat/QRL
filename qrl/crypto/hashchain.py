from binascii import hexlify
from collections import namedtuple

from pyqrllib.pyqrllib import shake256, getHashChainSeed, str2bin, sha2_256, bin2hstr, hstr2bin
from qrl.core import config
from qrl.crypto.hmac_drbg import GEN, GEN_range
from qrl.crypto.misc import sha256

HashChainBundle = namedtuple('HashChainBundle', 'seed hashchain hc_terminator')


class HashChain(object):
    def __init__(self, seed_private, blocks_per_epoch=config.dev.blocks_per_epoch):
        # type: (str, int) -> None
        self._seed_private = seed_private
        self._blocks_per_epoch = blocks_per_epoch

    def _calc_hashchain(self, epoch):
        """
        generates a 20,000th hash in iterative sha256 chain..derived from private SEED
        :param epoch:
        :type epoch: int
        :return:
        """
        hc_seed = getHashChainSeed(self._seed_private, epoch, config.dev.hashchain_nums)

        hc = []
        for hash_chain in hc_seed:
            hc.append([hash_chain])

        hc_terminator = []
        for hash_chain in hc[:-1]:  # skip last element as it is reveal hash
            for x in range(self._blocks_per_epoch):
                hash_chain.append(sha2_256(32, hash_chain[-1]))
            hc_terminator.append(hash_chain[-1])

        # Reveal hash chain
        for hash_chain in hc[-1:]:
            # Extra hash to reveal one hash value
            for x in range(self._blocks_per_epoch + 1):
                hash_chain.append(sha2_256(32, hash_chain[-1]))
            hc_terminator.append(hash_chain[-1])

        return hc_seed, hc, hc_terminator

    def hashchain(self, epoch=0):
        # type: (int) -> HashChainBundle
        """

        :param epoch:
        :type epoch:
        :return:
        :rtype:
        >>> from binascii import unhexlify; isinstance(HashChain(hstr2bin('32eee808dc7c5dfe26fd4859b415e5a713bd764036bbeefd7a541da9a1cc7b9fcaf17da039a62756b63835de1769e05e')).hashchain(), HashChainBundle)
        True
        """
        return HashChainBundle(*self._calc_hashchain(epoch))

    def hashchain_reveal(self, epoch=0):
        """

        :param epoch:
        :type epoch:
        :return:
        :rtype:
        >>> from qrl.crypto.doctest_data import *; HashChain( hstr2bin('32eee808dc7c5dfe26fd4859b415e5a713bd764036bbeefd7a541da9a1cc7b9fcaf17da039a62756b63835de1769e05e')).hashchain_reveal() == hashchain_reveal_expected1
        True
        """
        hc_seed, hc, hc_terminator = self._calc_hashchain(epoch)
        return hc_terminator
