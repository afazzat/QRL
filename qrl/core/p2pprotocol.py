# coding=utf-8
import json
import struct
import time
from decimal import Decimal

from twisted.internet import reactor
from twisted.internet.protocol import Protocol, connectionDone

from pyqrllib.pyqrllib import bin2hstr, hstr2bin, str2bin
from qrl.core import helper, config, logger, fork
from qrl.core.block import Block
from qrl.core.doctest_data import wrap_message_expected1
from qrl.core.messagereceipt import MessageReceipt
from qrl.core.nstate import NState
from qrl.core.Transaction import StakeTransaction, SimpleTransaction
from qrl.crypto.misc import sha256
from qrl.core.processors.TxnProcessor import TxnProcessor


class P2PProtocol(Protocol):
    def __init__(self):
        # TODO: Comment with some names the services
        self.service = {'reboot': self.reboot,
                        'MR': self.MR,
                        # 'RFM': self.RFM, only for internal usage
                        'SFM': self.SFM,
                        'TX': self.TX,
                        'ST': self.ST,
                        'BM': self.BM,
                        'BK': self.BK,
                        'PBB': self.PBB,
                        'PB': self.PB,
                        'PH': self.PH,
                        'LB': self.LB,
                        'FMBH': self.FMBH,
                        'PMBH': self.PMBH,
                        'MB': self.MB,
                        'CB': self.CB,
                        'BN': self.BN,
                        'FB': self.FB,
                        'FH': self.FH,
                        'PO': self.PO,
                        'PI': self.PI,
                        'PL': self.PL,
                        'RT': self.RT,
                        'PE': self.PE,
                        'VE': self.VE,
                        'R1': self.R1,
                        'IP': self.IP,
                        }
        self.buffer = b''
        self.messages = []
        self.conn_identity = None
        self.blockheight = None
        self.version = ''
        self.blocknumber_headerhash = {}
        self.last_requested_blocknum = None
        self.fetch_tried = 0
        pass

    def parse_msg(self, data):
        try:
            jdata = json.loads(data.decode())
        except Exception as e:
            logger.warning("parse_msg [json] %s", e)
            return

        func = jdata['type']

        if func not in self.service:
            return

        func = self.service[func]
        try:
            if 'data' in jdata:
                func(jdata['data'])
            else:
                func()
        except Exception as e:
            logger.error("parse_msg [%s] \n%s", func, e)

    def reboot(self, data):
        hash_dict = json.loads(data)
        if not ('hash' in hash_dict and 'nonce' in hash_dict and 'blocknumber' in hash_dict):
            return
        status, error = self.factory.chain.validate_reboot(hash_dict['hash'], hash_dict['nonce'])
        if not status:
            logger.info('status %s', status)
            logger.info('error %s', error)
            return
        for peer in self.factory.peers:
            if peer != self:
                peer.transport.write(self.wrap_message('reboot', data))
        reboot_data = ['2920c8ec34f04f59b7df4284a4b41ca8cbec82ccdde331dd2d64cc89156af653', hash_dict['nonce']]
        self.factory.chain.state.db.put('reboot_data', reboot_data)
        blocknumber = hash_dict['blocknumber']
        logger.info('Initiating Reboot Sequence..... #%s', blocknumber)
        if blocknumber != 0:
            if blocknumber <= self.factory.chain.height():
                self.factory.pos.update_node_state(NState.unsynced)
                del self.factory.chain.m_blockchain[blocknumber:]
                self.factory.chain.f_write_m_blockchain()
                self.factory.chain.m_load_chain()
                self.factory.pos.update_node_state(NState.synced)

    def MR(self, data):
        """
        Message Receipt
        This function accepts message receipt from peer,
        checks if the message hash already been received or not.
        In case its a already received message, it is ignored.
        Otherwise the request is made to get the full message.
        :return:
        """
        data = json.loads(data)
        if data['type'] not in MessageReceipt.allowed_types:
            return

        if data['type'] in ['R1', 'TX'] and self.factory.nodeState.state != NState.synced:
            return

        if data['type'] == 'TX' and len(self.factory.chain.pending_tx_pool) >= config.dev.transaction_pool_size:
            logger.warning('TX pool size full, incoming tx dropped. mr hash: %s', data['hash'])
            return

        if data['type'] == 'ST' and self.factory.chain.height() > 1 and self.factory.nodeState.state != NState.synced:
            return

        if self.factory.master_mr.peer_contains_hash(data['hash'], data['type'], self):
            return

        self.factory.master_mr.add_peer(data['hash'], data['type'], self)

        if data['hash'] in self.factory.master_mr.hash_callLater:  # Ignore if already requested
            return

        if self.factory.master_mr.contains(data['hash'], data['type']):
            return

        if data['type'] == 'BK':
            if not self.factory.chain.block_chain_buffer.verify_BK_hash(data, self.conn_identity):
                self.factory.master_mr.deregister(data['hash'], data['type'])
                return

        self.RFM(data)

    def RFM(self, data):
        """
        Request Full Message
        This function request for the full message against,
        the Message Receipt received.
        :return:
        """
        msg_hash = data['hash']
        if msg_hash in self.factory.master_mr.hash_msg:
            if msg_hash in self.factory.master_mr.hash_callLater:
                del self.factory.master_mr.hash_callLater[msg_hash]
            return
        for peer in self.factory.master_mr.hash_peer[msg_hash]:
            if peer not in self.factory.master_mr.requested_hash[msg_hash]:
                self.factory.master_mr.requested_hash[msg_hash].append(peer)
                # Storing MR params, so the received full message could be checked against
                # the MR receipt provided.
                self.factory.master_mr.hash_params[msg_hash] = data
                peer.transport.write(self.wrap_message('SFM', helper.json_encode(data)))
                call_later_obj = reactor.callLater(config.dev.message_receipt_timeout,
                                                   self.RFM,
                                                   data)
                self.factory.master_mr.hash_callLater[msg_hash] = call_later_obj
                return

        # If executing reach to this line, then it means no peer was able to provide
        # Full message for this hash thus the hash has to be deleted.
        # Moreover, negative points could be added to the peers, for this behavior
        if msg_hash in self.factory.master_mr.hash_callLater:
            del self.factory.master_mr.hash_callLater[msg_hash]

    def SFM(self, data):  # Send full message
        """
        Send Full Message
        This function serves the request made for the full message.
        :return:
        """
        data = json.loads(data)
        msg_hash = data['hash']
        msg_type = data['type']
        if not self.factory.master_mr.contains(msg_hash, msg_type):
            return

        # Sending message from node, doesn't guarantee that peer has received it.
        # Thus requesting peer could re request it, may be ACK would be required
        # To confirm, if the peer has received, otherwise X number of maximum retry
        # if self.factory.master_mr.peer_contains_hash(msg_hash, msg_type, self):
        #    return

        self.transport.write(self.wrap_message(msg_type,
                                               self.factory.master_mr.hash_msg[msg_hash]))

        self.factory.master_mr.add_peer(msg_hash, msg_type, self)

    def broadcast(self, msg_hash, msg_type):  # Move to factory
        """
        Broadcast
        This function sends the Message Receipt to all connected peers.
        :return:
        """
        data = {'hash': sha256(str(msg_hash)),
                'type': msg_type}

        for peer in self.factory.peer_connections:
            if peer not in self.factory.master_mr.hash_peer[data['hash']]:
                peer.transport.write(self.wrap_message('MR', helper.json_encode(data)))

    def TX(self, data):  # tx received..
        """
        Transaction
        Executed whenever a new TX type message is received.
        :return:
        """
        self.recv_tx(data)
        return

    def ST(self, data):
        """
        Stake Transaction
        This function processes whenever a Transaction having
        subtype ST is received.
        :return:
        """
        try:
            st = StakeTransaction().json_to_transaction(data)
        except Exception as e:
            logger.error('st rejected - unable to decode serialised data - closing connection')
            logger.exception(e)
            self.transport.loseConnection()
            return

        if not self.factory.master_mr.isRequested(st.get_message_hash(), self):
            return

        if len(
                self.factory.chain.m_blockchain) == 1 and st.epoch > 0:  # catch error for new nodes listening for ST's from later epochs
            return

        for t in self.factory.chain.transaction_pool:
            if st.get_message_hash() == t.get_message_hash():
                return
        # logger.info('--> %s %s',self.factory.chain.block_chain_buffer.height(), self.factory.chain.height())
        tx_state = self.factory.chain.block_chain_buffer.get_stxn_state(
            blocknumber=self.factory.chain.block_chain_buffer.height() + 1,
            addr=st.txfrom)
        if st.validate_tx() and st.state_validate_tx(tx_state=tx_state):
            self.factory.chain.add_tx_to_pool(st)
        else:
            logger.warning('>>>ST %s invalid state validation failed..', st.hash)
            return

        self.factory.master_mr.register(st.get_message_hash(), st.transaction_to_json(), 'ST')
        self.broadcast(st.get_message_hash(), 'ST')
        return

    def BM(self, data=None):  # blockheight map for synchronisation and error correction prior to POS cycle resync..
        """
        Blockheight Map
        Simply maps the peer with their respective blockheight.
        If no data is provided in parameter, the node sends its 
        own current blockheight. 
        :return:
        """
        if not data:
            logger.info('<<< Sending block_map %s', self.transport.getPeer().host)

            z = {'block_number': self.factory.chain.m_blockchain[-1].blockheader.blocknumber,
                 'headerhash': self.factory.chain.m_blockchain[-1].blockheader.headerhash}

            self.transport.write(self.wrap_message('BM', helper.json_encode(z)))
            return
        else:
            logger.info('>>> Receiving block_map')
            z = helper.json_decode(data)
            block_number = z['block_number']
            headerhash = z['headerhash'].encode('latin1')

            i = [block_number, headerhash, self.transport.getPeer().host]
            logger.info('%s', i)
            if i not in self.factory.chain.blockheight_map:
                self.factory.chain.blockheight_map.append(i)
            return

    def BK(self, data):  # block received
        """
        Block
        This function processes any new block received.
        :return:
        """
        try:
            block = Block.from_json(data)
        except Exception as e:
            logger.error('block rejected - unable to decode serialised data %s', self.transport.getPeer().host)
            logger.exception(e)
            return
        logger.info('>>>Received block from %s %s %s',
                    self.conn_identity,
                    block.blockheader.blocknumber,
                    block.blockheader.stake_selector)
        if not self.factory.master_mr.isRequested(block.blockheader.headerhash, self, block):
            return

        self.factory.pos.pre_block_logic(block)
        self.factory.master_mr.register(block.blockheader.headerhash, data, 'BK')
        self.broadcast(block.blockheader.headerhash, 'BK')
        return

    def isNoMoreBlock(self, data):
        if type(data) == int:
            blocknumber = data
            if blocknumber != self.last_requested_blocknum:
                return True
            try:
                reactor.download_monitor.cancel()
            except:
                pass
            self.factory.pos.update_node_state(NState.synced)
            return True
        return False

    def PBB(self, data):
        """
        Push Block Buffer
        This function executes while syncing block from other peers.
        Blocks received by this function, directly added into
        chain.block_chain_buffer.
        So it is expected to receive multiple of blocks having
        same blocknumber.
        :return:
        """
        self.factory.pos.last_pb_time = time.time()
        try:
            if self.isNoMoreBlock(data):
                return

            data = helper.json_decode(data)
            blocknumber = int(list(data.keys())[0].encode('ascii'))

            if blocknumber != self.last_requested_blocknum:
                logger.info('Blocknumber not found in pending_blocks %s %s', blocknumber, self.conn_identity)
                return

            for jsonBlock in data[str(blocknumber)]:
                block = Block.from_json(json.dumps(jsonBlock))
                logger.info('>>>Received Block #%s', block.blockheader.blocknumber)

                status = self.factory.chain.block_chain_buffer.add_block(block)
                if type(status) == bool and not status:
                    logger.info("[PBB] Failed to add block by add_block, re-requesting the block #%s", blocknumber)
                    logger.info('Skipping one block')
                    continue

            try:
                reactor.download_block.cancel()
            except Exception:
                pass

            # Below code is to stop downloading, once we see that we reached to blocknumber that are in pending_blocks
            # This could be exploited by sybil node, to send blocks in pending_blocks in order to disrupt downloading
            # TODO: required a better fix
            if len(self.factory.chain.block_chain_buffer.pending_blocks) > 0 and min(
                    self.factory.chain.block_chain_buffer.pending_blocks.keys()) == blocknumber:
                self.factory.chain.block_chain_buffer.process_pending_blocks()
                return
            self.factory.pos.randomize_block_fetch(blocknumber + 1)
        except Exception as e:
            logger.error('block rejected - unable to decode serialised data %s', self.transport.getPeer().host)
            logger.exception(e)
        return

    def PB(self, data):
        """
        Push Block
        This function processes requested blocks received while syncing.
        Block received under this function are directly added to the main
        chain i.e. chain.m_blockchain
        It is expected to receive only one block for a given blocknumber.
        :return:
        """
        self.factory.pos.last_pb_time = time.time()
        try:
            if self.isNoMoreBlock(data):
                return

            block = Block.from_json(data)

            blocknumber = block.blockheader.blocknumber
            logger.info('>>> Received Block #%d', blocknumber)
            if blocknumber != self.last_requested_blocknum:
                logger.warning('Did not match %s %s', self.last_requested_blocknum, self.conn_identity)
                return

            if blocknumber > self.factory.chain.height():
                if not self.factory.chain.add_block_mainchain(block):
                    logger.warning('PB failed to add block to mainchain')
                    return

            try:
                reactor.download_monitor.cancel()
            except Exception as e:
                logger.warning("PB: %s", e)

            self.factory.pos.randomize_block_fetch(blocknumber + 1)

        except Exception as e:
            logger.error('block rejected - unable to decode serialised data %s', self.transport.getPeer().host)
            logger.exception(e)
        return

    def PH(self, data):
        """
        Push Headerhash
        :return:
        """
        if self.factory.nodeState.state == NState.forked:
            fork.verify(data, self.conn_identity, self.chain, self.randomize_headerhash_fetch)
        else:
            mini_block = json.loads(data)
            self.blocknumber_headerhash[mini_block['blocknumber']] = mini_block['headerhash']
        return

    def LB(self):  # request for last block to be sent
        """
        Last BLock
        Sends the last block from the main chain.
        :return:
        """
        logger.info('<<<Sending last block %s %s bytes to node %s', self.factory.chain.m_blockheight(),
                    str(len(helper.json_bytestream(self.factory.chain.m_get_last_block()))),
                    self.transport.getPeer().host)

        self.transport.write(self.wrap_message('BK', helper.json_bytestream_bk(self.factory.chain.m_get_last_block())))
        return

    def FMBH(self):  # Fetch Maximum Blockheight and Headerhash
        """
        Fetch Maximum Blockheight and HeaderHash
        Serves the fetch request for maximum blockheight & headerhash
        Sends the current blockheight and the headerhash of the 
        last block in mainchain.
        :return:
        """
        if self.factory.pos.nodeState.state != NState.synced:
            return
        logger.info('<<<Sending blockheight and headerhash to: %s %s', self.transport.getPeer().host, str(time.time()))

        data = {'headerhash': self.factory.chain.m_blockchain[-1].blockheader.headerhash,
                'blocknumber': self.factory.chain.m_blockchain[-1].blockheader.blocknumber}

        self.transport.write(self.wrap_message('PMBH', helper.json_encode(data)))
        return

    def PMBH(self, data):  # Push Maximum Blockheight and Headerhash
        """
        Push Maximum Blockheight and Headerhash
        Function processes, received maximum blockheight and headerhash.
        :return:
        """
        data = helper.json_decode(data)
        if not data or 'headerhash' not in data or 'blocknumber' not in data:
            return

        if self.conn_identity in self.factory.pos.fmbh_allowed_peers:
            self.factory.pos.fmbh_allowed_peers[self.conn_identity] = data
            if data['headerhash'] not in self.factory.pos.fmbh_blockhash_peers:
                self.factory.pos.fmbh_blockhash_peers[data['headerhash']] = {'blocknumber': data['blocknumber'],
                                                                             'peers': []}
            self.factory.pos.fmbh_blockhash_peers[data['headerhash']]['peers'].append(self)

    def MB(self):  # we send with just prefix as request..with CB number and blockhash as answer..
        """
        Maximum Blockheight
        Sends maximum blockheight of the mainchain.
        :return:
        """
        logger.info('<<<Sending blockheight to: %s %s', self.transport.getPeer().host, str(time.time()))
        self.send_m_blockheight_to_peer()
        return

    def CB(self, data):
        """
        Check Blockheight
        :return:
        """
        z = helper.json_decode(data)
        block_number = z['block_number']
        headerhash = z['headerhash'].encode('latin1')

        self.blockheight = block_number

        logger.info('>>>Blockheight from: %s blockheight: %s local blockheight: %s %s',
                    self.transport.getPeer().host, block_number,
                    self.factory.chain.m_blockheight(), str(time.time()))

        self.factory.peers_blockheight[self.transport.getPeer().host + ':' + str(self.transport.getPeer().port)] = z[
            'block_number']

        if self.factory.nodeState.state == NState.syncing: return

        if block_number == self.factory.chain.m_blockheight():
            # if self.factory.chain.m_blockchain[block_number].blockheader.headerhash != headerhash:
            if self.factory.chain.m_get_block(block_number).blockheader.headerhash != headerhash:
                logger.info('>>> WARNING: headerhash mismatch from %s', self.transport.getPeer().host)

                # initiate fork recovery and protection code here..
                # call an outer function which sets a flag and scrutinises the chains from all connected hosts to see what is going on..
                # again need to think this one through in detail..

                return

        if block_number > self.factory.chain.m_blockheight():
            return

        if len(self.factory.chain.m_blockchain) == 1 and self.factory.genesis == 0:
            # set the flag so that no other Protocol instances trigger the genesis stake functions..
            self.factory.genesis = 1
            logger.info('genesis pos countdown to block 1 begun, 60s until stake tx circulated..')
            reactor.callLater(1, self.factory.pos.pre_pos_1)
            return

        # connected to multiple hosts and already passed through..
        elif len(self.factory.chain.m_blockchain) == 1 and self.factory.genesis == 1:
            return

    def BN(self, data):  # request for block (n)
        """Block(n)
        Sends the nth block from mainchain.
        :return:
        """
        if int(data) <= self.factory.chain.m_blockheight():
            logger.info('<<<Sending block number %s %s bytes to node: %s', int(data),
                        len(helper.json_bytestream(self.factory.chain.m_get_block(int(data)))),
                        self.transport.getPeer().host)
            self.transport.write(
                self.wrap_message('BK', helper.json_bytestream_bk(self.factory.chain.m_get_block(int(data)))))
        else:
            if int(data) >= self.factory.chain.m_blockheight():
                logger.info('BN for a blockheight greater than local chain length..')
            else:
                logger.info('BN request without valid block number %s - closing connection', str(data))
                self.transport.loseConnection()
        return

    def FB(self, data):  # Fetch Request for block
        """
        Fetch Block
        Sends the request for the block.
        :return:
        """
        data = int(data)
        logger.info(' Request for %s by %s', data, self.conn_identity)
        if 0 < data <= self.factory.chain.block_chain_buffer.height():
            self.factory.chain.block_chain_buffer.send_block(data, self.transport, self.wrap_message)
        else:
            self.transport.write(self.wrap_message('PB', data))
            if data > self.factory.chain.height():
                logger.info('FB for a blocknumber is greater than the local chain length..')
                return
            logger.info(' Send for blocmnumber #%s to %s', data, self.conn_identity)
        return

    def FH(self, data):  # Fetch Block Headerhash
        """
        Fetch Block Headerhash
        Sends the request for the blockheaderhash of a given blocknumber.
        :return:
        """
        data = int(data)
        if 0 < data <= self.factory.chain.height():
            mini_block = {}
            logger.info('<<<Pushing block headerhash of block number %s to node: %s',
                        str(data), self.transport.getPeer().host)
            mini_block['headerhash'] = self.factory.chain.m_get_block(data).blockheader.headerhash
            mini_block['blocknumber'] = data
            self.transport.write(self.wrap_message('PH', helper.json_bytestream_ph(mini_block)))
        else:
            if data > self.factory.chain.height():
                logger.info('FH for a blocknumber is greater than the local chain length..')
        return

    def PO(self, data):
        """
        Pong
        :return:
        """
        if data[0:2] == 'NG':
            y = 0
            for entry in self.factory.chain.ping_list:
                if entry['node'] == self.transport.getPeer().host:
                    entry['ping (ms)'] = (time.time() - self.chain.last_ping) * 1000
                    y = 1
            if y == 0:
                self.factory.chain.ping_list.append({'node': self.transport.getPeer().host,
                                                     'ping (ms)': (time.time() - self.factory.chain.last_ping) * 1000})

    def PI(self, data):
        """
        Ping
        :return:
        """
        if data[0:2] == 'NG':
            self.transport.write(self.wrap_message('PONG'))
        else:
            self.transport.loseConnection()
        return

    def PL(self, data):  # receiving a list of peers to save into peer list..
        """
        Peers List
        :return:
        """
        self.recv_peers(data)

    def RT(self):
        """
        Transaction_pool to peer
        :return:
        """
        for t in self.factory.chain.transaction_pool:
            self.f.send_tx_to_peers(t)
        return

    def PE(self):  # get a list of connected peers..need to add some ddos and type checking proteection here..
        """
        Peers
        Sends the list of all connected peers.
        :return:
        """
        self.get_peers()
        return

    def VE(self, data=None):
        """
        Version
        If data is None then sends the version & genesis_prev_headerhash.
        Otherwise, process the content of data and incase of non matching,
        genesis_prev_headerhash, it disconnects the odd peer.
        :return:
        """
        if not data:
            version_details = {
                'version': config.dev.version_number,
                'genesis_prev_headerhash': config.dev.genesis_prev_headerhash
            }
            self.transport.write(self.wrap_message('VE', helper.json_encode(version_details)))
        else:
            try:
                data = helper.json_decode(data)
                self.version = str(data['version'])
                logger.info('%s version: %s | genesis prev_headerhash %s',
                            self.transport.getPeer().host,
                            data['version'],
                            data['genesis_prev_headerhash'])

                if data['genesis_prev_headerhash'] == config.dev.genesis_prev_headerhash:
                    return
                logger.warning('%s genesis_prev_headerhash mismatch', self.conn_identity)
                logger.warning('Expected: %s', config.dev.genesis_prev_headerhash)
                logger.warning('Found: %s', data['genesis_prev_headerhash'])
            except Exception as e:
                logger.error('Peer Caused Exception %s', self.conn_identity)
                logger.exception(e)

            self.transport.loseConnection()

        return

    # receive a reveal_one message sent out after block receipt or creation (could be here prior to the block!)
    def R1(self, data):
        """
        Reveal
        Process the reveal message received by the peer.
        :return:
        """
        if self.factory.nodeState.state != NState.synced:
            return
        z = json.loads(data, parse_float=Decimal)
        if not z:
            return
        block_number = z['block_number']
        headerhash = z['headerhash'].encode('latin1')
        stake_address = z['stake_address'].encode('latin1')
        vote_hash = z['vote_hash'].encode('latin1')
        reveal_one = z['reveal_one'].encode('latin1')

        if not self.factory.master_mr.isRequested(z['vote_hash'], self):
            return

        if block_number <= self.factory.chain.height():
            return

        for entry in self.factory.chain.stake_reveal_one:  # already received, do not relay.
            if entry[3] == reveal_one:
                return

        if len(self.factory.chain.stake_validator_latency) > 20:
            del self.factory.chain.stake_validator_latency[min(self.factory.chain.stake_validator_latency.keys())]

        if self.factory.nodeState.epoch_diff == 0:
            sv_list = self.factory.chain.block_chain_buffer.stake_list_get(z['block_number'])

            if stake_address not in sv_list:
                logger.info('stake address not in the stake_list')
                logger.info('len of sv_list %s', len(sv_list))
                logger.info('len of next_sv_list %s',
                            len(self.factory.chain.block_chain_buffer.next_stake_list_get(z['block_number'])))
                return

            stake_validators_list = self.factory.chain.block_chain_buffer.get_stake_validators_list(block_number)

            target_chain = stake_validators_list.select_target(headerhash)
            if not stake_validators_list.validate_hash(vote_hash,
                                                       block_number,
                                                       target_chain=target_chain,
                                                       stake_address=stake_address):
                logger.info('%s vote hash doesnt hash to stake terminator vote %s',  # nonce %s vote_hash %s',
                            self.conn_identity, vote_hash)  # , s[2], vote_hash_terminator)
                return

            if not stake_validators_list.validate_hash(reveal_one,
                                                       block_number,
                                                       target_chain=config.dev.hashchain_nums - 1,
                                                       stake_address=stake_address):
                logger.info('%s reveal doesnt hash to stake terminator reveal %s',  # nonce %s reveal_hash %s',
                            self.conn_identity, reveal_one)  # , s[2], reveal_hash_terminator)
                return

        if len(self.factory.pos.r1_time_diff) > 2:
            del self.factory.pos.r1_time_diff[min(self.factory.pos.r1_time_diff.keys())]

        self.factory.pos.r1_time_diff[block_number].append(int(time.time() * 1000))

        logger.info('>>> POS reveal_one: %s %s %s %s', self.transport.getPeer().host, stake_address, block_number,
                    reveal_one)
        score = self.factory.chain.score(stake_address=stake_address,
                                         reveal_one=reveal_one,
                                         balance=self.factory.chain.block_chain_buffer.get_st_balance(stake_address,
                                                                                                      block_number),
                                         seed=z['seed'])

        if score is None:
            logger.info('Score None for stake_address %s reveal_one %s', stake_address, reveal_one)
            return

        if score != z['weighted_hash']:
            logger.info('Weighted_hash didnt match')
            logger.info('Expected : %s', score)
            logger.info('Found : %s', z['weighted_hash'])
            logger.info('Seed found : %ld', z['seed'])
            logger.info('Seed Expected : %ld',
                        self.factory.chain.block_chain_buffer.get_epoch_seed(z['block_number']))
            logger.info('Balance : %ld',
                        self.factory.chain.block_chain_buffer.get_st_balance(stake_address, block_number))

            return

        epoch = block_number // config.dev.blocks_per_epoch
        epoch_seed = self.factory.chain.block_chain_buffer.get_epoch_seed(z['block_number'])

        if epoch_seed != z['seed']:
            logger.info('Seed didnt match')
            logger.info('Expected : %ld', epoch_seed)
            logger.info('Found : %ld', z['seed'])
            return

        sv_hash = self.factory.chain.get_stake_validators_hash()
        # if sv_hash != z['SV_hash']:
        # logger.info(( 'SV_hash didnt match' ))
        # logger.info(( 'Expected : ', sv_hash ))
        # logger.info(( 'Found : ', z['SV_hash'] ))
        # return

        self.factory.chain.stake_reveal_one.append(
            [stake_address, headerhash, block_number, reveal_one, score, vote_hash])
        self.factory.master_mr.register(z['vote_hash'], data, 'R1')
        if self.factory.nodeState.state == NState.synced:
            self.broadcast(z['vote_hash'], 'R1')

        return

    def IP(self, data):  # fun feature to allow geo-tagging on qrl explorer of test nodes..reveals IP so optional..
        """
        IP
        If data is None, node sends its own IP.
        Otherwise, append the received IP into ip_list
        and also broadcast it to all other connected peers
        :return:
        """
        if not data:
            if self.factory.ip_geotag == 1:
                for peer in self.factory.peers:
                    if peer != self:
                        peer.transport.write(self.wrap_message('IP', self.transport.getHost().host))
        else:
            if data not in self.factory.chain.ip_list:
                self.factory.chain.ip_list.append(data)
                for peer in self.factory.peers:
                    if peer != self:
                        peer.transport.write(self.wrap_message('IP', self.transport.getHost().host))

        return

    def recv_peers(self, json_data):
        """
        Receive Peers
        Received peers list is saved.
        :return:
        """
        if not config.user.enable_peer_discovery:
            return
        data = helper.json_decode(json_data)
        new_ips = []
        for ip in data:
            if ip not in new_ips:
                new_ips.append(ip.encode('latin1'))

        peer_addresses = self.factory.peer_addresses
        logger.info('%s peers data received: %s', self.transport.getPeer().host, new_ips)
        for node in new_ips:
            if node not in peer_addresses:
                if node != self.transport.getHost().host:
                    peer_addresses.append(node)
                    reactor.connectTCP(node, 9000, self.factory)

        self.factory.update_peer_addresses(peer_addresses)
        return

    def get_latest_block_from_connection(self):
        """
        Get Latest Block
        Sends the request for the last block in the mainchain.
        :return:
        """
        logger.info('<<<Requested last block from %s', self.transport.getPeer().host)
        self.transport.write(self.wrap_message('LB'))
        return

    def get_m_blockheight_from_connection(self):
        """
        Get blockheight
        Sends the request to all peers to send their mainchain max blockheight.
        :return:
        """
        logger.info('<<<Requesting blockheight from %s', self.transport.getPeer().host)
        msg = self.wrap_message('MB')
        self.transport.write(msg)
        return

    def send_m_blockheight_to_peer(self):
        """
        Send mainchain blockheight to peer
        Sends the mainchain maximum blockheight request.
        :return:
        """
        z = {'headerhash': self.factory.chain.m_blockchain[-1].blockheader.headerhash,
             'block_number': 0}

        if len(self.factory.chain.m_blockchain):
            z['block_number'] = self.factory.chain.m_blockchain[-1].blockheader.blocknumber
        self.transport.write(self.wrap_message('CB', helper.json_encode(z)))
        return

    def get_version(self):
        """
        Get Version
        Sends request for the version.
        :return:
        """
        logger.info('<<<Getting version %s', self.transport.getPeer().host)
        self.transport.write(self.wrap_message('VE'))
        return

    def get_peers(self):
        """
        Get Peers
        Sends the peers list.
        :return:
        """
        logger.info('<<<Sending connected peers to %s', self.transport.getPeer().host)
        peers_list = []
        for peer in self.factory.peer_connections:
            peers_list.append(peer.transport.getPeer().host)
        self.transport.write(self.wrap_message('PL', helper.json_encode(peers_list)))
        return

    def get_block_n(self, n):
        """
        Get Block n
        Sends request for the block number n.
        :return:
        """
        logger.info('<<<Requested block: %s from %s', n, self.transport.getPeer().host)
        self.transport.write(self.wrap_message('BN', str(n)))
        return

    def fetch_block_n(self, n):
        """
        Fetch Block n
        Sends request for the block number n.
        :return:
        """
        if self.last_requested_blocknum != n:
            self.fetch_tried = 0
        self.fetch_tried += 1  # TODO: remove from target_peers if tried is greater than x
        self.last_requested_blocknum = n
        logger.info('<<<Fetching block: %s from %s', n, self.conn_identity)
        self.transport.write(self.wrap_message('FB', str(n)))
        return

    def fetch_FMBH(self):
        """
        Fetch Maximum Block Height
        Sends request for the maximum blockheight.
        :return:
        """
        logger.info('<<<Fetching FMBH from : %s', self.conn_identity)
        self.transport.write(self.wrap_message('FMBH'))

    def fetch_headerhash_n(self, n):
        """
        Fetch Headerhash n
        Sends request for the headerhash of blocknumber n.
        :return:
        """
        logger.info('<<<Fetching headerhash of block: %s from %s', n, self.conn_identity)
        self.transport.write(self.wrap_message('FH', str(n)))
        return

    MSG_INITIATOR = bytearray(b'\xff\x00\x00')
    MSG_TERMINATOR = bytearray(b'\x00\x00\xff')

    @staticmethod
    def wrap_message(mtype, data=None):
        """
        :param mtype:
        :type mtype: str
        :param data:
        :type data: Union[None, str, None, None, None, None, None, None, None, None, None]
        :return:
        :rtype: str
        >>> from qrl.core.doctest_data import wrap_message_expected1, wrap_message_expected1b
        >>> answer = bin2hstr(P2PProtocol.wrap_message('TESTKEY_1234', 12345))
        >>> answer == 'ff00003030303030303237007b2264617461223a2031323334352c202274797065223a2022544553544b45595f31323334227d0000ff' or answer == 'ff00003030303030303237007b2274797065223a2022544553544b45595f31323334222c202264617461223a2031323334357d0000ff'
        True
        """
        # FIXME: Move this to protobuf
        jdata = {'type': mtype}
        if data:
            jdata['data'] = data

        str_data = json.dumps(jdata)

        # FIXME: struct.pack may result in endianness problems
        str_data_len = bin2hstr(struct.pack('>L', len(str_data)))

        tmp = b''
        tmp += P2PProtocol.MSG_INITIATOR
        tmp += str_data_len.encode()
        tmp += bytearray(b'\x00')
        tmp += str_data.encode()
        tmp += P2PProtocol.MSG_TERMINATOR

        return tmp

    def clean_buffer(self, reason=None, upto=None):
        if reason:
            logger.info('%s', reason)
        if upto:
            self.buffer = self.buffer[upto:]  # Clean buffer till the value provided in upto
        else:
            self.buffer = b''  # Clean buffer completely

    def parse_buffer(self):
        """
        :return:
        :rtype: bool
        >>> from qrl.core.doctest_data import wrap_message_expected1
        >>> p=P2PProtocol()
        >>> p.buffer = wrap_message_expected1
        >>> found_message = p.parse_buffer()
        >>> p.messages
        [bytearray(b'{"data": 12345, "type": "TESTKEY_1234"}')]
        """
        # FIXME
        if len(self.buffer) == 0:
            return False

        d = self.buffer.find(P2PProtocol.MSG_INITIATOR)  # find the initiator sequence
        num_d = self.buffer.count(P2PProtocol.MSG_INITIATOR)  # count the initiator sequences

        if d == -1:  # if no initiator sequences found then wipe buffer..
            self.clean_buffer(reason='Message data without initiator')
            return False

        self.buffer = self.buffer[d:]  # delete data up to initiator

        if len(self.buffer) < 8:  # Buffer is still incomplete as it doesn't have message size
            return False

        try:
            tmp = self.buffer[3:11]
            tmp2 = hstr2bin(tmp.decode())
            tmp3 = bytearray(tmp2)
            m = struct.unpack('>L', tmp3)[0]  # is m length encoded correctly?
        except:
            if num_d > 1:  # if not, is this the only initiator in the buffer?
                self.buffer = self.buffer[3:]
                d = self.buffer.find(P2PProtocol.MSG_INITIATOR)
                self.clean_buffer(reason='Struct.unpack error attempting to decipher msg length, next msg preserved',
                                  upto=d)  # no
                return True
            else:
                self.clean_buffer(reason='Struct.unpack error attempting to decipher msg length..')  # yes
            return False

        if m > config.dev.message_buffer_size:  # check if size is more than 500 KB
            if num_d > 1:
                self.buffer = self.buffer[3:]
                d = self.buffer.find(P2PProtocol.MSG_INITIATOR)
                self.clean_buffer(reason='Size is more than 500 KB, next msg preserved', upto=d)
                return True
            else:
                self.clean_buffer(reason='Size is more than 500 KB')
            return False

        e = self.buffer.find(P2PProtocol.MSG_TERMINATOR)  # find the terminator sequence

        if e == -1:  # no terminator sequence found
            if len(self.buffer) > 12 + m + 3:
                if num_d > 1:  # if not is this the only initiator sequence?
                    self.buffer = self.buffer[3:]
                    d = self.buffer.find(P2PProtocol.MSG_INITIATOR)
                    self.clean_buffer(reason='Message without appropriate terminator, next msg preserved', upto=d)  # no
                    return True
                else:
                    self.clean_buffer(reason='Message without initiator and terminator')  # yes
            return False

        if e != 3 + 9 + m:  # is terminator sequence located correctly?
            if num_d > 1:  # if not is this the only initiator sequence?
                self.buffer = self.buffer[3:]
                d = self.buffer.find(P2PProtocol.MSG_INITIATOR)
                self.clean_buffer(reason='Message terminator incorrectly positioned, next msg preserved', upto=d)  # no
                return True
            else:
                self.clean_buffer(reason='Message terminator incorrectly positioned')  # yes
            return False

        self.messages.append(self.buffer[12:12 + m])  # if survived the above then save the msg into the self.messages
        self.buffer = self.buffer[8 + m + 3:]  # reset the buffer to after the msg
        return True

    def dataReceived(self, data):  # adds data received to buffer. then tries to parse the buffer twice..
        """
        :param data:Message data without initiator
        :type data: str
        :return:
        :rtype: None
        >>> from qrl.core.doctest_data import wrap_message_expected1
        >>> p=P2PProtocol()
        >>> val_received = 0
        >>> def mockService(x):
        ...     global val_received
        ...     val_received = x
        >>> p.service['TESTKEY_1234'] = mockService
        >>> p.dataReceived(wrap_message_expected1)
        >>> val_received
        12345
        """

        self.buffer += data

        for x in range(50):
            if not self.parse_buffer():
                break
            else:
                for msg in self.messages:
                    self.parse_msg(msg)
                del self.messages[:]

    def connectionMade(self):
        peerHost, peerPort = self.transport.getPeer().host, self.transport.getPeer().port
        self.conn_identity = peerHost + ":" + str(peerPort)

        # FIXME: (For AWS) This could be problematic for other users
        if config.dev.public_ip:
            if self.transport.getPeer().host == config.dev.public_ip:
                self.transport.loseConnection()
                return

        if len(self.factory.peer_connections) >= config.user.max_peers_limit:
            # FIXME: Should we stop listening to avoid unnecessary load due to many connections?
            logger.info('Peer limit hit ')
            logger.info('# of Connected peers %s', len(self.factory.peer_connections))
            logger.info('Peer Limit %s', config.user.max_peers_limit)
            logger.info('Disconnecting client %s', self.conn_identity)
            self.transport.loseConnection()
            return

        self.factory.connections += 1
        self.factory.peer_connections.append(self)
        peer_list = self.factory.peer_addresses
        if self.transport.getPeer().host == self.transport.getHost().host:
            if self.transport.getPeer().host in peer_list:
                logger.info('Self in peer_list, removing..')
                peer_list.remove(self.transport.getPeer().host)
                self.factory.update_peer_addresses(peer_list)
            self.transport.loseConnection()
            return

        if self.transport.getPeer().host not in peer_list:
            logger.info('Adding to peer_list')
            peer_list.append(self.transport.getPeer().host)
            self.factory.update_peer_addresses(peer_list)

        logger.info('>>> new peer connection : %s:%s ',
                    self.transport.getPeer().host,
                    str(self.transport.getPeer().port))

        self.get_m_blockheight_from_connection()
        self.get_peers()
        self.get_version()

    # here goes the code for handshake..using functions within the p2pprotocol class
    # should ask for latest block/block number.

    def connectionLost(self, reason=connectionDone):
        logger.info('%s disconnected. remainder connected: %s',
                    self.transport.getPeer().host,
                    str(self.factory.connections))  # , reason
        try:
            self.factory.peer_connections.remove(self)
            self.factory.connections -= 1

            if self.conn_identity in self.factory.target_peers:
                del self.factory.target_peers[self.conn_identity]
            host_port = self.transport.getPeer().host + ':' + str(self.transport.getPeer().port)
            if host_port in self.factory.peers_blockheight:
                del self.factory.peers_blockheight[host_port]
            if self.factory.connections == 0:
                reactor.callLater(60, self.factory.connect_peers)
        except Exception:
            pass

    def recv_tx(self, json_tx_obj):

        try:
            tx = SimpleTransaction().json_to_transaction(json_tx_obj)
        except Exception as e:
            logger.info('tx rejected - unable to decode serialised data - closing connection')
            logger.exception(e)
            self.transport.loseConnection()
            return

        if not self.factory.master_mr.isRequested(tx.get_message_hash(), self):
            return

        if tx.txhash in self.factory.chain.prev_txpool or tx.txhash in self.factory.chain.pending_tx_pool_hash:
            return

        del self.factory.chain.prev_txpool[0]
        self.factory.chain.prev_txpool.append(tx.txhash)

        for t in self.factory.chain.transaction_pool:  # duplicate tx already received, would mess up nonce..
            if tx.txhash == t.txhash:
                return

        self.factory.chain.update_pending_tx_pool(tx, self)

        self.factory.master_mr.register(tx.get_message_hash(), json_tx_obj, 'TX')
        self.broadcast(tx.get_message_hash(), 'TX')

        if not self.factory.txn_processor_running:
            txn_processor = TxnProcessor(block_chain_buffer=self.factory.chain.block_chain_buffer,
                                         pending_tx_pool=self.factory.chain.pending_tx_pool,
                                         transaction_pool=self.factory.chain.transaction_pool,
                                         txhash_timestamp=self.factory.chain.txhash_timestamp)

            task_defer = TxnProcessor.create_cooperate(txn_processor).whenDone()
            task_defer.addCallback(self.factory.reset_processor_flag) \
                .addErrback(self.factory.reset_processor_flag_with_err)
            self.factory.txn_processor_running = True
        return
