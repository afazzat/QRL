# coding=utf-8
# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php.

# FIXME: This is odd...
from grpc._cython.cygrpc import StatusCode

from qrl.core import logger
from qrl.core.qrlnode import QRLNode
from qrl.generated import qrl_pb2
from qrl.generated.qrl_pb2_grpc import PublicAPIServicer


class APIService(PublicAPIServicer):
    # TODO: Separate the Service from the node model
    def __init__(self, qrlnode: QRLNode):
        self.qrlnode = qrlnode

    def GetKnownPeers(self, request: qrl_pb2.GetKnownPeersReq, context) -> qrl_pb2.GetKnownPeersResp:
        known_peers = qrl_pb2.KnownPeers()
        known_peers.peers.extend([qrl_pb2.Peer(ip=p) for p in self.qrlnode.peer_addresses])
        return qrl_pb2.GetKnownPeersResp(known_peers=known_peers)

    def GetAddressState(self, request: qrl_pb2.GetAddressStateReq, context) -> qrl_pb2.GetAddressStateResp:
        try:
            address_state = self.qrlnode.get_address_state(request.address)
        except Exception as e:
            context.set_code(StatusCode.not_found)
            context.set_details(e)
            return None
        return qrl_pb2.GetAddressStateResp(state=address_state)

    def TransferCoins(self, request: qrl_pb2.TransferCoinsReq, context) -> qrl_pb2.TransferCoinsResp:
        logger.debug("[QRLNode] TransferCoins")
        self.qrlnode.transfer_coins()
        return qrl_pb2.TransferCoinsResp()