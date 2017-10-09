import os

from qrl.core import config, logger
from qrl.generated import qrl_pb2


class QRLNode:
    def __init__(self):
        self.peer_addresses = []
        self.peers_path = os.path.join(config.user.data_path, config.dev.peers_filename)
        self.load_peer_addresses()

    def load_peer_addresses(self):
        if os.path.isfile(self.peers_path):
            logger.info('Opening peers.qrl')
            with open(self.peers_path, 'rb') as infile:
                known_peers = qrl_pb2.KnownPeers()
                known_peers.ParseFromString(infile.read())
                self.peer_addresses = [peer.ip for peer in known_peers.peers]
        else:
            logger.info('Creating peers.qrl')
            # Ensure the data path exists
            config.create_path(config.user.data_path)
            self.update_peer_addresses(config.user.peer_list)

        logger.info('Known Peers: %s', self.peer_addresses)

    def update_peer_addresses(self, peer_addresses):
        # FIXME: Probably will be refactored
        self.peer_addresses = peer_addresses
        known_peers = qrl_pb2.KnownPeers()
        known_peers.peers.extend([qrl_pb2.Peer(ip=p) for p in self.peer_addresses])
        with open(self.peers_path, "wb") as outfile:
            outfile.write(known_peers.SerializeToString())

